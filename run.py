"""
run.py — Project Antigravity Master Launcher
Starts N P2P nodes (auto-bootstrapped) + opens the React dashboard.

Auto-discovery is ON by default — nodes on the same WiFi find each other
automatically. No manual IP entry needed.

Usage:
  python3 run.py                      # 3 nodes + dashboard (default)
  python3 run.py --nodes 10           # 10 nodes
  python3 run.py --nodes 5 --start-port 8000
  python3 run.py --no-dashboard       # nodes only, no browser dashboard
  python3 run.py --no-discovery       # disable LAN auto-discovery
"""

import subprocess
import sys
import os
import time
import threading
import signal
import argparse
import shutil
import webbrowser

# Windows: fix asyncio subprocess handling (only needed on Python < 3.12)
if sys.platform == "win32" and sys.version_info < (3, 12):
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

PROJECT_DIR   = os.path.dirname(os.path.abspath(__file__))
DASHBOARD_DIR = os.path.join(PROJECT_DIR, "dashboard")

def _resolve_python() -> str:
    """
    Find a usable Python executable path cross-platform.
    sys.executable can be a Windows Store stub or relative path
    that subprocess / CreateProcess cannot locate directly.
    """
    import shutil

    # 1. Try sys.executable first (works on macOS/Linux always)
    exe = sys.executable
    if exe and os.path.isfile(exe):
        return exe

    # 2. Fall back to searching PATH (handles Windows Store stubs)
    for name in ("python3", "python"):
        found = shutil.which(name)
        if found and os.path.isfile(found):
            return found

    # 3. Last resort — let the OS sort it out
    return "python"

PYTHON = _resolve_python()

# ── ANSI colours ───────────────────────────────────────────────────────────
GREEN  = "\033[92m"
CYAN   = "\033[96m"
YELLOW = "\033[93m"
RED    = "\033[91m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

NODE_COLORS = [
    "\033[96m", "\033[92m", "\033[95m", "\033[93m",
    "\033[94m", "\033[91m", "\033[97m", "\033[36m",
]

# ── Banner ─────────────────────────────────────────────────────────────────
def banner(n: int):
    print(f"""
{BOLD}{CYAN}
  ╔══════════════════════════════════════════════════════════╗
  ║           PROJECT ANTIGRAVITY  —  P2P Launcher           ║
  ║      Kademlia / discv5-inspired P2P Discovery Protocol   ║
  ║   🔍 Auto-Discovery ON  —  Nodes find each other on LAN  ║
  ╚══════════════════════════════════════════════════════════╝
{RESET}""")

# ── Install Python deps ────────────────────────────────────────────────────
def install_python_deps():
    print(f"{YELLOW}[SETUP] Checking Python dependencies...{RESET}")
    deps = ["cryptography", "PyQt6"]
    missing = []
    for pkg in deps:
        r = subprocess.run([PYTHON, "-c", f"import {pkg.split('[')[0]}"],
                           capture_output=True)
        if r.returncode != 0:
            missing.append(pkg)
    if missing:
        print(f"{YELLOW}        Installing: {', '.join(missing)}{RESET}")
        subprocess.check_call([PYTHON, "-m", "pip", "install", "-q"] + missing)
        print(f"{GREEN}        ✓ Installed.{RESET}")
    else:
        print(f"{GREEN}        ✓ All Python dependencies present.{RESET}")

