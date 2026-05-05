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
        # Local node IP address that this machine advertises
        self._host         = my_host
        # P2P service port that peers should connect to
        self._port         = my_port
        # Callback function triggered whenever a fresh peer is discovered
        self._on_peer      = on_peer_found
        # Set of already discovered peers to avoid duplicate callbacks
        self._seen: set    = set()
        # Controls whether discovery threads should keep running
        self._running      = False
        # Shared UDP socket used for both sending announcements and receiving them
        self._sock: Optional[socket.socket] = None

    # ── Public API ──────────────────────────────────────────────────────

    def start(self) -> None:
        # Mark discovery service as active
        self._running = True
        # Build and configure the UDP broadcast socket
        self._sock = self._make_socket()

        # Background listener thread handles incoming discovery packets
        threading.Thread(target=self._listen_loop, daemon=True).start()
        # Background announcer thread repeatedly broadcasts this node
        threading.Thread(target=self._announce_loop, daemon=True).start()

        logger.info(f"[DISCOVERY] Started — broadcasting on port {DISCOVERY_PORT}")

    def stop(self) -> None:
        # Signal both loops to terminate
        self._running = False
        if self._sock:
            try:
                # Safely close socket to release port and stop recvfrom
                self._sock.close()
            except Exception:
                # Ignore cleanup failures during shutdown
                pass

    def get_discovered_peers(self) -> List[Tuple[str, int]]:
        # Return snapshot of all peers found so far
        return list(self._seen)

    # ── Internal ────────────────────────────────────────────────────────

    def _make_socket(self) -> socket.socket:
        # Create IPv4 UDP socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Allow rebinding quickly after restart
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Permit sending to LAN broadcast address
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            # Allows multiple listeners on same discovery port when supported
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except AttributeError:
            # Windows fallback path
            pass  # Not available on Windows — SO_REUSEADDR is enough
        # Bind to all network interfaces on discovery port
        s.bind(("", DISCOVERY_PORT))
        # Timeout ensures loop can periodically check running state
        s.settimeout(1.0)
        return s

    def _announce(self) -> None:
        """Broadcast our address to the whole LAN."""
        # Discovery message structure:
        # ANTIGRAVITY|ANNOUNCE|<ip>:<port>
        msg = MAGIC + b"|ANNOUNCE|" + f"{self._host}:{self._port}".encode()
        try:
            # Send packet to all LAN devices
            self._sock.sendto(msg, (BROADCAST_ADDR, DISCOVERY_PORT))
        except Exception as e:
            # Broadcast failures are non-fatal; log for debugging
            logger.debug(f"[DISCOVERY] Broadcast error: {e}")

    def _announce_loop(self) -> None:
        # Delay slightly so listener starts first
        time.sleep(0.3)
        while self._running:
            # Broadcast current node presence
            self._announce()
            # Wait before next announcement cycle
            time.sleep(ANNOUNCE_INTERVAL)


    def _listen_loop(self) -> None:
        # Main receive loop for processing incoming peer broadcasts
        while self._running:
            try:
                # Read incoming UDP packet and sender address
                data, addr = self._sock.recvfrom(256)
            except socket.timeout:
                # Normal timeout — continue polling
                continue
            except Exception:
                # Socket likely closed or fatal error
                break

            # Ignore unrelated UDP traffic immediately
            if not data.startswith(MAGIC):
                continue

            try:
                # Expected structure:
                # MAGIC|MESSAGE_TYPE|PAYLOAD
                _, msg_type, payload = data.split(b"|", 2)
            except ValueError:
                # Malformed packet structure
                continue

            if msg_type == b"ANNOUNCE":
                # Decode peer's advertised address
                peer_str = payload.decode()
                try:
                    # Split into host and port
                    peer_host, peer_port_s = peer_str.rsplit(":", 1)
                    peer_port = int(peer_port_s)
                except ValueError:
                    # Invalid formatting
                    continue

                # Ignore self-broadcasts
                if peer_host == self._host and peer_port == self._port:
                    continue

                key = (peer_host, peer_port)
                # Only notify once per unique peer
                if key not in self._seen:
                    self._seen.add(key)
                    logger.info(
                        f"[DISCOVERY] 🔍 Found peer: {peer_host}:{peer_port}"
                    )
                    # Hand off discovered peer to higher-level networking layer
                    self._on_peer(peer_host, peer_port)