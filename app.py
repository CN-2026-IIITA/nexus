"""
app.py — Project Antigravity
Entry point: spins up the asyncio event loop in a background thread,
starts the AntigravityNode, then launches the PyQt6 GUI on the main thread.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import socket
import sys
import threading

from PyQt6.QtWidgets import QApplication

from crypto import NodeKeypair
from network import AntigravityNode
from gui import MainWindow
from discovery import LanDiscovery
# ── File sharing extensions (additive — no existing code changed) ──────────
from dht_storage import DHTStorage
from rpc_extensions import DHTNode
from file_sharing_gui import FileSharePanel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("antigravity.app")


# ---------------------------------------------------------------------------
# Helper: detect a free UDP port
# ---------------------------------------------------------------------------

def find_free_udp_port(host: str = "0.0.0.0") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Async entrypoint (runs in background thread)
# ---------------------------------------------------------------------------

async def _run_node(node: AntigravityNode) -> None:
    await node.start()
    # Keep the loop alive indefinitely
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        await node.stop()


# ---------------------------------------------------------------------------
# CLI parsing
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Project Antigravity — P2P Node Discovery Protocol"
    )
    p.add_argument(
        "--host", default="0.0.0.0",
        help="IP address to bind the UDP socket (default: 0.0.0.0)"
    )
    p.add_argument(
        "--port", type=int, default=0,
        help="UDP port to listen on (default: auto-select)"
    )
    p.add_argument(
        "--key", default=None,
        help="Path to a persisted private key file (hex). "
             "If omitted a new key is generated each run."
    )
    p.add_argument(
        "--save-key", default=None,
        help="If provided, persist the generated key to this path."
    )
    p.add_argument(
        "--bootstrap", nargs="*", metavar="HOST:PORT",
        help="Bootstrap peer addresses, e.g. --bootstrap 10.0.0.1:9000 10.0.0.2:9000"
    )
    p.add_argument(
        "--no-discovery", action="store_true",
        help="Disable automatic LAN peer discovery (localhost-only testing)"
    )
    return p.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()

    # ── Key pair ──────────────────────────────────────────────────────
    if args.key and os.path.exists(args.key):
        keypair = NodeKeypair.load(args.key)
        logger.info(f"Loaded existing key from {args.key}")
    else:
        keypair = NodeKeypair()
        logger.info("Generated new ephemeral key pair")

    if args.save_key:
        keypair.save(args.save_key)
        logger.info(f"Key saved to {args.save_key}")

    # ── Port ──────────────────────────────────────────────────────────
    port = args.port if args.port else find_free_udp_port(args.host)
    logger.info(f"Using UDP port {port}")

    # ── Node (DHTNode is a drop-in subclass of AntigravityNode) ──────────
    _storage = DHTStorage()
    node = DHTNode(keypair=keypair, host=args.host, port=port,
                   storage=_storage)

    # ── Asyncio loop in a daemon thread ──────────────────────────────
    loop = asyncio.new_event_loop()

    async def _main_task():
        await node.start()

        # ── Manual bootstrap peers (--bootstrap flag) ──────────────────
        manual_peers = []
        if args.bootstrap:
            for entry in args.bootstrap:
                try:
                    h, p_ = entry.rsplit(":", 1)
                    manual_peers.append((h, int(p_)))
                except ValueError:
                    logger.warning(f"Skipping invalid bootstrap addr: {entry}")
            if manual_peers:
                await node.bootstrap(manual_peers)

        # ── Automatic LAN discovery (runs unless --no-discovery given) ──
        # Works with no manual IP entry: nodes find each other on the LAN
        # by broadcasting UDP packets to 255.255.255.255:19099
        if not args.no_discovery:
            # Determine the real LAN IP to broadcast (not 0.0.0.0)
            lan_ip = args.host
            if lan_ip in ("0.0.0.0", "127.0.0.1", ""):
                # Auto-detect the LAN IP
                try:
                    s = __import__("socket").socket(__import__("socket").AF_INET, __import__("socket").SOCK_DGRAM)
                    s.connect(("8.8.8.8", 80))
                    lan_ip = s.getsockname()[0]
                    s.close()
                except Exception:
                    lan_ip = "127.0.0.1"

            def _on_peer_found(peer_host: str, peer_port: int) -> None:
                """Called from the discovery thread when a new LAN peer is seen."""
                asyncio.run_coroutine_threadsafe(
                    node.bootstrap([(peer_host, peer_port)]), loop
                )

            discovery = LanDiscovery(
                my_host=lan_ip,
                my_port=port,
                on_peer_found=_on_peer_found,
            )
            discovery.start()
            logger.info(f"[DISCOVERY] Auto-discovery ON — LAN IP: {lan_ip}:{port}")

        # Stay alive
        while True:
            await asyncio.sleep(3600)

    def _start_loop():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_main_task())

    thread = threading.Thread(target=_start_loop, daemon=True, name="antigravity-net")
    thread.start()

    # ── Qt application (main thread) ──────────────────────────────────
    app = QApplication(sys.argv)
    app.setApplicationName("Project Antigravity")
    app.setApplicationVersion("1.0.0")

    window = MainWindow(loop=loop)
    window.show()

    # Attach node once it is running
    # Give the background thread a moment to bind the socket
    import time
    time.sleep(0.3)
    window.attach_node(node)

    # ── File Sharing tab (additive, zero gui.py changes) ──────────────────
    _fs_panel = FileSharePanel(node=node, loop=loop)
    # Locate the QTabWidget dynamically — avoids modifying gui.py
    from PyQt6.QtWidgets import QTabWidget as _QTW
    _tab_w = window.findChild(_QTW)
    if _tab_w is not None:
        _tab_w.addTab(_fs_panel, "📁 File Sharing")

    # On quit, cancel the asyncio loop
    def _on_quit():
        loop.call_soon_threadsafe(loop.stop)

    app.aboutToQuit.connect(_on_quit)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
