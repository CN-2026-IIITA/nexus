"""
discovery.py — Project Antigravity
Automatic LAN peer discovery using UDP broadcast.

How it works:
  1. On startup, node broadcasts "I'm here at <ip>:<port>" to the whole LAN
  2. Every other Antigravity node on the same WiFi hears it and responds
  3. The new node bootstraps automatically — no manual IP needed
"""

from __future__ import annotations
import socket
import threading
import time
import logging
from typing import Callable, List, Optional, Tuple

logger = logging.getLogger("antigravity.discovery")

DISCOVERY_PORT    = 19099          # UDP broadcast port (separate from P2P)
BROADCAST_ADDR    = "255.255.255.255"
MAGIC             = b"ANTIGRAVITY"  # so we ignore unrelated broadcast traffic
ANNOUNCE_INTERVAL = 5              # re-announce every 5 seconds


class LanDiscovery:
    """
    Broadcasts this node's address on the LAN and collects peer addresses.

    Usage:
        disc = LanDiscovery(my_host="192.168.1.5", my_port=9000,
                            on_peer_found=lambda host, port: ...)
        disc.start()
        ...
        disc.stop()
    """

    def __init__(
        self,
        my_host: str,
        my_port: int,
        on_peer_found: Callable[[str, int], None],
    ):
        self._host         = my_host
        self._port         = my_port
        self._on_peer      = on_peer_found
        self._seen: set    = set()
        self._running      = False
        self._sock: Optional[socket.socket] = None

    # ── Public API ──────────────────────────────────────────────────────

    def start(self) -> None:
        self._running = True
        self._sock = self._make_socket()

        # Thread 1: listen for announcements from other nodes
        threading.Thread(target=self._listen_loop, daemon=True).start()
        # Thread 2: periodically announce ourselves
        threading.Thread(target=self._announce_loop, daemon=True).start()

        logger.info(f"[DISCOVERY] Started — broadcasting on port {DISCOVERY_PORT}")

    def stop(self) -> None:
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass

    def get_discovered_peers(self) -> List[Tuple[str, int]]:
        return list(self._seen)

    # ── Internal ────────────────────────────────────────────────────────

    def _make_socket(self) -> socket.socket:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Enable broadcast
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            # SO_REUSEPORT lets multiple processes share the port (Linux/macOS)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except AttributeError:
            pass  # Not available on Windows — SO_REUSEADDR is enough
        s.bind(("", DISCOVERY_PORT))
        s.settimeout(1.0)
        return s

        # This code was written by team nexus

    def _announce(self) -> None:
        """Broadcast our address to the whole LAN."""
        msg = MAGIC + b"|ANNOUNCE|" + f"{self._host}:{self._port}".encode()
        try:
            self._sock.sendto(msg, (BROADCAST_ADDR, DISCOVERY_PORT))
        except Exception as e:
            logger.debug(f"[DISCOVERY] Broadcast error: {e}")

    def _announce_loop(self) -> None:
        # Small initial delay so the listen socket is ready
        time.sleep(0.3)
        while self._running:
            self._announce()
            time.sleep(ANNOUNCE_INTERVAL)

    def _listen_loop(self) -> None:
        while self._running:
            try:
                data, addr = self._sock.recvfrom(256)
            except socket.timeout:
                continue
            except Exception:
                break

            if not data.startswith(MAGIC):
                continue

            try:
                _, msg_type, payload = data.split(b"|", 2)
            except ValueError:
                continue

            if msg_type == b"ANNOUNCE":
                peer_str = payload.decode()
                try:
                    peer_host, peer_port_s = peer_str.rsplit(":", 1)
                    peer_port = int(peer_port_s)
                except ValueError:
                    continue

                # Ignore our own announcements
                if peer_host == self._host and peer_port == self._port:
                    continue

                key = (peer_host, peer_port)
                if key not in self._seen:
                    self._seen.add(key)
                    logger.info(
                        f"[DISCOVERY] 🔍 Found peer: {peer_host}:{peer_port}"
                    )
                    self._on_peer(peer_host, peer_port)
