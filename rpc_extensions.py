"""
rpc_extensions.py — Project Antigravity
Adds STORE + FIND_VALUE Kademlia RPCs and a TCP chunk transfer layer.

Architecture:
  UDP port   → DHT control (STORE / FIND_VALUE / VALUE_FOUND / VALUE_NODES)
  TCP port+1 → Raw chunk byte transfer

DHTNode subclasses AntigravityNode without modifying it.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

from crypto import ANR, xor_distance_hex
from dht_storage import DHTStorage
from network import AntigravityNode, _log
from routing import K

logger = logging.getLogger("antigravity.dht")
ALPHA = 3   # parallel iterative-lookup concurrency


# ── DHT message types ──────────────────────────────────────────────────────

class DHTMsgType(str, Enum):
    STORE       = "STORE"
    FIND_VALUE  = "FIND_VALUE"
    VALUE_FOUND = "VALUE_FOUND"
    VALUE_NODES = "VALUE_NODES"


# ── Message dataclasses ────────────────────────────────────────────────────

@dataclass
class _DHTBase:
    msg_type: str
    msg_id: str = field(default_factory=lambda: os.urandom(8).hex())
    sender_anr: Optional[dict] = None

    def to_bytes(self) -> bytes:
        return json.dumps(asdict(self), separators=(",", ":")).encode()

    def get_sender_anr(self) -> Optional[ANR]:
        return ANR.from_dict(self.sender_anr) if self.sender_anr else None


@dataclass
class StoreMessage(_DHTBase):
    """Announce: store this key→(small) value at the recipient."""
    msg_type: str = DHTMsgType.STORE
    key: str = ""
    value_b64: str = ""       # base64-encoded bytes (manifest or chunk pointer)

    @classmethod
    def build(cls, key: str, value: bytes, sender_anr: ANR) -> "StoreMessage":
        return cls(key=key,
                   value_b64=base64.b64encode(value).decode(),
                   sender_anr=sender_anr.to_dict())

    def get_value(self) -> bytes:
        return base64.b64decode(self.value_b64)


@dataclass
class FindValueMessage(_DHTBase):
    msg_type: str = DHTMsgType.FIND_VALUE
    key: str = ""

    @classmethod
    def build(cls, key: str, sender_anr: ANR) -> "FindValueMessage":
        return cls(key=key, sender_anr=sender_anr.to_dict())


@dataclass
class ValueFoundMessage(_DHTBase):
    msg_type: str = DHTMsgType.VALUE_FOUND
    find_id: str = ""
    key: str = ""
    value_b64: str = ""

    @classmethod
    def build(cls, req: FindValueMessage, value: bytes,
              sender_anr: ANR) -> "ValueFoundMessage":
        return cls(find_id=req.msg_id,
                   key=req.key,
                   value_b64=base64.b64encode(value).decode(),
                   sender_anr=sender_anr.to_dict())

    def get_value(self) -> bytes:
        return base64.b64decode(self.value_b64)


@dataclass
class ValueNodesMessage(_DHTBase):
    msg_type: str = DHTMsgType.VALUE_NODES
    find_id: str = ""
    key: str = ""
    nodes: List[dict] = field(default_factory=list)

    @classmethod
    def build(cls, req: FindValueMessage, closest: List[ANR],
              sender_anr: ANR) -> "ValueNodesMessage":
        return cls(find_id=req.msg_id,
                   key=req.key,
                   nodes=[a.to_dict() for a in closest[:3]],  # ≤3 to stay under MTU
                   sender_anr=sender_anr.to_dict())

    def get_nodes(self) -> List[ANR]:
        return [ANR.from_dict(d) for d in self.nodes]


# ── Codec ──────────────────────────────────────────────────────────────────

_KLASSES = {
    DHTMsgType.STORE:       StoreMessage,
    DHTMsgType.FIND_VALUE:  FindValueMessage,
    DHTMsgType.VALUE_FOUND: ValueFoundMessage,
    DHTMsgType.VALUE_NODES: ValueNodesMessage,
}

def _decode_dht(data: bytes) -> _DHTBase:
    d = json.loads(data.decode())
    klass = _KLASSES.get(d.get("msg_type", ""))
    if klass is None:
        raise ValueError("not a DHT message")
    valid = set(klass.__dataclass_fields__)
    return klass(**{k: v for k, v in d.items() if k in valid})

def _encode_dht(msg: _DHTBase) -> bytes:
    data = msg.to_bytes()
    if len(data) > 1200:
        raise ValueError(f"DHT msg too large: {len(data)} bytes")
    return data


# ── TCP chunk server ───────────────────────────────────────────────────────

class TCPChunkServer:
    """Serves raw chunk bytes over TCP. Protocol: 'GET <key>\n' → 'FOUND <N>\n<bytes>'"""

    def __init__(self, storage: DHTStorage, host: str, port: int):
        self._storage = storage
        self._host    = host
        self._port    = port
        self._server  = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle, self._host, self._port)
        logger.info(f"[TCP] Chunk server listening on {self._host}:{self._port}")

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, r: asyncio.StreamReader,
                      w: asyncio.StreamWriter) -> None:
        try:
            line = await asyncio.wait_for(r.readline(), timeout=5.0)
            cmd  = line.decode().strip()
            if cmd.startswith("GET "):
                key   = cmd[4:].strip()
                value = self._storage.get(key)
                if value is not None:
                    w.write(f"FOUND {len(value)}\n".encode() + value)
                    logger.debug(f"[TCP] Served {key[:8]}… {len(value)} B")
                else:
                    w.write(b"NOTFOUND\n")
            else:
                w.write(b"ERROR\n")
            await w.drain()
        except Exception as e:
            logger.debug(f"[TCP] Handle error: {e}")
        finally:
            try:
                w.close()
                await w.wait_closed()
            except Exception:
                pass


async def fetch_chunk_tcp(host: str, tcp_port: int, key: str,
                          timeout: float = 30.0) -> Optional[bytes]:
    """Fetch a chunk from a remote TCP chunk server. Returns None on failure."""
    try:
        r, w = await asyncio.wait_for(
            asyncio.open_connection(host, tcp_port), timeout=10.0)
        w.write(f"GET {key}\n".encode())
        await w.drain()
        hdr = (await asyncio.wait_for(r.readline(), timeout=10.0)).decode().strip()
        if hdr.startswith("FOUND "):
            size = int(hdr[6:])
            data = await asyncio.wait_for(r.readexactly(size), timeout=timeout)
            w.close()
            return data
        w.close()
        return None
    except Exception as e:
        logger.debug(f"[TCP] Fetch {host}:{tcp_port} key={key[:8]}: {e}")
        return None


# ── DHTNode ────────────────────────────────────────────────────────────────

class DHTNode(AntigravityNode):
    """
    AntigravityNode + Kademlia STORE / FIND_VALUE RPCs.

    New capabilities
    ----------------
    * dht_store(key, value)  — replicate a value to K closest nodes
    * find_value(key)        — iterative DHT lookup
    * TCP server on port+1   — serves raw chunk bytes
    """

    def __init__(self, *, storage: DHTStorage, **kwargs):
        super().__init__(**kwargs)
        self.storage   = storage
        self.tcp_port  = self.port + 1
        self._tcp_srv  = TCPChunkServer(storage, self.host, self.tcp_port)
        # Pending FIND_VALUE futures: msg_id → Future
        self._pf_value: Dict[str, asyncio.Future] = {}

    def _get_real_ip(self) -> str:
        """Return the best LAN IP for other machines to TCP-connect to us."""
        import socket as _s
        # If bound to a specific non-loopback IP, use it
        if self.host not in ("0.0.0.0", "", "127.0.0.1"):
            return self.host
        # Try routing table peers first — use the IP we reached them from
        try:
            all_peers = self.routing_table.find_closest(self.anr.node_id, count=20)
            for peer in all_peers:
                if peer.ip not in ("0.0.0.0", "127.0.0.1", ""):
                    # Connect UDP toward a known peer to find our outbound IP
                    sock = _s.socket(_s.AF_INET, _s.SOCK_DGRAM)
                    sock.connect((peer.ip, peer.udp_port))
                    ip = sock.getsockname()[0]
                    sock.close()
                    if ip not in ("0.0.0.0", "127.0.0.1"):
                        return ip
        except Exception:
            pass
        # Fallback: route toward 8.8.8.8
        try:
            sock = _s.socket(_s.AF_INET, _s.SOCK_DGRAM)
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
            sock.close()
            if ip not in ("0.0.0.0", "127.0.0.1"):
                return ip
        except Exception:
            pass
        return "127.0.0.1"

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def start(self) -> None:
        await super().start()
        await self._tcp_srv.start()

    async def stop(self) -> None:
        await self._tcp_srv.stop()
        await super().stop()

    # ── Datagram override ─────────────────────────────────────────────────

    async def _handle_datagram(self, data: bytes,
                               addr: Tuple[str, int]) -> None:
        # Try DHT messages first; fall through to base for PING/PONG/FIND_NODE
        try:
            msg = _decode_dht(data)
            handlers = {
                DHTMsgType.STORE:       self._handle_store,
                DHTMsgType.FIND_VALUE:  self._handle_find_value,
                DHTMsgType.VALUE_FOUND: self._handle_value_found,
                DHTMsgType.VALUE_NODES: self._handle_value_nodes,
            }
            h = handlers.get(msg.msg_type)
            if h:
                r = h(msg, addr)
                if asyncio.iscoroutine(r):
                    await r
                return
        except (ValueError, KeyError, json.JSONDecodeError):
            pass
        await super()._handle_datagram(data, addr)

    # ── STORE ─────────────────────────────────────────────────────────────

    async def dht_store(self, key: str, value: bytes,
                        replication: int = 3) -> int:
        """
        Store locally and replicate.
        - Large values (chunks): TCP pointer to K-closest nodes.
        - Small values (manifests): broadcast to ALL known peers so any
          machine on the LAN can find the file key immediately.
        """
        MAX_INLINE = 400

        self.storage.store(key, value)

        if len(value) > MAX_INLINE:
            remote_payload = json.dumps(
                {"tcp_host": self._get_real_ip(),
                 "tcp_port": self.tcp_port}
            ).encode()
            # Replicate pointer to K-closest only
            closest = self.routing_table.find_closest(key, count=K)
            targets = [a for a in closest
                       if a.node_id != self.anr.node_id][:replication]
        else:
            remote_payload = value
            # Broadcast manifest to EVERY known peer — ensures cross-machine lookup
            all_nodes: list = []
            for bucket in self.routing_table._buckets:
                all_nodes.extend(bucket.nodes)
            targets = [a for a in all_nodes if a.node_id != self.anr.node_id]

        stored = 0
        for anr in targets:
            try:
                msg  = StoreMessage.build(key, remote_payload, self.anr)
                data = _encode_dht(msg)
                self._send_raw(data, (anr.ip, anr.udp_port))
                stored += 1
                logger.info(f"[DHT] STORE {key[:8]}… → {anr.ip}:{anr.udp_port}")
            except Exception as e:
                logger.debug(f"[DHT] STORE send error: {e}")
        return stored


    def _handle_store(self, msg: StoreMessage,
                      addr: Tuple[str, int]) -> None:
        value = msg.get_value()
        self.storage.store(msg.key, value)
        logger.info(f"[DHT] Stored {msg.key[:8]}… ({len(value)} B) from {addr[0]}")
        sender = msg.get_sender_anr()
        if sender:
            asyncio.ensure_future(self._maybe_add(sender, addr[0]))

    # ── FIND_VALUE ────────────────────────────────────────────────────────

    async def ask_find_value(
        self, host: str, port: int, key: str, timeout: float = 5.0,
    ) -> "Optional[bytes | List[ANR]]":
        """
        Send FIND_VALUE to (host, port).
        Returns bytes if found, List[ANR] of closer nodes, or None on timeout.
        """
        msg  = FindValueMessage.build(key, self.anr)
        fut  = asyncio.get_event_loop().create_future()
        self._pf_value[msg.msg_id] = fut
        data = _encode_dht(msg)
        self._send_raw(data, (host, port))
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            self._pf_value.pop(msg.msg_id, None)

    async def find_value(self, key: str) -> Optional[bytes]:
        """
        Iterative Kademlia FIND_VALUE lookup.
        Returns the raw value bytes, or None if not found.
        """
        # Check local store first
        local = self.storage.get(key)
        if local is not None:
            return local

        # Seed candidates from routing table
        candidates: List[ANR] = self.routing_table.find_closest(key, count=K)
        queried: set = set()

        while True:
            unqueried = [n for n in candidates if n.node_id not in queried]
            batch     = unqueried[:ALPHA]
            if not batch:
                return None

            tasks = [
                self.ask_find_value(a.ip, a.udp_port, key)
                for a in batch
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for anr, result in zip(batch, results):
                queried.add(anr.node_id)
                if isinstance(result, bytes):
                    self.storage.store(key, result)   # cache locally
                    return result
                if isinstance(result, list):
                    for new_anr in result:
                        if new_anr.node_id not in {n.node_id for n in candidates}:
                            candidates.append(new_anr)
                    candidates.sort(
                        key=lambda a: xor_distance_hex(a.node_id, key))
                    candidates = candidates[:K]

    def _handle_find_value(self, msg: FindValueMessage,
                           addr: Tuple[str, int]) -> None:
        sender = msg.get_sender_anr()
        if sender:
            asyncio.ensure_future(self._maybe_add(sender, addr[0]))
        logger.info(f"[DHT] FIND_VALUE {msg.key[:8]}… from {addr[0]}")

        value = self.storage.get(msg.key)
        if value is not None:
            # Always check if the stored value is itself a pointer (already small)
            # or actual data that is too large to send inline
            MAX_INLINE = 400
            try:
                parsed = json.loads(value.decode())
                is_pointer = "tcp_host" in parsed
            except Exception:
                is_pointer = False

            if is_pointer or len(value) <= MAX_INLINE:
                # Send value inline (it's either small or already a pointer)
                send_val = value
            else:
                # Large actual chunk — convert to a pointer on the fly
                send_val = json.dumps(
                    {"tcp_host": self._get_real_ip(),
                     "tcp_port": self.tcp_port}
                ).encode()

            resp = ValueFoundMessage.build(msg, send_val, self.anr)
            try:
                self._send_raw(_encode_dht(resp), addr)
            except ValueError as e:
                logger.warning(f"[DHT] VALUE_FOUND still too large: {e}")
        else:
            closest = self.routing_table.find_closest(msg.key, count=3)
            resp = ValueNodesMessage.build(msg, closest, self.anr)
            try:
                self._send_raw(_encode_dht(resp), addr)
            except ValueError:
                pass

    def _handle_value_found(self, msg: ValueFoundMessage,
                            addr: Tuple[str, int]) -> None:
        fut = self._pf_value.get(msg.find_id)
        if fut and not fut.done():
            fut.set_result(msg.get_value())

    def _handle_value_nodes(self, msg: ValueNodesMessage,
                            addr: Tuple[str, int]) -> None:
        fut = self._pf_value.get(msg.find_id)
        if fut and not fut.done():
            fut.set_result(msg.get_nodes())
        for anr in msg.get_nodes():
            asyncio.ensure_future(self._maybe_add(anr))

    # ── Internal send helper ──────────────────────────────────────────────

    def _send_raw(self, data: bytes, addr: Tuple[str, int]) -> None:
        """Send pre-encoded bytes directly (bypasses encode_message check)."""
        if self._transport is None:
            raise RuntimeError("Transport not ready")
        self._transport.sendto(data, addr)
