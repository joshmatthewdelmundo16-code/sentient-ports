"""Migrate local SQLite data to Supabase (PostgreSQL).

Reads every row from the local SQLite database and inserts it into
Supabase, skipping rows whose primary key already exists (idempotent).

Usage (from platform/):
    python scripts/migrate_sqlite_to_supabase.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, inspect, text, MetaData

# ── Connection strings ────────────────────────────────────────────────────────

SQLITE_URL = os.environ.get(
    "SQLITE_URL",
    f"sqlite:///{Path(__file__).resolve().parents[1] / 'federated_platform.db'}",
)

PG_URL = os.environ.get("DATABASE_URL", "")
if not PG_URL:
    print("ERROR: Set DATABASE_URL to your Supabase connection string.")
    sys.exit(1)

# Normalise URL for psycopg driver
if PG_URL.startswith("postgres://"):
    PG_URL = "postgresql://" + PG_URL[len("postgres://"):]
if PG_URL.startswith("postgresql://"):
    PG_URL = "postgresql+psycopg://" + PG_URL[len("postgresql://"):]
if "pgbouncer=true" in PG_URL:
    PG_URL = PG_URL.replace("?pgbouncer=true", "").replace("&pgbouncer=true", "")
if "supabase" in PG_URL and "sslmode=" not in PG_URL:
    PG_URL += ("&" if "?" in PG_URL else "?") + "sslmode=require"

# ── Table migration order (parents before children) ──────────────────────────

TABLE_ORDER = [
    "organizations",
    "app_users",
    "memberships",
    "models",
    "datasets",
    "dataset_values",
    "model_inputs",
    "model_outputs",
    "contracts",
    "contract_bindings",
    "ingestion_sources",
    "ingestion_logs",
    "baselines",
    "baseline_runs",
    "scenarios",
    "scenario_overrides",
    "scenario_runs",
    "execution_runs",
    "execution_run_outputs",
    "approved_outputs",
    "governance_participants",
    "governance_approvals",
    "audit_events",
    "connectors",
    "connector_runs",
    "network_cases",
    "plan_periods",
]


def migrate() -> None:
    print(f"\nSQLite  → {SQLITE_URL}")
    print(f"Supabase → {PG_URL[:50]}...\n")

    sqlite_eng = create_engine(SQLITE_URL)
    pg_eng = create_engine(PG_URL)

    sqlite_meta = MetaData()
    sqlite_meta.reflect(sqlite_eng)

    pg_insp = inspect(pg_eng)
    pg_tables = set(pg_insp.get_table_names())

    # Build ordered list: known order first, then any remaining tables
    known = [t for t in TABLE_ORDER if t in sqlite_meta.tables]
    extra = [t for t in sqlite_meta.tables if t not in TABLE_ORDER]
    ordered = known + extra

    total_inserted = 0

    with pg_eng.begin() as pg_conn:
        for table_name in ordered:
            if table_name not in pg_tables:
                print(f"  SKIP  {table_name} (not in Supabase schema)")
                continue

            table = sqlite_meta.tables[table_name]

            with sqlite_eng.connect() as sq_conn:
                rows = sq_conn.execute(table.select()).mappings().all()

            if not rows:
                print(f"  empty {table_name}")
                continue

            cols = list(rows[0].keys())
            inserted = 0
            skipped = 0

            for row in rows:
                row_dict = dict(row)
                placeholders = ", ".join(f":{c}" for c in cols)
                col_list = ", ".join(f'"{c}"' for c in cols)
                sql = text(
                    f'INSERT INTO "{table_name}" ({col_list}) VALUES ({placeholders}) '
                    f"ON CONFLICT DO NOTHING"
                )
                result = pg_conn.execute(sql, row_dict)
                if result.rowcount:
                    inserted += 1
                else:
                    skipped += 1

            total_inserted += inserted
            print(f"  {table_name}: {inserted} inserted, {skipped} skipped")

    print(f"\n✓ Migration complete — {total_inserted} rows inserted into Supabase.")


if __name__ == "__main__":
    migrate()
