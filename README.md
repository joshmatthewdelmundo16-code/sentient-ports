# Federated Model Orchestration & Decision-Support Platform

> **D16 — Federation Integrity**  
> Python · FastAPI · SQLAlchemy · SQLite/PostgreSQL · Alembic · Jinja2 · pytest  
> Docker-free local dev, optional PostgreSQL for shared deployments.

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

> Docker, Node are **not required**. PostgreSQL is optional (SQLite is the default).

---

## Quick Start

All commands run from the `platform/` directory with the venv **activated**.

### 1 — Set up the environment

```powershell
# From the repo root (Sentient Ports/)
Set-Location platform

# Activate the virtual environment
.\.venv\Scripts\Activate.ps1

# If activation fails with a policy error, run once:
# Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned

# Install dependencies (first time only)
pip install -r requirements.txt
```

### 2 — Reset / initialise the database

Run this before starting the application for the first time, or whenever
you want a clean slate:

```powershell
python scripts/reset_demo.py
```

This deletes the SQLite database file (if present), recreates the schema,
and seeds the four-model demo federation. Expected output:

```
Deleted:  ...\federated_platform.db
Schema:   created (CREATE TABLE IF NOT EXISTS)

Demo chain seeded:
  1. Fuel Price           version=<id>…  dataset=<id>…
  2. Shipping Cost        version=<id>…  dataset=<id>…
  3. Operations Cost      version=<id>…  dataset=<id>…
  4. Emissions            version=<id>…  dataset=<id>…

Terminal version (Emissions): <id>…
Fuel Price version:           <id>…
Fuel Price dataset:           <id>…
```

### 3 — Start the application

```powershell
python scripts/serve.py
```

