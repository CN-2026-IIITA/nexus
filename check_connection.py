"""
check_connection.py — Project Antigravity
Sends a PING from a temporary node to both Node A and Node B,
then prints whether they are reachable and shows their Node IDs.

Run:  python3 check_connection.py
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from crypto import NodeKeypair, ANR
from network import AntigravityNode

NODE_A = ("127.0.0.1", 9000)
NODE_B = ("127.0.0.1", 9001)

GREEN  = "\033[92m"
RED    = "\033[91m"
CYAN   = "\033[96m"
YELLOW = "\033[93m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

async def check():
    # Spin up a temporary probe node on a random port
    keypair = NodeKeypair()
    probe   = AntigravityNode(keypair, "127.0.0.1", 0)

    # Bind to port 0 → OS picks a free port
    loop = asyncio.get_event_loop()
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    probe.port = sock.getsockname()[1]
    probe.anr  = ANR.create(keypair, "127.0.0.1", probe.port)
    sock.close()

    await probe.start()

    print(f"\n{BOLD}{CYAN}  PROJECT ANTIGRAVITY — Connection Checker{RESET}")
    print(f"  Probe node on port {probe.port}\n")

    results = {}
    for label, (host, port) in [("Node A", NODE_A), ("Node B", NODE_B)]:
        print(f"  Pinging {label} at {host}:{port} ...", end=" ", flush=True)
        pong = await probe.ping(host, port, timeout=3.0)
        if pong:
            anr  = pong.get_sender_anr()
            nid  = anr.node_id[:16] + "…" if anr else "unknown"
            print(f"{GREEN}✓ REACHABLE{RESET}  Node ID: {CYAN}{nid}{RESET}")
            results[label] = True
        else:
            print(f"{RED}✗ NO RESPONSE (not running or wrong port){RESET}")
            results[label] = False

    # Now check cross-routing: does Node A know about Node B?
    print()
    if results.get("Node A"):
        nb = await probe.find_node(*NODE_A, target_id=probe.anr.node_id)
        known_by_a = len(nb.nodes) if nb else 0
        if known_by_a > 0:
            print(f"  {GREEN}✓ Node A knows {known_by_a} peer(s) → nodes ARE sharing routing info{RESET}")
        else:
            print(f"  {YELLOW}⚠ Node A routing table is empty → Bootstrap not done yet{RESET}")
            print(f"    → In Node B's GUI: enter 127.0.0.1 / 9000 → click Bootstrap")

    # Summary
    both_up = all(results.values())
    print()
    if both_up:
        print(f"  {BOLD}{GREEN}✅  Both nodes are ONLINE and reachable.{RESET}")
    else:
        down = [k for k, v in results.items() if not v]
        print(f"  {BOLD}{RED}❌  {', '.join(down)} did not respond.{RESET}")
        print(f"  → Make sure run.py is running in another terminal.")

    await probe.stop()
    print()

if __name__ == "__main__":
    asyncio.run(check())
