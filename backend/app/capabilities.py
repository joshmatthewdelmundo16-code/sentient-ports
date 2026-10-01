"""Capability truth registry — D27.

One honest, machine-readable statement of what this platform does, against the publicly
described Sentient Ports / Sentient Hubs capabilities it is inspired by. Served at
/api/capabilities and rendered by the React "Capabilities" page.

Statuses: implemented · partial · illustrative · not_implemented · unverified ·
external_dependency.

Honesty guard: a capability that cites routes as evidence is DOWNGRADED to "unverified" at
runtime if any of those routes is not served by this process — the registry cannot claim a
feature the running build does not have.

This is an independent implementation of the KIND of capability the public material describes.
It is not, and does not claim to be, a reproduction of any proprietary system.
"""

from __future__ import annotations

from typing import Any

C = dict[str, Any]


def _c(key: str, area: str, name: str, status: str, reference: str, implementation: str,
       limits: str = "", routes: tuple[str, ...] = (), modules: tuple[str, ...] = (),
       tests: tuple[str, ...] = ()) -> C:
    return {"key": key, "area": area, "name": name, "status": status, "public_reference": reference,
            "implementation": implementation, "limits": limits, "evidence_routes": list(routes),
            "evidence_modules": list(modules), "evidence_tests": list(tests)}


