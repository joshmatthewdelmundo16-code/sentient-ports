"""Build the React product into frontend/dist — D25.

Finds Node on PATH, or the portable copy at <repo>/.tools/node, installs dependencies
exactly as locked (`npm ci`) when needed, type-checks, and builds. FastAPI serves the result
at /app; /api/build-info reports its build id so a stale bundle is visible.

    python scripts/build_frontend.py            # install if needed, then build
    python scripts/build_frontend.py --check    # also run lint and unit tests
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

PLATFORM = Path(__file__).resolve().parents[1]
FRONTEND = PLATFORM / "frontend"
PORTABLE = PLATFORM.parent / ".tools" / "node"


def find_npm() -> tuple[str, dict[str, str]]:
    env = dict(os.environ)
    npm = shutil.which("npm")
    if npm is None and PORTABLE.is_dir():
        env["PATH"] = str(PORTABLE) + os.pathsep + env.get("PATH", "")
        npm = shutil.which("npm", path=env["PATH"])
    if npm is None:
        sys.exit("Node.js (npm) was not found. Install Node 22+ or place a portable copy at "
                 f"{PORTABLE}.")
    return npm, env


def run(npm: str, env: dict[str, str], *args: str) -> None:
    print("$ npm", " ".join(args), flush=True)
    subprocess.run([npm, *args], cwd=FRONTEND, env=env, check=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="also run lint and unit tests")
    opts = ap.parse_args()
    npm, env = find_npm()
    if not (FRONTEND / "node_modules").is_dir():
        run(npm, env, "ci", "--no-audit", "--no-fund")
    if opts.check:
        run(npm, env, "run", "lint")
        run(npm, env, "test")
    run(npm, env, "run", "build")
    print(f"Built {FRONTEND / 'dist'} — served at /app by the FastAPI process.")


if __name__ == "__main__":
    main()
