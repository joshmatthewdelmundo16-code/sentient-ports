# D28 — The open-source stack: Dagster, Airbyte, Evidence (and Airflow)

The reference comparison recommends replicating the "Sentient Ports experience" with
**Dagster** (link the models) + **Airbyte** (fetch the Excel data) + **Evidence** (interactive
reports), with Airflow as the alternative orchestrator. All four are now integrated.

They are integrated as **plumbing around the platform, not in place of it**. The federation
semantics — contracts, change detection, propagation, results, lineage, scenarios, governance —
stay in the platform, which is the part the reference itself says open source does not provide
("you'll have to write the logic that defines how the fuel price in Excel should actually change
the port's shipping schedule"). Every integration is optional and off by default; the platform
runs without any of them.

| Reference layer | Tool | What it does here | How it stays governed |
|---|---|---|---|
| Model linking (orchestrator) | **Dagster** | Optional executor (`"executor": "dagster"`); a code location with the stable job `platform_graphrun_job`; the federation shown as a Dagster **asset graph**; each dataset a run computes recorded as an **asset materialization** | Dagster triggers, the platform computes. The callback is service-token-only, correlated on the Dagster run id, idempotent |
| Model linking (orchestrator) | **Airflow** | Optional executor (D21), one stable DAG | Same contract as Dagster |
| Excel in/out (connector) | **Airbyte** | Airbyte syncs Excel (SharePoint / Google Drive / S3), databases or SaaS APIs into a staging schema; the `airbyte` connector ingests the newest synced rows; the platform can trigger and follow Airbyte syncs; the poll scheduler can watch the stream | Airbyte never writes a dataset. Rows go through contract validation → change detection → propagation → lineage like every connector. Tables must carry `_airbyte_extracted_at` and the organization's stream prefix |
| Dynamic reports / master plans | **Evidence** | Markdown reports: overview, ripple effects, master plan, scenarios, shared outputs | Its only data source is a CSV export taken **through the API as a signed-in user** — same tenant scope, roles and approved-output boundary as the UI. No database connection |
| — | Superset | **Not added** | It would query the database directly and bypass `/api/exposed-outputs` |
| — | Kestra, Meltano | **Not added** | Kestra duplicates Dagster/Airflow; Meltano duplicates Airbyte |

## What was verified, and how

| Integration | Verification |
|---|---|
| Dagster | `tests/test_d28_dagster_executor.py` (fake transport + httpx mock of the GraphQL API). **End to end against a real Dagster 1.13.24** (`dagster dev`): the code location loaded the job and the 11-asset federation graph; the platform launched a GraphRun through GraphQL; Dagster's op called back; the platform recorded 5 results; `/reconcile` read `SUCCESS` from Dagster; Dagster recorded 5 asset materializations tagged with the GraphRun id. |
| Airbyte | `tests/test_d28_airbyte_connector.py`: tables in Airbyte's Postgres-destination layout (wide and field/value sheets), newest-sync selection, merge onto current values, propagation, unchanged re-reads, refusals (unknown field, bad value, contract bounds, empty stream, table not synced, non-Airbyte table, another organization's prefix, SQL-shaped names), the sync API client (token and client-credentials) against a fake, and the scheduler. **Not run against a real Airbyte** — it needs Docker or Kubernetes, and neither exists in this environment. |
| Evidence | `tests/test_d28_evidence_export.py`: every table written, shared outputs identical to `/api/exposed-outputs`, no cross-organization leakage, non-members refused, and the exporter/Evidence have no database access. **Evidence 40.1.8 `build:strict` passed** on a demo-network export, and all five pages rendered in Chrome with data and no query errors. |

## Running each one

All commands run from `platform/`.

### Dagster

```powershell
python -m venv .venv-dagster; .\.venv-dagster\Scripts\pip install -r dagster_platform\requirements.txt
# Platform side (.env or environment):
#   DAGSTER_ENABLED=true
#   DAGSTER_GRAPHQL_URL=http://127.0.0.1:3000/graphql
#   PLATFORM_SERVICE_TOKEN=<long random string>        # needed with AUTH_MODE=required
# Dagster side:
$env:PLATFORM_CALLBACK_BASE_URL = "http://127.0.0.1:8000"
$env:PLATFORM_CALLBACK_TOKEN    = "<the same PLATFORM_SERVICE_TOKEN>"
$env:PLATFORM_FEDERATION_MAP    = "evidence\sources\federation_map.json"   # optional: asset graph
.\.venv-dagster\Scripts\dagster dev -w dagster_platform\workspace.yaml
```

Run a graph through Dagster: `POST /api/graph-executions` with
`{"target_version_id": "...", "executor": "dagster"}`. The run returns `running`; the platform
status follows Dagster via `POST /api/executions/{run_id}/reconcile`. In the Dagster UI, the
**federation** asset group shows the model links, and each run marks the datasets it computed.

### Airbyte

1. In Airbyte, create a **Postgres destination** pointing at the platform database, schema
   `airbyte` (or set `AIRBYTE_STAGING_SCHEMA`).
2. Create a connection from your source (e.g. the File or SharePoint source reading the
   assumptions workbook) with **destination stream prefix** `<org_key>__` — for example
   `port_northbay__` — and sync mode *full refresh | overwrite*.
3. In the platform, an admin defines the source:

   ```json
   POST /api/connectors
   {"name": "Assumptions workbook (Airbyte)", "kind": "airbyte",
    "target_dataset_id": "<dataset id>",
    "config": {"stream_table": "port_northbay__assumptions", "layout": "field_value",
               "connection_id": "<Airbyte connection UUID, optional>"},
    "poll_interval_s": 300}
   ```

   `layout` is `wide` (one column per contract field; optional `fields` maps field → column) or
   `field_value` (a two-column `field | value` sheet, like the CSV connector).
4. Ingest with `POST /api/connectors/{id}/airbyte/ingest`, or let the poll scheduler do it
   (`POLL_SCHEDULER_ENABLED=true`). To start the Airbyte sync from the platform, set
   `AIRBYTE_API_URL` and a token (or client id and secret), then
   `POST /api/connectors/{id}/airbyte/sync` and `GET /api/connectors/{id}/airbyte/jobs/{job_id}`.

### Evidence

```powershell
python scripts/export_platform_data.py --base-url http://127.0.0.1:8000 `
    --org port-northbay --email analyst@northbay.example    # password: --password or PLATFORM_EXPORT_PASSWORD
cd evidence
npm install
npm run sources
npm run dev          # or: npm run build   (static site in evidence/build)
```

Re-run the export (e.g. after an Excel upload or an Airbyte sync) and `npm run sources` to
refresh the reports. The export is per organization and per user: what a viewer cannot see in the
UI is not in their export.

## Limits, stated

* Airbyte was not run here; its integration is proven against its documented table layout and
  API shapes, not a live instance.
* Evidence reports are a batch snapshot of the export, not a live view.
* Dagster and Airflow are schedulers/triggers here; they never compute a model. That is
  deliberate — computing inside them would split results and lineage across two systems.
* The Dagster op retries a 404/409 callback briefly, because the platform commits a submitted run
  at the end of the request that launched it.