CAPABILITIES: list[C] = [
    # --- Federation core ------------------------------------------------------------------
    _c("federation", "Federation", "Federated model linking", "implemented",
       "Trusted tools and models linked in one governed solution; models talking to each other.",
       "Model registry, versions, typed data contracts, explicit input/output bindings, dataset-mediated dependency graph, one GraphRun per execution.",
       routes=("/api/federation/map", "/api/graph"), modules=("services/federation.py", "services/orchestration.py"),
       tests=("test_d16_federation_integrity.py",)),
    _c("propagation", "Federation", "Change propagation", "implemented",
       "Cascading impacts of a change across dependent models.",
       "Hash-based change detection → ChangeEvent → downstream closure → one propagation GraphRun.",
       routes=("/api/changes/propagate",), tests=("test_d9_change_propagation.py",)),
    _c("lineage", "Federation", "Results, lineage and provenance", "implemented",
       "Traceable, trusted data.",
       "Server-recorded results per step, lineage edges to source change events, provenance of every current value.",
       routes=("/api/lineage/{result_id}", "/api/catalog"), tests=("test_d10_results_lineage.py",)),
    _c("scenarios", "Decision support", "Baseline and scenario comparison", "implemented",
       "Complex scenario modelling.",
       "Read-only scenario runs over the same engine; metric-by-metric comparison of two exact runs.",
       routes=("/api/scenarios/{scenario_id}/comparison",), tests=("test_d19_scenarios_baseline.py",)),
    _c("why", "Decision support", "Why a result changed", "implemented",
       "Contextualised insight for decision-makers.",
       "Field-level causal trace from bindings and recorded results; semantic activity feed.",
       routes=("/api/scenarios/{scenario_id}/explanation", "/api/activity"), tests=("test_d25_product.py",)),
    _c("react", "Decision support", "React decision-support product", "implemented",
       "Contextualised dashboards.",
       "React + TypeScript product served same-origin at /app; legacy pages kept at /ui as rollback.",
       routes=("/app/{path:path}",), tests=("test_d25_product.py", "frontend e2e")),
    # --- Network & governance ------------------------------------------------------------
    _c("private_zones", "Network", "Home-port private modelling zones", "implemented",
       "Dedicated home-port modelling zones.",
       "Every record belongs to one organization; the database session of each request is scoped so reads are filtered and writes stamped at the ORM layer. Route-coverage tests attack every API route with another organization's IDs (SQLite and PostgreSQL).",
       limits="Demo organizations are synthetic.", routes=("/api/workspace",), tests=("test_d26_security.py",)),
    _c("hubs", "Network", "Regional, national and network hubs", "implemented",
       "Group, regional and global inter-port collaboration.",
       "Organization hierarchy (port → regional → national → network); hub views built only from outputs approved for that hub, with coverage; hub aggregates flow into the hub's own federation.",
       limits="Aggregates are sums/means of shared KPIs; no network-level forecasting.",
       routes=("/api/network/hub", "/api/network/hierarchy"), tests=("test_d27_network.py",)),
    _c("sharing", "Network", "Controlled sharing of approved outputs", "implemented",
       "Controlled access to shared data; data collaboration agreements and governance.",
       "Approvals per field with audience organization, purpose, expiry, revocation (with reason and who), explicit run-result sharing; denied exposure is audited; scenario results are never shared implicitly.",
       limits="Purpose is recorded and shown with every exposure, not machine-matched to a consumer-declared purpose. Not a legal data-sharing agreement workflow.",
       routes=("/api/approved-outputs", "/api/network/exposure"), tests=("test_d26_security.py", "test_d27_network.py")),
    _c("cases", "Network", "Collaborative decision zones", "implemented",
       "Shared private zones for collaborative decision-making.",
       "Collaboration cases with host and members; outputs shared into a case are visible only to its members while it is open; case audit trail.",
       routes=("/api/cases", "/api/cases/{case_id}/outputs"), tests=("test_d27_network.py",)),
    _c("auth", "Security", "Authentication and authorization", "implemented",
       "Secure operation.",
       "Built-in accounts (scrypt), server-side sessions, CSRF protection, per-organization roles, central route policy, rate limits, audit.",
       limits="No SSO/OIDC, MFA or email password reset; rate limits are per process.",
       routes=("/api/auth/login", "/api/session", "/api/audit"), tests=("test_d26_security.py",)),
    # --- Model ecosystem --------------------------------------------------------------------
    _c("library", "Models", "Model library", "implemented",
       "Access to a library of models and third-party services.",
       "Cards with provider, provider kind, domain, versions, contracts with units, execution method, governance and calibration status, provenance, and a compatibility check from real bindings; installable reviewed packs.",
       routes=("/api/model-library", "/api/model-library/packs"), tests=("test_d27_planning.py",)),
    _c("third_party", "Models", "Trusted third-party model integrations", "not_implemented",
       "Third-party and partner models available through the platform.",
       "The registry can represent a third-party or open-source provider, but no external model is connected.",
       limits="An integration needs a vetted adapter, a contract, and tests against the real service."),
    # --- Modelling -------------------------------------------------------------------------
    _c("port_models", "Modelling", "Port planning models", "illustrative",
       "Port-specific operational and planning models.",
       "Demand and vessel calls, berths and cranes with M/M/c queueing, yard, gate, turnaround, congestion, energy, emissions, resilience headroom, cost and investment — textbook relations.",
       limits="Uncalibrated, synthetic parameters; M/M/c is pessimistic for berth service.",
       modules=("library/port_planning.py",), tests=("test_d27_planning.py",)),
    _c("cross_domain", "Modelling", "Cross-domain modelling", "illustrative",
       "Holistic modelling of natural, social and industrial systems.",
       "One federation spanning natural (weather, tide), operational, economic (jobs, value added), environmental (NOx, PM2.5) and social (exposure, traffic) models.",
       limits="Deliberately simple equations; not validated science.", modules=("library/port_city.py",),
       tests=("test_d27_planning.py",)),
    _c("calibration", "Modelling", "Calibration against real port data", "external_dependency",
       "Trusted, calibrated models.", "Not performed: no real port data is available here.",
       limits="Needs real data and a documented calibration method."),
    # --- Data ------------------------------------------------------------------------------
    _c("excel", "Data", "Excel ingestion", "implemented",
       "Excel as an assumption source.",
       "Upload → preview → mapping → dry-run validation → commit → change detection → propagation → provenance.",
       limits="Upload-based, not synchronised. .xlsx only; no formulas, macros or external links.",
       routes=("/api/ingestions/excel", "/api/ingestions/excel/validate"), tests=("test_d18_excel_integration.py",)),
    _c("csv", "Data", "CSV upload connector", "implemented", "Linking trusted data sources.",
       "field,value CSV onto one dataset through the governed path.", routes=("/api/connectors/{source_id}/csv",),
       tests=("test_d27_connectors.py",)),
    _c("rest", "Data", "REST API polling", "implemented", "Linking trusted data sources.",
       "HTTPS JSON with exact host allowlist, public-address check, no redirects, size/time limits, secrets only from CONNECTOR_SECRET_* variables; optional single-instance scheduler.",
       limits="Tested against a mocked endpoint; no real external feed is configured.",
       routes=("/api/connectors/{source_id}/poll",), tests=("test_d27_connectors.py",)),
    _c("webhook", "Data", "Signed webhooks", "implemented", "Event-driven data.",
       "HMAC-SHA256 signatures with derived per-source keys, 5-minute replay window, idempotent delivery IDs.",
       routes=("/api/webhooks/{source_id}",), tests=("test_d27_connectors.py",)),
    _c("telemetry", "Data", "Telemetry and event ingestion", "implemented", "Live sensor data.",
       "Idempotent readings, windowed aggregation into a dataset, propagation to a live operations model.",
       limits="No real sensors are connected; the demo feed is a labelled simulator.",
       routes=("/api/connectors/{source_id}/readings",), tests=("test_d27_connectors.py",)),
    _c("live", "Data", "Live refresh", "implemented", "A holistic live view.",
       "Server-sent events of new activity per organization (database polling), React refreshes on arrival.",
       limits="Refresh of platform activity, not a sensor stream; ~2 s polling.", routes=("/api/events/stream",),
       tests=("test_d27_connectors.py",)),
    _c("db_connector", "Data", "External database connector", "not_implemented", "Linking trusted data sources.",
       "Not built.", limits="Needs a vetted read-only driver, a credential vault and a query allowlist."),
    _c("object_storage", "Data", "Object storage / Parquet", "not_implemented", "Linking trusted data sources.",
       "Not built.", limits="Needs a bucket and credentials (external), and pyarrow for Parquet."),
    # --- Planning & optimization -------------------------------------------------------------
    _c("master_planning", "Planning", "Dynamic master planning", "implemented",
       "Dynamic master planning.",
       "Multi-period plans with growth and interpolated assumptions, shocks, investments with commissioning years, branches and plan comparison; every period is an engine run with results and lineage.",
       limits="Models are illustrative; discounting uses a stated step approximation.",
       routes=("/api/plans", "/api/plans/compare"), tests=("test_d27_planning.py",)),
    _c("optimization", "Planning", "Optimization", "implemented",
       "Proactive, adaptive operational optimisation.",
       "Declared decision variables, constraints and objectives; exhaustive enumeration (exact for the declared grid), Pareto set, weighted pick attributed to declared weights, verified by re-running through the engine.",
       limits="Discrete grids up to 5,000 combinations; no continuous or large-scale solver; not operational real-time optimisation.",
       routes=("/api/optimization", "/api/optimization/{study_id}/run"), tests=("test_d27_planning.py",)),
    # --- Application portfolio ---------------------------------------------------------------
    _c("app_operations", "Applications", "Dynamic port operations", "partial",
       "Dynamic port operations.",
       "Live operations pulse over telemetry windows (simulated in the demo) and berth queueing in planning.",
       limits="No real operational feeds."),
    _c("app_master_plan", "Applications", "Dynamic master planning", "implemented",
       "Dynamic master planning.", "See master planning (models illustrative)."),
    _c("app_assets", "Applications", "Strategic asset management", "partial",
       "Strategic asset management.", "Investment timing and capacity alternatives through optimization.",
       limits="No asset condition, lifecycle or maintenance data."),
    _c("app_port_city", "Applications", "Interconnected port city", "illustrative",
       "Interconnected port city.", "Cross-domain port-city federation."),
    _c("app_supply_chain", "Applications", "Supply-chain modelling", "not_implemented",
       "Supply-chain modelling.", "Only port-level aggregation across the network; no hinterland or carrier network model."),
    _c("app_geopolitics", "Applications", "Geopolitical-risk impacts on trade", "illustrative",
       "Geopolitical-risk impacts on trade.", "Demand shocks in master plans.",
       limits="Shocks are user-declared factors, not a trade model."),
    _c("app_routing", "Applications", "Route optimisation", "not_implemented",
       "Proactive and adaptive route optimisation.", "Not built."),
    _c("app_resilience", "Applications", "Resilience and climate impacts", "illustrative",
       "Resilience and climate impacts.", "Disruption headroom in planning; storm and tide access in the port-city pack."),
    _c("app_air", "Applications", "Air quality and emissions", "illustrative",
       "Air quality and emissions.", "CO₂ in planning; NOx and PM2.5 with community exposure in the port-city pack."),
    _c("app_capital", "Applications", "Capital allocation and investment planning", "implemented",
       "Capital allocation.", "Investment alternatives, NPV and trade-offs through optimization (models illustrative)."),
    # --- Open-source integrations (D28) ------------------------------------------------------
    # Each is optional and off by default; the federation semantics stay in the platform.
    _c("oss_airflow", "Integrations", "Apache Airflow executor", "implemented",
       "Model linking via an orchestrator (Airflow).",
       "Optional external executor: the platform creates the GraphRun, triggers one stable DAG, and the DAG calls back; results, lineage and status stay platform-owned.",
       limits="Off by default (AIRFLOW_ENABLED). Tested against a fake Airflow transport; no Airflow runs in this environment.",
       routes=("/api/executions/{run_id}/airflow-callback",),
       modules=("execution/airflow_executor.py", "execution/dags/platform_graphrun_dag.py"),
       tests=("test_d21_airflow_executor.py",)),
    _c("oss_dagster", "Integrations", "Dagster executor and asset graph", "implemented",
       "Model linking with data tracked between models (Dagster).",
       "Optional external executor over Dagster's GraphQL API with the Airflow contract; a Dagster code location whose op calls back, plus the federation as a Dagster asset graph with each computed dataset recorded as an asset materialization.",
       limits="Off by default (DAGSTER_ENABLED). Verified end to end against a real Dagster 1.13.24 (launch → callback → results → reconcile → materializations); Dagster is not a platform dependency.",
       routes=("/api/executions/{run_id}/dagster-callback",),
       modules=("execution/dagster_executor.py", "../../dagster_platform/definitions.py"),
       tests=("test_d28_dagster_executor.py",)),
    _c("oss_airbyte", "Integrations", "Airbyte connector", "partial",
       "Excel and other sources synced into the hub (Airbyte).",
       "Airbyte syncs into a staging schema; the airbyte connector ingests the newest synced rows through contract validation, change detection and propagation, and can trigger and follow Airbyte sync jobs. Per-organization stream prefixes isolate tenants.",
       limits="Not exercised against a running Airbyte (it needs Docker/Kubernetes): tested with tables in Airbyte's destination layout and a fake Airbyte API.",
       routes=("/api/connectors/{source_id}/airbyte/ingest", "/api/connectors/{source_id}/airbyte/sync"),
       modules=("connectors/airbyte.py",), tests=("test_d28_airbyte_connector.py",)),
    _c("oss_evidence", "Integrations", "Evidence reports (BI as code)", "implemented",
       "Markdown reports that update when the underlying models change (Evidence).",
       "An Evidence project (overview, ripple effects, master plan, scenarios, shared outputs) whose only data source is a governed export taken through the platform API as a signed-in user.",
       limits="Reports refresh when the export is re-run (batch), not live. Evidence 40.1.8 build verified with strict mode and in a browser; Superset deliberately not added (direct database access would bypass governance).",
       modules=("../../scripts/export_platform_data.py", "../../evidence/pages"),
       tests=("test_d28_evidence_export.py",)),
    # --- Operations ------------------------------------------------------------------------
    _c("postgres", "Operations", "Shared PostgreSQL (Supabase)", "partial",
       "Secure cloud operation.",
       "Full test suite and Alembic lifecycle verified on a disposable local PostgreSQL 16; Supabase pooling, TLS and Data-API lockdown configured.",
       limits="Never run against the Supabase project itself (it must not be used for tests).",
       routes=("/ready",), tests=("test_d15_postgresql.py", "test_d26_security.py")),
    _c("deployment", "Operations", "Public deployment", "external_dependency", "Browser-accessible service.",
       "Deployment package and documentation are complete.",
       limits="No hosting account here: no public URL exists, and the container image was not built."),
    _c("observability", "Operations", "Health, readiness and audit", "partial", "Holistic live view of operations.",
       "Liveness, readiness (database + migration head), request IDs, access logs, audit trail, build self-report.",
       limits="No metrics or tracing backend.", routes=("/health", "/ready", "/api/build-info")),
]

STATUS_ORDER = ("implemented", "partial", "illustrative", "unverified", "external_dependency", "not_implemented")


def registry(app: Any) -> dict[str, Any]:
    from backend.app.buildinfo import route_paths

    served = route_paths(app)
    rows = []
    for c in CAPABILITIES:
        row = dict(c)
        missing = [r for r in c["evidence_routes"] if r not in served]
        if missing:
            row["status"] = "unverified"
            row["limits"] = (row["limits"] + " " if row["limits"] else "") + f"Evidence routes not served: {missing}."
        rows.append(row)
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in STATUS_ORDER}
    return {"capabilities": rows, "counts": counts, "statuses": list(STATUS_ORDER),
            "statement": "Independent implementation inspired by publicly described capabilities. "
                         "Not a reproduction of any proprietary system."}
