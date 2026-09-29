"""Safe local launcher — D24.

Why this exists
---------------
The D24 defect was that `/ui/governance` returned 404 in the browser while the route
existed and its tests passed. The cause was that an older `uvicorn` process was still
bound to 127.0.0.1:8000, so the browser reached a build that predated the route. A second,
current server had been started on 0.0.0.0:8000 afterwards — Windows allows both binds, and
loopback traffic goes to the more specific 127.0.0.1 socket — so the newer server was
running but unreachable, and nothing reported the conflict.

`python -m uvicorn ...` cannot detect that. This launcher does:

  * it refuses to start when something is already answering on the port, and tells you what
    that something is (its version and phase, read from /api/build-info);
  * `--force` stops the process holding the port first, on Windows and POSIX;
  * it always binds the address it prints, and prints the URL that actually works;
  * it warns when the configured database is not local, so a shared database is never
    written to by accident.

Usage
    python scripts/serve.py                 # 127.0.0.1:8000, autoreload
    python scripts/serve.py --port 8010
    python scripts/serve.py --host 0.0.0.0  # reachable from the LAN
    python scripts/serve.py --force         # take over the port
    python scripts/serve.py --no-reload
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PLATFORM_DIR = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Port inspection
# ---------------------------------------------------------------------------

def port_is_busy(host: str, port: int) -> bool:
    """True when something already accepts connections on the port.

    Checked against 127.0.0.1 as well as the requested host, because a server bound to
    127.0.0.1 shadows one bound to 0.0.0.0 for loopback traffic — exactly the conflict that
    hid the newer build in the reported defect.
    """
    for target in {host if host not in ("0.0.0.0", "::") else "127.0.0.1", "127.0.0.1"}:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.4)
            if sock.connect_ex((target, port)) == 0:
                return True
    return False


def identify_occupant(port: int) -> dict | None:
    """Ask whatever is on the port what build it is. None when it does not answer."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/build-info", timeout=2) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError:
        return {"version": "unknown (no /api/build-info — predates D24)", "phase": "?"}
    except Exception:
        return None


def pids_on_port(port: int) -> list[int]:
    """PIDs listening on the port, best-effort and platform-specific."""
    pids: set[int] = set()
    try:
        if sys.platform == "win32":
            out = subprocess.run(
                ["netstat", "-ano", "-p", "TCP"],
                capture_output=True, text=True, timeout=10,
            ).stdout
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 5 and parts[3] == "LISTENING" and parts[1].endswith(f":{port}"):
                    pids.add(int(parts[4]))
        else:
            out = subprocess.run(
                ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
                capture_output=True, text=True, timeout=10,
            ).stdout
            pids.update(int(p) for p in out.split() if p.strip().isdigit())
    except Exception:
        pass
    return sorted(pids)


def stop_pids(pids: list[int]) -> None:
    for pid in pids:
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               capture_output=True, timeout=10)
            else:
                os.kill(pid, 15)
        except Exception as exc:  # noqa: BLE001
            print(f"  could not stop PID {pid}: {exc}")


# ---------------------------------------------------------------------------
# Database reporting
# ---------------------------------------------------------------------------

def describe_database() -> tuple[str, str]:
    """(family, human description) for the configured database — never the credentials."""
    sys.path.insert(0, str(PLATFORM_DIR))
    from backend.app.config.settings import DATABASE_URL  # noqa: PLC0415

    family = DATABASE_URL.split("://", 1)[0]
    if family.startswith("sqlite"):
        return "sqlite", f"local SQLite file ({DATABASE_URL.split('///')[-1]})"
    host = DATABASE_URL.split("@")[-1].split("/")[0] if "@" in DATABASE_URL else "?"
    return family, f"{family} at {host}"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Start the platform safely.")
    parser.add_argument("--host", default="127.0.0.1",
                        help="bind address (0.0.0.0 to expose on the LAN)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--force", action="store_true",
                        help="stop whatever already holds the port, then start")
    parser.add_argument("--no-reload", action="store_true",
                        help="disable autoreload (use for a deployment smoke test)")
    args = parser.parse_args()

    if port_is_busy(args.host, args.port):
        occupant = identify_occupant(args.port)
        pids = pids_on_port(args.port)
        print(f"Port {args.port} is already in use.")
        if occupant:
            print(f"  It is answering as: {occupant.get('name', 'a platform server')} "
                  f"{occupant.get('version')} ({occupant.get('phase')})")
            missing = occupant.get("missing_capabilities") or []
            if missing:
                print(f"  That build is missing: {', '.join(missing)}")
                print("  This is the condition that makes /ui/governance return 404 in a browser.")
        else:
            print("  It is not answering as this application.")
        if pids:
            print(f"  Held by PID(s): {', '.join(str(p) for p in pids)}")

        if not args.force:
            print()
            print("Refusing to start a second server that the browser would not reach.")
            print(f"  Re-run with --force to stop it, or use --port {args.port + 1}.")
            return 1

        print("  --force: stopping it…")
        stop_pids(pids)
        for _ in range(20):
            time.sleep(0.25)
            if not port_is_busy(args.host, args.port):
                break
        if port_is_busy(args.host, args.port):
            print("  Port is still busy. Stop the process manually and retry.")
            return 1
        print("  Port released.")

    family, description = describe_database()
    print(f"Database: {description}")
    if family != "sqlite":
        print("  WARNING: this is not a local database. Schema creation and demo seeding")
        print("  are disabled by default against it, but any ingestion you commit through")
        print("  the UI will write to it. Use a local SQLite database to experiment.")

    url_host = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    print()
    print(f"  Start here : http://{url_host}:{args.port}/ui/start")
    print(f"  Workspace  : http://{url_host}:{args.port}/ui")
    print(f"  Governance : http://{url_host}:{args.port}/ui/governance")
    print(f"  Build info : http://{url_host}:{args.port}/api/build-info")
    print()

    cmd = [
        sys.executable, "-m", "uvicorn", "backend.app.main:api",
        "--host", args.host, "--port", str(args.port),
    ]
    if not args.no_reload:
        cmd.append("--reload")
    return subprocess.call(cmd, cwd=str(PLATFORM_DIR))


if __name__ == "__main__":
    raise SystemExit(main())