# ── Install & start React dashboard ───────────────────────────────────────
def start_dashboard() -> "subprocess.Popen | None":
    if not os.path.isdir(DASHBOARD_DIR):
        print(f"{YELLOW}[DASHBOARD] Not found — skipping.{RESET}")
        return None

    # Find npm
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm:
        # Try Homebrew path (macOS)
        brew_npm = "/opt/homebrew/bin/npm"
        npm = brew_npm if os.path.exists(brew_npm) else None

    if not npm:
        print(f"{YELLOW}[DASHBOARD] npm not found — skipping React dashboard.{RESET}")
        print(f"            Install Node.js from https://nodejs.org to enable it.")
        return None

    print(f"{YELLOW}[DASHBOARD] Setting up React dashboard...{RESET}")

    # Install node_modules if needed
    node_modules = os.path.join(DASHBOARD_DIR, "node_modules")
    if not os.path.isdir(node_modules):
        print(f"            Installing npm packages (first run only)...")
        subprocess.check_call([npm, "install", "--silent"],
                              cwd=DASHBOARD_DIR,
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)

    # Start dev server
    proc = subprocess.Popen(
        [npm, "run", "dev", "--", "--port", "5173"],
        cwd=DASHBOARD_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    print(f"{GREEN}[DASHBOARD] ✓ React dashboard starting at http://localhost:5173{RESET}")
    return proc

# ── Stream node logs ───────────────────────────────────────────────────────
def stream_output(proc, label, color):
    for line in proc.stdout:
        line = line.rstrip()
        if line and "qt.qpa" not in line and "WARNING" not in line:
            print(f"{color}[{label}]{RESET} {line}")

# ── Launch a single P2P node ───────────────────────────────────────────────
def launch_node(port: int, host: str, bootstrap: str = None,
                extra_args: list = None) -> subprocess.Popen:
    cmd = [PYTHON, os.path.join(PROJECT_DIR, "app.py"),
           "--host", host, "--port", str(port)]
    if bootstrap:
        cmd += ["--bootstrap", bootstrap]
    if extra_args:
        cmd += extra_args
    return subprocess.Popen(
        cmd, cwd=PROJECT_DIR,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )

# ── Main ───────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Project Antigravity — P2P Node Launcher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 run.py                  # 3 nodes + dashboard, auto-discovery ON
  python3 run.py --nodes 10       # 10 nodes
  python3 run.py --no-dashboard   # nodes only
  python3 run.py --no-discovery   # disable LAN auto-discovery
  python3 run.py --nodes 5 --start-port 8000
"""
    )
    parser.add_argument("--nodes",        type=int, default=3,
                        help="Number of nodes (default: 3)")
    parser.add_argument("--start-port",   type=int, default=9000,
                        help="First node port (default: 9000)")
    parser.add_argument("--host",         type=str, default="0.0.0.0",
                        help="Bind host (default: 0.0.0.0 — all interfaces)")
    parser.add_argument("--no-discovery", action="store_true",
                        help="Disable LAN auto-discovery")
    parser.add_argument("--no-dashboard", action="store_true",
                        help="Don't start the React dashboard")
    args = parser.parse_args()

    n           = args.nodes
    start_port  = args.start_port
    host        = args.host
    discovery   = not args.no_discovery
    run_dash    = not args.no_dashboard

    banner(n)
    install_python_deps()

    all_procs = []

    # ── React dashboard ───────────────────────────────────────────────────
    if run_dash:
        dash_proc = start_dashboard()
        if dash_proc:
            all_procs.append((dash_proc, "Dashboard", CYAN))
    print()

    # ── P2P nodes ─────────────────────────────────────────────────────────
    seed_addr  = f"127.0.0.1:{start_port}"   # local seed (first node)
    processes  = []

    print(f"{BOLD}Launching {n} P2P node(s) on {host}, ports {start_port}–{start_port+n-1}...{RESET}")
    print(f"{CYAN}Auto-discovery: {'ON 🔍 — nodes will find peers on the LAN automatically' if discovery else 'OFF'}{RESET}\n")

    extra = [] if discovery else ["--no-discovery"]

    for i in range(n):
        port  = start_port + i
        label = f"Node {chr(65 + i) if i < 26 else str(i+1)}"
        color = NODE_COLORS[i % len(NODE_COLORS)]

        is_seed   = (i == 0)
        bootstrap = None if is_seed else seed_addr

        proc = launch_node(port, host, bootstrap, extra)
        processes.append((proc, label, color))
        all_procs.append((proc, label, color))

        role = "(seed)" if is_seed else f"(→ {seed_addr})"
        print(f"  {color}▶ {label}{RESET}  {host}:{port}  {role}")

        time.sleep(2.0 if is_seed else 0.5)

    # Stream logs in background threads
    for proc, label, color in processes:
        threading.Thread(target=stream_output,
                         args=(proc, label, color), daemon=True).start()

    # ── Summary ───────────────────────────────────────────────────────────
    print(f"""
{BOLD}{GREEN}  ✅  ALL {n} NODE(S) RUNNING!{RESET}
{CYAN}  🔍  Auto-discovery is broadcasting on your LAN.
      Any PC on the same WiFi running this app will connect automatically.{RESET}

{BOLD}  Dashboard:{RESET}  http://localhost:5173
{BOLD}  Verify:   {RESET}  {YELLOW}python3 check_connection.py{RESET}

{YELLOW}  Press Ctrl+C to shut everything down.{RESET}
""")

    # ── Graceful shutdown ─────────────────────────────────────────────────
    def shutdown(sig, frame):
        print(f"\n{RED}[SHUTDOWN] Stopping everything...{RESET}")
        for proc, label, _ in all_procs:
            try: proc.terminate()
            except Exception: pass
        for proc, label, _ in all_procs:
            try:
                proc.wait(timeout=3)
                print(f"  {label} stopped.")
            except Exception: pass
        print(f"{GREEN}[SHUTDOWN] Done. Goodbye!{RESET}")
        sys.exit(0)

    signal.signal(signal.SIGINT,  shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # ── Input forwarding loop ─────────────────────────────────────────────
    # Instead of just sleeping, we read from the terminal and forward
    # commands like /chat to the first node's stdin.
    import select
    while True:
        if all(p.poll() is not None for p, _, _ in processes):
            print(f"{YELLOW}All nodes exited.{RESET}")
            break
        try:
            # Non-blocking check for Windows/Linux
            if sys.platform == "win32":
                import msvcrt
                if msvcrt.kbhit():
                    cmd = input().strip()
                    if cmd and processes:
                        proc = processes[0][0]
                        proc.stdin.write(cmd + "\n")
                        proc.stdin.flush()
                else:
                    time.sleep(0.1)
            else:
                i, _, _ = select.select([sys.stdin], [], [], 0.1)
                if i:
                    cmd = sys.stdin.readline().strip()
                    if cmd and processes:
                        proc = processes[0][0]
                        proc.stdin.write(cmd + "\n")
                        proc.stdin.flush()
        except (EOFError, KeyboardInterrupt):
            break
        except Exception:
            time.sleep(0.1)

if __name__ == "__main__":
    main()
