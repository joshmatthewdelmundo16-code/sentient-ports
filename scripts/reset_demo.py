"""Reset the demo database and reseed the federation chain.

Run from the platform/ directory with the virtual environment activated:

    python scripts/reset_demo.py

What this does:
  1. SQLite: deletes the database file. Any other database is REFUSED (D25).
  2. Re-creates all tables via init_db().
  3. Seeds the Fuel Price → Shipping Cost → Operations Cost → Emissions chain.
  4. Prints the resulting IDs so you can verify the seed.

After running this, start the application normally and the browser UI will
reflect a clean, fresh demo state.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure the platform package is importable when run as a script
_PLATFORM_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PLATFORM_DIR))

from backend.app.config.settings import DATABASE_URL
from backend.app.persistence.database import Base, SessionLocal, engine, init_db
from backend.app.ui.demo_seed import seed_demo_data


def main() -> None:
    # ── 1. Clear existing data ─────────────────────────────────────────────
    if DATABASE_URL.startswith("sqlite:///"):
        raw = DATABASE_URL[len("sqlite:///"):]
        db_path = Path(raw) if os.path.isabs(raw) else _PLATFORM_DIR / raw
        db_path = db_path.resolve()
        if db_path.exists():
            db_path.unlink()
            print(f"Deleted:  {db_path}")
        else:
            print(f"No file:  {db_path} (nothing to delete)")
    else:
        # D25: never drop a shared database. This script used to run drop_all() against
        # whatever DATABASE_URL pointed at — including a shared Supabase project. A demo
        # reset is a local operation; for a disposable PostgreSQL, drop it with its own
        # tooling (or `alembic downgrade base`) deliberately.
        print(
            "Refusing to reset a non-SQLite database. reset_demo.py only resets the local "
            "SQLite demo file. Unset DATABASE_URL (or point it at a sqlite:/// file) and "
            "run again.",
            file=sys.stderr,
        )
        sys.exit(2)

    # ── 2. Recreate schema ─────────────────────────────────────────────────
    init_db()
    print("Schema:   created (CREATE TABLE IF NOT EXISTS)")

    # ── 3. Seed demo chain ─────────────────────────────────────────────────
    db = SessionLocal()
    try:
        config = seed_demo_data(db)
        db.commit()
    except Exception as exc:
        db.rollback()
        print(f"\nSeed FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        db.close()

    # ── 4. Report ──────────────────────────────────────────────────────────
    print("\nDemo chain seeded:")
    for i, m in enumerate(config.get("models", []), 1):
        print(
            f"  {i}. {m['name']:<20s}"
            f"  version={m['version_id'][:8]}…"
            f"  dataset={m['dataset_id'][:8]}…"
        )

    print(f"\nTerminal version (Emissions): {config.get('terminal_version_id', '')[:8]}…")
    print(f"Fuel Price version:           {config.get('fuel_price_version_id', '')[:8]}…")
    print(f"Input dataset ({config.get('input_dataset_name', '')}): {config.get('input_dataset_id', '')[:8]}…")

    print("\nDone. Start the application (it seeds the rest of the demo on first start):")
    print("  python scripts/serve.py")
    print("\nThen open:  http://127.0.0.1:8000/app/")


if __name__ == "__main__":
    main()
