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

# Dedicated port used only for LAN discovery broadcasts
DISCOVERY_PORT    = 19099          # UDP broadcast port (separate from P2P)
# Broadcast address reaches all devices on the local subnet
BROADCAST_ADDR    = "255.255.255.255"
# Signature prefix to distinguish Antigravity packets from unrelated UDP broadcasts
MAGIC             = b"ANTIGRAVITY"  # so we ignore unrelated broadcast traffic
# Frequency (in seconds) for repeating discovery announcements
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
        # Local node IP address being advertised
        self._host         = my_host
        # Local node P2P port being advertised
        self._port         = my_port
        # Callback triggered when a new peer is discovered
        self._on_peer      = on_peer_found
        # Tracks already discovered peers to prevent duplicates
        self._seen: set    = set()
        # Running state for both background loops
        self._running      = False
        # Shared UDP socket for sending + receiving broadcasts
        self._sock: Optional[socket.socket] = None

    # ── Public API ──────────────────────────────────────────────────────

    def start(self) -> None:
        # Enable discovery system
        self._running = True
        # Create and configure UDP broadcast socket
        self._sock = self._make_socket()

        # Thread 1: listen for announcements from other nodes
        threading.Thread(target=self._listen_loop, daemon=True).start()
        # Thread 2: periodically announce ourselves
        threading.Thread(target=self._announce_loop, daemon=True).start()

        logger.info(f"[DISCOVERY] Started — broadcasting on port {DISCOVERY_PORT}")

    def stop(self) -> None:
        # Gracefully shut down discovery loops
        self._running = False
        if self._sock:
            try:
                # Close UDP socket safely
                self._sock.close()
            except Exception:
                pass

    def get_discovered_peers(self) -> List[Tuple[str, int]]:
        # Returns all unique peers found so far
        return list(self._seen)

    # ── Internal ────────────────────────────────────────────────────────

    def _make_socket(self) -> socket.socket:
        # Create UDP IPv4 socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Allow address reuse for quick restarts
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Enable broadcast
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            # SO_REUSEPORT lets multiple processes share the port (Linux/macOS)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except AttributeError:
            # Windows may not support SO_REUSEPORT
            pass  # Not available on Windows — SO_REUSEADDR is enough
        # Listen on all interfaces for discovery traffic
        s.bind(("", DISCOVERY_PORT))
        # Prevent blocking forever while listening
        s.settimeout(1.0)
        return s

    def _announce(self) -> None:
        """Broadcast our address to the whole LAN."""
        # Packet format: MAGIC|ANNOUNCE|ip:port
        msg = MAGIC + b"|ANNOUNCE|" + f"{self._host}:{self._port}".encode()
        try:
            # Broadcast to every node on local network
            self._sock.sendto(msg, (BROADCAST_ADDR, DISCOVERY_PORT))
        except Exception as e:
            logger.debug(f"[DISCOVERY] Broadcast error: {e}")

    def _announce_loop(self) -> None:
        # Small initial delay so the listen socket is ready
        time.sleep(0.3)
        while self._running:
            # Periodically advertise this node
            self._announce()
            time.sleep(ANNOUNCE_INTERVAL)


    def _listen_loop(self) -> None:
        # Continuously listen for LAN discovery packets
        while self._running:
            try:
                # Receive incoming UDP packet
                data, addr = self._sock.recvfrom(256)
            except socket.timeout:
                # Retry loop after timeout
                continue
            except Exception:
                # Exit if socket is closed or unrecoverable error occurs
                break

            # Ignore non-Antigravity traffic
            if not data.startswith(MAGIC):
                continue

            try:
                # Parse MAGIC|TYPE|PAYLOAD
                _, msg_type, payload = data.split(b"|", 2)
            except ValueError:
                # Ignore malformed packets
                continue

            if msg_type == b"ANNOUNCE":
                # Extract advertised peer address
                peer_str = payload.decode()
                try:
                    peer_host, peer_port_s = peer_str.rsplit(":", 1)
                    peer_port = int(peer_port_s)
                except ValueError:
                    # Ignore malformed host:port payload
                    continue

                # Ignore our own announcements
                if peer_host == self._host and peer_port == self._port:
                    continue

                key = (peer_host, peer_port)
                # Only process newly discovered peers
                if key not in self._seen:
                    self._seen.add(key)
                    logger.info(
                        f"[DISCOVERY] 🔍 Found peer: {peer_host}:{peer_port}"
                    )
                    # Notify external node manager / bootstrap system
                    self._on_peer(peer_host, peer_port)