Use this rather than calling uvicorn directly. It refuses to start when something already
holds the port, and tells you which build that something is — see
[Stale servers](#stale-servers-the-d24-defect) for why that matters. Options:
`--port`, `--host 0.0.0.0`, `--force` (take over the port), `--no-reload`.

### 4 — Open the browser UI

**http://127.0.0.1:8000/ui/start** — Start Here. Read this first; it explains the whole
product without requiring this README.

Other endpoints:

- **http://127.0.0.1:8000/ui** — the decision workspace
- **http://127.0.0.1:8000/ui/governance** — participants, approvals, exposed outputs
- **http://127.0.0.1:8000/health** — platform health check (JSON)
- **http://127.0.0.1:8000/api/build-info** — which build is answering, and what it can do
- **http://127.0.0.1:8000/docs** — interactive Swagger API docs

---

## Stale servers (the D24 defect)

`/ui/governance` once returned `{"detail":"Not Found"}` in a browser while the route
existed in the source and its tests passed. The route was fine: an older `uvicorn` process
was still bound to `127.0.0.1:8000` and the browser was reaching a build that predated the
route. A newer server had been started on `0.0.0.0:8000` afterwards — Windows allows both
binds, and loopback traffic goes to the more specific socket — so the current server was
running and unreachable.

Three things now make that impossible to miss:

- `GET /api/build-info` reports the running build and a capability map derived from its
  **real route table**, so it cannot claim a route the process does not serve.
- The UI calls it on load and shows a banner naming the missing capabilities.
- `python scripts/serve.py` refuses to start onto an occupied port and identifies the
  occupant.

If a page ever looks wrong, check `/api/build-info` first — `missing_capabilities` should
be `[]`.

---

## Excel assumptions workflow

Open **Excel Assumptions** in the workspace, or use the API directly:

| Step | Endpoint |
|---|---|
| See what a mapping reads, with units and current values | `GET /api/ingestions/mappings/{key}/preview` |
| Download a pre-filled workbook | `GET /api/ingestions/template?mapping={key}` |
| Dry run — what would change, is it valid (**writes nothing**) | `POST /api/ingestions/excel/validate?mapping={key}` |
| Commit — write, detect change, propagate | `POST /api/ingestions/excel?mapping={key}` |
| Provenance for one ingestion | `GET /api/ingestions/{id}` |

The connector reads cached cell values only. It never evaluates formulas, never runs
macros or VBA, never modifies or stores your file, and reads only the mapped cells. A
formula cell with no cached value is rejected with an explanation rather than guessed at.

---

## Deployment

See **[DEPLOYMENT.md](../DEPLOYMENT.md)** at the repository root for running this as a
shared service (container + PostgreSQL), the full environment-variable reference, and the
migration procedure.

---

## Demo Flow

Follow these steps in the browser UI at `http://127.0.0.1:8000/ui`:

1. **Federation tab** — verify the four models are listed:
   Fuel Price → Shipping Cost → Operations Cost → Emissions

2. **Graph tab** — inspect the dependency topology (4 nodes, 3 edges)

3. **Execute tab** — set Fuel Price to `100`, click **▶ Run Graph**
   - Expected outputs: Fuel=100, Shipping=200, Operations=300, Emissions=150
   - Click **View Run Detail →** to inspect persisted results

4. **History tab** — confirm the run appears with status `succeeded`
   - Click **Details** → inspect step results and lineage

5. **Propagate tab** — set New Fuel Price to `120`, click **⚡ Apply Change & Propagate**
   - Expected new outputs: Fuel=120, Shipping=240, Operations=360, Emissions=180
   - Result cards appear automatically with Lineage buttons

### Demo arithmetic (SyntheticAdapter scalars)

| Model | Scalar | fuel=100 | fuel=120 |
|-------|--------|----------|----------|
| Fuel Price | ×1.0 | 100 | 120 |
| Shipping Cost | ×2.0 | 200 | 240 |
| Operations Cost | ×1.5 | 300 | 360 |
| Emissions | ×0.5 | 150 | 180 |

> Synthetic values — clearly labelled, not production port data.

### Federation model (D16)

- **Data flows through datasets.** Each model version declares inputs and outputs
  (`model_io_binding`, plus the datasets named by its dependency edges). Inputs are
  assembled per dataset, with optional field maps (`{"value": "a"}`); two inputs that
  resolve to the same name are an explicit error, never a silent overwrite.
- **Datasets hold the truth.** `Dataset.current_value/current_hash` are maintained on
  every write: canonical JSON → SHA-256 → unchanged = no-op, changed = `ChangeEvent`
  with old/new value and provenance. Model-produced datasets reject external writes.
- **Contracts are enforced** on dataset writes, model inputs and model outputs
  (type, required, nullable, min/max, undeclared fields, unit metadata).
- **One graph execution = one GraphRun** (`execution_run`, `run_kind="graph"`) with one
  step per model; on failure the remaining steps are `skipped` and nothing is published.
- **Server-authoritative results and lineage.** Results follow declared outputs; lineage
  edges record run, step, source/target dataset and the originating `ChangeEvent`.
- **Persisted execution behaviour.** `model_version.adapter_type/adapter_config`.
  Only active versions of `draft`/`active` models execute.

API (breaking changes from D15 are rejected with 422, not ignored):

```text
POST /api/graph-executions  {"target_version_id", "dataset_values": {dataset_id: record}, "input_data"?}
POST /api/changes/propagate {"dataset_id", "value": record}      → 201 changed / 200 no-op
GET  /api/executions/{run_id}[/results|/lineage|/change-events]
GET  /api/datasets[/{id}[/change-events]]
```

---

## Running the Tests

```powershell
# Full suite (all phases)
python -m pytest tests/ -q

# End-to-end demo scenario only
python -m pytest tests/test_d13_e2e.py -v

# A specific phase
python -m pytest tests/test_d11_api.py -v
```

The suite always runs against in-memory SQLite. `conftest.py` forces `DATABASE_URL` to
`sqlite:///:memory:` before any test module loads, and **refuses to run** if you have
exported a non-SQLite `DATABASE_URL` — tests create, mutate and drop data, so pointing
them at a shared database would be destructive. PostgreSQL coverage is opted into
separately via `TEST_DATABASE_URL`, which must be a disposable database.

All tests use in-memory SQLite by default — no external services required.

To run PostgreSQL-specific tests (requires a running PostgreSQL instance):

```powershell
$env:TEST_DATABASE_URL = "postgresql+psycopg://user:pass@localhost:5432/test_db"
python -m pytest tests/test_d15_postgresql.py tests/test_d16_federation_integrity.py -v
```

PostgreSQL tests run inside a transaction that is rolled back, so they never modify
the target database (safe against a shared MVP instance).

### Alembic Migrations (PostgreSQL)

```powershell
# Apply all migrations to the configured DATABASE_URL
python -m alembic upgrade head

# Generate a new migration after ORM changes
python -m alembic revision --autogenerate -m "description"
```

---

## PostgreSQL Setup (Optional)

The platform supports both SQLite (default, zero-install) and PostgreSQL
(for shared/team deployments).

1. Create a managed PostgreSQL database (Supabase, Neon, Railway, RDS, etc.)
2. Copy `.env.example` to `.env`
3. Set `DATABASE_URL` to your PostgreSQL connection string:
   ```
   DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/DBNAME
   ```
4. Run migrations: `python -m alembic upgrade head`
5. Seed demo data: `python scripts/reset_demo.py`
6. Start the application normally

> **Security:** Never commit `.env` or credentials. The `.env` file is gitignored.
> The health endpoint does not expose database credentials.

---

## Project Structure

```
platform/
├── backend/
│   └── app/
│       ├── main.py                  # FastAPI bootstrap, startup seed, /health
│       ├── config/
│       │   └── settings.py          # Environment / DB / storage config
│       ├── persistence/
│       │   └── database.py          # SQLAlchemy ORM entities + init_db()
│       ├── api/                     # D11 HTTP routers (models, graph, execution…)
│       │   └── deps.py              # FastAPI dependencies (DB, adapter registry)
│       ├── registry/                # D3 Model registry service
│       ├── contracts/               # D4 Data contract engine
│       ├── federation/              # D5 Dependency graph
│       ├── adapters/                # D6 ModelAdapter interface + SyntheticAdapter
│       ├── execution/               # D7 Single-model execution runner
│       ├── orchestration/           # D8 Graph orchestration
│       ├── propagation/             # D9 Change propagation service
│       ├── results/                 # D10 Result + lineage persistence
│       └── ui/                      # D12 Browser UI
│           ├── demo_seed.py         # Idempotent demo chain seeder
│           ├── demo_state.py        # Runtime state (registry, config)
│           ├── router.py            # /ui and /api/demo-config routes
│           └── templates/
│               └── index.html       # Single-page browser UI (Jinja2)
├── scripts/
│   └── reset_demo.py               # Clean-start / reseed script
├── tests/
│   ├── test_d0_smoke.py            # D0 foundation
│   ├── test_d3_registry.py         # D3 model registry
│   ├── test_d4_contracts.py        # D4 data contracts
│   ├── test_d5_graph.py            # D5 dependency graph
│   ├── test_d6_adapters.py         # D6 model adapters
│   ├── test_d7_execution.py        # D7 single-model execution
│   ├── test_d8_orchestration.py    # D8 graph orchestration
│   ├── test_d9_propagation.py      # D9 change propagation
│   ├── test_d10_results.py         # D10 results + lineage
│   ├── test_d11_api.py             # D11 REST API
│   ├── test_d12_ui.py              # D12 browser UI routes
│   ├── test_d13_e2e.py             # D13 end-to-end demo scenario
│   ├── test_d14_visualization.py   # D14 client-grade visualization
│   ├── test_d15_postgresql.py      # D15 PostgreSQL compatibility
│   ├── test_d16_federation_integrity.py  # D16 datasets, contracts, GraphRun, fan-in
│   └── demo_support.py             # Shared demo-seed test helpers
├── alembic/                         # Alembic migration infrastructure
│   ├── env.py                       # Migration environment config
│   ├── script.py.mako               # Migration template
│   └── versions/
│       ├── 001_baseline_schema.py   # Baseline migration (all 10 tables)
│       └── 002_federation_integrity.py  # D16: I/O bindings, GraphRun, provenance
├── alembic.ini                      # Alembic configuration
├── .env.example                     # Environment template
├── pytest.ini
└── requirements.txt
```

---

## Phase Roadmap

| Phase | Scope | Status |
|-------|-------|--------|
| D0 — Foundation | FastAPI bootstrap, DB entities, `/health`, smoke tests | ✅ Done |
| D1 — Core Persistence | SQLAlchemy repositories, base entities | ✅ Done |
| D2 — Persistence Repositories | BaseRepository[T], all domain repos | ✅ Done |
| D3 — Model Registry | `register_model`, `register_model_version`, versioning | ✅ Done |
| D4 — Data Contracts | Schema validation, contract management | ✅ Done |
| D5 — Dependency Graph | Producer→dataset→consumer, topological sort | ✅ Done |
| D6 — Model Adapters | ModelAdapter interface, SyntheticAdapter | ✅ Done |
| D7 — Single Model Execution | ExecutionRunner, step recording | ✅ Done |
| D8 — Graph Orchestration | Multi-step topological execution | ✅ Done |
| D9 — Change Propagation | ChangeEvent, PropagationService, lineage | ✅ Done |
| D10 — Results & Lineage | Result persistence, lineage API | ✅ Done |
| D11 — API Layer | Full REST API, FastAPI routers | ✅ Done |
| D12 — Browser UI | Jinja2 server-rendered HTML + vanilla JS | ✅ Done |
| D13 — E2E Demo & Hardening | Smoke tests, reset script, UI hardening | ✅ Done |
| D14 — Client-Grade Visualization | Federation hub, visual graph, impact/results/lineage | ✅ Done |
| D15 — PostgreSQL / Shared MVP DB | Dual SQLite/PG, Alembic migrations, psycopg driver | ✅ Done |
| D16 — Federation Integrity | Dataset routing, contract enforcement, change detection, GraphRun, fan-in | ✅ Done |
| D17 — Toy Port Domain | Five synthetic port models, unit-carrying contracts, two-level DAG | ✅ Done |
| D18 — Excel Integration | `.xlsx` connector, named cell mappings, ingestion runs, provenance | ✅ Done |
| D19 — Scenarios & Baseline | Baselines, overrides, read-only scenario execution, comparison | ✅ Done |
| D20 — Decision-Support UI | Decision workspace, KPI cards, comparison rendering | ✅ Done |
| D21 — Airflow Optional Executor | External executor behind a feature flag, off by default | ✅ Done |
| D22 — Participants & Approved Outputs | Governance model, approval lifecycle, exposed-output boundary | ✅ Done |
| D23 — Decision-Support Product UI | Product navigation, impact/why, sources, execution & governance panels | ✅ Done |
| D24 — Parity, Live Productization & Guided Excel | Build self-report, Start Here onboarding, guided Excel workflow, governance seeding, container + PostgreSQL deployment, startup safety gates | ✅ Done |
| MVP (P1) | Authentication, object storage, additional connectors | Planned |

---

## Development Notes

- **DB file:** `federated_platform.db` (SQLite default, auto-created at startup, gitignored)
- **PostgreSQL:** set `DATABASE_URL` env var; run `alembic upgrade head` for migrations
- **Storage:** `./storage/` (local artifact FS, auto-created, gitignored)
- **Reset:** `python scripts/reset_demo.py` wipes and reseeds (SQLite: deletes file; PG: drops tables)
- **Migrations:** `001` baseline (10 tables) → `002` federation integrity. Run
  `alembic upgrade head` before starting the app on an existing PostgreSQL database;
  the startup seed then upgrades the demo federation in place.
- **Tests:** default suite uses in-memory SQLite (`StaticPool`); PG tests require `TEST_DATABASE_URL`
- **Security:** `.env` is gitignored; health endpoint never exposes credentials
