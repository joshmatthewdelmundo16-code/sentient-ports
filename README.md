# Federated Model Orchestration & Decision-Support Platform

> **Demo tier (D0) — Phase 0 Foundation**  
> Python · FastAPI · SQLAlchemy · SQLite · pytest  
> Docker-free, no auth, local execution.

An open-source technical approximation of the functional architecture
described for Sentient Ports / Sentient Hubs. The product core is a
**model orchestration + federation layer** that understands heterogeneous
models, their inputs/outputs, versions, contracts, and the propagation
of change through dependent models.

---

## Prerequisites

| Tool | Version | Notes |
|------|---------|-------|
| Python | 3.11+ | 3.14 tested |
| pip | any | ships with Python |
| Git | any | for version control |

> Docker, PostgreSQL, Node are **not required** for the demo.

---

## Quick Start

All commands run from the `platform/` directory with the venv **activated**.

### 1 — Set up the environment

```powershell
# From the repo root (Sentient Ports/)
Set-Location platform

# Activate the virtual environment (already created)
.\.venv\Scripts\Activate.ps1

# If activation fails with a policy error, run once:
# Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

### 2 — Configure (optional)

```powershell
# Copy the template and edit if needed (SQLite defaults work out of the box)
Copy-Item .env.example .env
```

### 3 — Run the application

```powershell
python -m uvicorn backend.app.main:api --reload --host 127.0.0.1 --port 8000
```

Then open:

- **http://127.0.0.1:8000/health** — platform health check
- **http://127.0.0.1:8000/docs** — interactive API docs (Swagger UI)

Expected `/health` response:

```json
{
  "ok": true,
  "version": "0.1.0-d0",
  "environment": "development",
  "db": "ok",
  "storage": "ok",
  "timestamp": "2026-09-29T..."
}
```

### 4 — Run the tests

```powershell
python -m pytest tests/ -v
```

Expected output: **all tests pass** (G1 Foundation gate).

---

## Project Structure

```
platform/
├── backend/
│   └── app/
│       ├── main.py              # FastAPI bootstrap + /health
│       ├── config/
│       │   └── settings.py      # Environment / DB / storage config
│       ├── persistence/
│       │   └── database.py      # SQLAlchemy entities + init_db()
│       ├── api/                 # (D1+) HTTP routers
│       ├── registry/            # (D1+) Model registry service
│       ├── contracts/           # (D1+) DataContract engine
│       ├── federation/          # (D1+) Dependency graph
│       ├── orchestration/       # (D1+) Orchestrator engine
│       ├── adapters/            # (D1+) ModelAdapter interface + PythonAdapter
│       ├── execution/           # (D1+) Execution runner
│       ├── lineage/             # (D1+) Lineage recording + traversal
│       ├── results/             # (D1+) Result persistence
│       └── scenarios/           # (D1+) Scenario management
├── demo_data/                   # (D1+) Seed scripts A→B→C
├── tests/
│   └── test_d0_smoke.py         # D0 foundation smoke tests
├── frontend/                    # (D1+) Jinja2 templates + static
├── docs/
├── .env.example                 # Environment template
├── .gitignore
├── pytest.ini
└── requirements.txt
```

---

## Demo Model Chain (D1+ implementation)

Synthetic demonstration — clearly labelled, not production port data.

| Model | Input | Output | Formula |
|-------|-------|--------|---------|
| A — Shipping Cost | `fuel_price` | `shipping_cost` | `200 + fuel_price × 3` |
| B — Operations Cost | `shipping_cost` | `operations_cost` | `200 + shipping_cost` |
| C — Emissions | `operations_cost` | `emissions` | `operations_cost × 0.17` |

**Baseline** (fuel = 100): 500 / 700 / 119.0  
**After change** (fuel = 120): 560 / 760 / 129.2

---

## Phase Roadmap

| Phase | Scope | Status |
|-------|-------|--------|
| D0 — Foundation | FastAPI bootstrap, DB entities, `/health`, smoke tests | ✅ **Done** |
| D1 — Registry + Core | Model registry, versioning, contracts, dependency graph | Next |
| D2 — Adapters + Execution | PythonAdapter, Orchestrator, propagation, lineage | Planned |
| D3 — API + Dashboard | Full REST API, Jinja2 dashboard, E2E tests | Planned |
| MVP (P1) | PostgreSQL, ExcelAdapter, scenarios, auth, Docker Compose | Planned |

See `FEDERATED_MODEL_PLATFORM_MASTER_PLAN.md` for the full architecture.

---

## Development Notes

- **DB file:** `federated_platform.db` (SQLite, auto-created on startup, gitignored)
- **Storage:** `./storage/` (local artifact FS, auto-created, gitignored)
- **No client data required:** demo uses synthetic fuel→shipping→operations→emissions chain
- **Migration path:** Alembic added at P1; `init_db()` (CREATE TABLE IF NOT EXISTS) until then
