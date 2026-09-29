"""Process-local decision-support config (D20).

Populated at startup by the guarded port decision seed (SQLite dev only). Mirrors
`demo_state` — a module-level mutable dict the /api/decision-config endpoint returns.
Empty ({}) when no baseline/scenario demo data has been seeded (e.g. running against a
shared PostgreSQL/Supabase database), which the UI renders as an empty state.
"""
from __future__ import annotations

config: dict = {}
