"""Stable Airflow DAG — D21 (optional).

ONE stable DAG that carries out a platform-owned GraphRun. It authors nothing per-run and
computes no models: on trigger it calls back into the platform, which runs the same
in-process execution and owns all results/lineage. This keeps Airflow a pure external
trigger/scheduler (no formulas, no persistence in Airflow).

This module is intentionally NOT imported by the platform or its test suite — Airflow is not
installed in the development environment. Deploy it into an existing Airflow 3.x `dags/`
folder only if you actually run Airflow. All Airflow imports are local so importing this file
without Airflow fails loudly at deploy time, never at platform import time.

Trigger conf (minimal, D21 correction #3): {"graph_run_id": "<platform GraphRun id>"}.
The dag_run_id is set by the platform to "graphrun__<graph_run_id>" for correlation.

Configuration (Airflow Variables / env on the Airflow side — never in DAG params/conf):
  PLATFORM_CALLBACK_BASE_URL   e.g. http://platform:8000
  PLATFORM_CALLBACK_TOKEN      optional bearer token for the callback (if the platform adds auth)
"""

from __future__ import annotations

import os
from datetime import datetime


def _callback(**context) -> dict:
    """Single task: correlate and ask the platform to carry out the GraphRun."""
    import json
    import urllib.request

    conf = (context.get("dag_run").conf or {}) if context.get("dag_run") else {}
    graph_run_id = conf.get("graph_run_id")
    if not graph_run_id:
        raise ValueError("dag_run.conf.graph_run_id is required.")
    dag_run_id = context["dag_run"].run_id

    base = os.environ.get("PLATFORM_CALLBACK_BASE_URL", "").rstrip("/")
    if not base:
        raise ValueError("PLATFORM_CALLBACK_BASE_URL is not set on the Airflow side.")
    url = f"{base}/api/executions/{graph_run_id}/airflow-callback"

    headers = {"Content-Type": "application/json"}
    token = os.environ.get("PLATFORM_CALLBACK_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    body = json.dumps({"dag_run_id": dag_run_id}).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 (trusted internal URL)
        payload = json.loads(resp.read().decode())
    if not payload.get("success") and payload.get("status") == "failed":
        raise RuntimeError(f"Platform GraphRun {graph_run_id} failed: {payload}")
    return payload


def _build_dag():
    from airflow import DAG
    from airflow.operators.python import PythonOperator

    with DAG(
        dag_id=os.environ.get("AIRFLOW_DAG_ID", "platform_graphrun"),
        description="Carry out a platform-owned GraphRun (platform owns compute/results).",
        schedule=None,               # triggered externally by the platform only
        start_date=datetime(2024, 1, 1),
        catchup=False,
        tags=["platform", "graphrun"],
    ) as dag:
        PythonOperator(task_id="run_graphrun_callback", python_callable=_callback)
    return dag


# Airflow discovers a module-level DAG object. Guarded so importing this file without Airflow
# (e.g. accidental import in the platform env) does not crash — it simply exposes no DAG.
try:  # pragma: no cover - only meaningful inside an Airflow deployment
    dag = _build_dag()
except Exception:  # noqa: BLE001
    dag = None
