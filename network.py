"""
network.py — Project Antigravity
Asynchronous UDP transport: send, receive, PING/PONG handshake,
FIND_NODE/NEIGHBORS lookup, and bootstrap procedure.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Dict, List, Optional, Tuple

from crypto import ANR, NodeKeypair
from protocols import (
    MsgType,
    PingMessage,
    PongMessage,
    FindNodeMessage,
    NeighborsMessage,
    decode_message,
    encode_message,
)
from routing import K, RoutingTable

logger = logging.getLogger("antigravity.network")


# ---------------------------------------------------------------------------
# Event system — GUI can subscribe to log events
# ---------------------------------------------------------------------------

class EventBus:
    """Simple synchronous pub/sub for UI ↔ network decoupling."""

    def __init__(self):
        self._subscribers: List[Callable[[str, dict], None]] = []

    def subscribe(self, fn: Callable[[str, dict], None]) -> None:
        self._subscribers.append(fn)

    def publish(self, event: str, payload: dict = {}) -> None:
        for fn in self._subscribers:
            try:
                fn(event, payload)
            except Exception:
                pass   # never let a UI bug crash the network layer


event_bus = EventBus()


def _log(event: str, msg: str, **kw) -> None:
    logger.info(msg)
    event_bus.publish(event, {"message": msg, **kw})


# ---------------------------------------------------------------------------
# UDP Protocol (asyncio DatagramProtocol)
# ---------------------------------------------------------------------------

class _AntigravityProtocol(asyncio.DatagramProtocol):
    """Low-level asyncio UDP transport handler."""

    def __init__(self, node: "AntigravityNode"):
        self._node = node
        self.transport: Optional[asyncio.DatagramTransport] = None

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr: Tuple[str, int]) -> None:
        asyncio.ensure_future(self._node._handle_datagram(data, addr))

    def error_received(self, exc: Exception) -> None:
        logger.error(f"UDP error: {exc}")

    def connection_lost(self, exc: Optional[Exception]) -> None:
        logger.warning(f"UDP connection lost: {exc}")


# ---------------------------------------------------------------------------
# Main node class
# ---------------------------------------------------------------------------

class AntigravityNode:
    """
    Full Antigravity P2P node.

    Responsibilities
    ----------------
    * Maintain a UDP socket (asyncio DatagramProtocol)
    * Manage its Kademlia routing table
    * Handle PING / PONG / FIND_NODE / NEIGHBORS
    * Expose bootstrap() for initial peer discovery
    """

    def __init__(
        self,
        keypair: NodeKeypair,
        host: str,
        port: int,
        k: int = K,
    ):
        self.keypair = keypair
        self.host = host
        self.port = port
        self.started_at: float = time.time()

        # Compute advertised IP (don't advertise 0.0.0.0 to peers)
        advertised_ip = host
        if advertised_ip in ("0.0.0.0", "127.0.0.1", ""):
            import socket as _s
            try:
                s = _s.socket(_s.AF_INET, _s.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                advertised_ip = s.getsockname()[0]
                s.close()
            except Exception:
                advertised_ip = "127.0.0.1"

        # Build our own ANR
        self.anr = ANR.create(keypair, advertised_ip, port)

        # Routing table
        self.routing_table = RoutingTable(self.anr, k=k)

        # Transport / protocol (set in start())
        self._transport: Optional[asyncio.DatagramTransport] = None
        self._protocol: Optional[_AntigravityProtocol] = None

        # Pending PING futures: msg_id → Future[PongMessage]
        self._pending_pings: Dict[str, asyncio.Future] = {}

        # Pending FIND_NODE futures: msg_id → Future[NeighborsMessage]
        self._pending_finds: Dict[str, asyncio.Future] = {}

        self._running = False

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def start(self) -> None:
        loop = asyncio.get_event_loop()
        self._protocol = _AntigravityProtocol(self)
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: self._protocol,
            local_addr=(self.host, self.port),
        )
        self._running = True
        _log("node_started", f"[NODE] Started on {self.host}:{self.port} — ID: {self.anr.short_id(16)}…")

    async def stop(self) -> None:
        self._running = False
        if self._transport:
            self._transport.close()
        _log("node_stopped", f"[NODE] Stopped.")

    # ------------------------------------------------------------------ #
    # Sending helpers
    # ------------------------------------------------------------------ #




    def _send(self, msg, addr: Tuple[str, int]) -> None:
        if self._transport is None:
            raise RuntimeError("Transport not ready — call start() first")
        try:
            data = encode_message(msg)
            self._transport.sendto(data, addr)
        except Exception as e:
            logger.error(f"Send error: {e}")

    # ------------------------------------------------------------------ #
    # PING / PONG
    # ------------------------------------------------------------------ #

    async def ping(
        self,
        host: str,
        port: int,
        timeout: float = 5.0,
    ) -> Optional[PongMessage]:
        """Send a PING and await PONG.  Returns None on timeout."""
        msg = PingMessage.build(self.anr)
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending_pings[msg.msg_id] = fut

        self._send(msg, (host, port))
        _log("sent_ping", f"[SENT] PING → {host}:{port}  (id={msg.msg_id[:8]})",
             remote_ip=host, remote_port=port, msg_type="PING")

        try:
            pong = await asyncio.wait_for(fut, timeout)
            _log("recv_pong", f"[RECV] PONG ← {host}:{port}  (id={pong.ping_id[:8]})")
            return pong
        except asyncio.TimeoutError:
            _log("ping_timeout", f"[TIMEOUT] No PONG from {host}:{port}")
            return None
        finally:
            self._pending_pings.pop(msg.msg_id, None)

    def _handle_ping(self, msg: PingMessage, addr: Tuple[str, int]) -> None:
        sender_anr = msg.get_sender_anr()
        _log("recv_ping", f"[RECV] PING ← {addr[0]}:{addr[1]}  (id={msg.msg_id[:8]})",
             remote_ip=addr[0], remote_port=addr[1], msg_type="PING",
             remote_id=sender_anr.node_id if sender_anr else "")
        if sender_anr:
            asyncio.ensure_future(self._maybe_add(sender_anr, addr[0]))

        pong = PongMessage.build(msg, self.anr)
        self._send(pong, addr)
        _log("sent_pong", f"[SENT] PONG → {addr[0]}:{addr[1]}  (id={pong.ping_id[:8]})",
             remote_ip=addr[0], remote_port=addr[1], msg_type="PONG")

    def _handle_pong(self, msg: PongMessage, addr: Tuple[str, int]) -> None:
        fut = self._pending_pings.get(msg.ping_id)
        if fut and not fut.done():
            fut.set_result(msg)
        sender_anr = msg.get_sender_anr()
        if sender_anr:
            _log("recv_pong_structured", f"[RECV] PONG ← {addr[0]}:{addr[1]}",
                 remote_ip=addr[0], remote_port=addr[1], msg_type="PONG",
                 remote_id=sender_anr.node_id)
            asyncio.ensure_future(self._maybe_add(sender_anr, addr[0]))

    # ------------------------------------------------------------------ #
    # FIND_NODE / NEIGHBORS
    # ------------------------------------------------------------------ #

    async def find_node(
        self,
        host: str,
        port: int,
        target_id: str,
        timeout: float = 5.0,
    ) -> Optional[NeighborsMessage]:
        """Send FIND_NODE and await NEIGHBORS."""
        msg = FindNodeMessage.build(target_id, self.anr)
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending_finds[msg.msg_id] = fut

        self._send(msg, (host, port))
        _log(
            "sent_find_node",
            f"[SENT] FIND_NODE → {host}:{port}  target={target_id[:8]}…",
        )

        try:
            neighbors = await asyncio.wait_for(fut, timeout)
            _log(
                "recv_neighbors",
                f"[RECV] NEIGHBORS ← {host}:{port}  ({len(neighbors.nodes)} nodes)",
            )
            return neighbors
        except asyncio.TimeoutError:
            _log("find_node_timeout", f"[TIMEOUT] No NEIGHBORS from {host}:{port}")
            return None
        finally:
            self._pending_finds.pop(msg.msg_id, None)

    def _handle_find_node(self, msg: FindNodeMessage, addr: Tuple[str, int]) -> None:
        sender_anr = msg.get_sender_anr()
        _log("recv_find_node",
             f"[RECV] FIND_NODE ← {addr[0]}:{addr[1]}  target={msg.target_id[:8]}…",
             remote_ip=addr[0], remote_port=addr[1], msg_type="FIND_NODE",
             remote_id=sender_anr.node_id if sender_anr else "")
        if sender_anr:
            asyncio.ensure_future(self._maybe_add(sender_anr, addr[0]))

        closest = self.routing_table.find_closest(msg.target_id, count=K)

        # Split into chunks of 3 to stay under the 1280-byte UDP MTU limit
        chunk_size = 3
        chunks = [closest[i:i+chunk_size] for i in range(0, max(len(closest), 1), chunk_size)]
        for chunk in chunks:
            response = NeighborsMessage.build(msg, chunk, self.anr)
            try:
                self._send(response, addr)
            except ValueError:
                pass   # skip oversized chunk
        _log("sent_neighbors",
             f"[SENT] NEIGHBORS → {addr[0]}:{addr[1]}  ({len(closest)} nodes)",
             remote_ip=addr[0], remote_port=addr[1], msg_type="NEIGHBORS")

    def _handle_neighbors(self, msg: NeighborsMessage, addr: Tuple[str, int]) -> None:
        fut = self._pending_finds.get(msg.find_node_id)
        if fut and not fut.done():
            fut.set_result(msg)

        # Add discovered nodes to routing table
        for anr in msg.get_nodes():
            asyncio.ensure_future(self._maybe_add(anr))

    # ------------------------------------------------------------------ #
    # Datagram dispatch
    # ------------------------------------------------------------------ #

    async def _handle_datagram(self, data: bytes, addr: Tuple[str, int]) -> None:
        try:
            msg = decode_message(data)
        except Exception as e:
            logger.warning(f"Malformed datagram from {addr}: {e}")
            return

        handlers = {
            MsgType.PING:       self._handle_ping,
            MsgType.PONG:       self._handle_pong,
            MsgType.FIND_NODE:  self._handle_find_node,
            MsgType.NEIGHBORS:  self._handle_neighbors,
        }
        handler = handlers.get(msg.msg_type)
        if handler:
            result = handler(msg, addr)
            if asyncio.iscoroutine(result):
                await result
        else:
            logger.warning(f"Unhandled msg_type: {msg.msg_type}")

    # ------------------------------------------------------------------ #
    # Routing table integration
    # ------------------------------------------------------------------ #

    async def _maybe_add(self, anr: ANR, real_ip: str = None) -> None:
        """Add *anr* to routing table; PING LRS if bucket is full."""
        if not anr.verify():
            logger.warning(f"Dropping ANR with invalid signature: {anr.short_id()}")
            return

        if real_ip and anr.ip in ("0.0.0.0", "127.0.0.1", ""):
            anr.ip = real_ip

        lrs = await self.routing_table.add(anr)
        if lrs is None:
            # Successfully inserted or refreshed — emit structured event for graph
            _log("peer_connected",
                 f"[ROUTING] Peer added: {anr.short_id(8)} @ {anr.ip}:{anr.udp_port}",
                 node_id=anr.node_id, node_ip=anr.ip, node_port=anr.udp_port)
        if lrs:
            # Bucket full — verify LRS node is still alive
            pong = await self.ping(lrs.ip, lrs.udp_port, timeout=3.0)
            if pong is None:
                await self.routing_table.remove(lrs.node_id)
                await self.routing_table.add(anr)   # retry after eviction

    # ------------------------------------------------------------------ #
    # Bootstrap
    # ------------------------------------------------------------------ #

    async def bootstrap(self, peers: List[Tuple[str, int]]) -> None:
        """
        Kademlia bootstrap procedure:
        1. PING each bootstrap peer to establish contact.
        2. FIND_NODE for our own ID against each responsive peer.
        3. Populate routing table from returned NEIGHBORS.
        """
        _log("bootstrap_start", f"[BOOTSTRAP] Initiating with {len(peers)} seed(s)…")

        responsive = []
        for host, port in peers:
            pong = await self.ping(host, port)
            if pong:
                sender_anr = pong.get_sender_anr()
                if sender_anr:
                    await self._maybe_add(sender_anr, host)
                    responsive.append((host, port))
                    _log("bootstrap_peer_ok", f"[BOOTSTRAP] Seed {host}:{port} is alive ✓")
            else:
                _log("bootstrap_peer_fail", f"[BOOTSTRAP] Seed {host}:{port} did not respond ✗")

        # Self-lookup
        for host, port in responsive:
            neighbors = await self.find_node(host, port, self.anr.node_id)
            if neighbors:
                for anr in neighbors.get_nodes():
                    await self._maybe_add(anr)

        total = self.routing_table.total_nodes
        _log("bootstrap_done", f"[BOOTSTRAP] Complete — {total} node(s) in routing table.")

    # ------------------------------------------------------------------ #
    # Properties
    # ------------------------------------------------------------------ #

    @property
    def uptime_seconds(self) -> float:
        return time.time() - self.started_at

    @property
    def node_id_hex(self) -> str:
        return self.anr.node_id

    def __repr__(self) -> str:
        return f"AntigravityNode({self.host}:{self.port}, id={self.anr.short_id()})"
