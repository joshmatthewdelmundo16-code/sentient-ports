"""AirflowExecutor — D21 optional external executor.

Triggers a stable Airflow DAG run to carry out an already-created platform GraphRun, and
reconciles the platform run status from Airflow's dag-run state. Airflow never computes a
model nor persists a result: when the DAG fires it calls back into the platform, which runs
the same in-process execution. Results and lineage stay platform-owned.

Correlation (D21 correction #3 — minimal payload):
  - platform GraphRun id  →  Airflow dag_run_id  = "graphrun__{run_id}"
  - the only conf sent is  {"graph_run_id": run_id}
The dag_run_id is stored on ExecutionRun.audit_json so callbacks and polling can correlate.
"""

from __future__ import annotations

import json
from typing import Any

from backend.app.execution.airflow_client import (
    AirflowConfig,
    AirflowTransport,
    AirflowTransportError,
    HttpxAirflowTransport,
)
from backend.app.execution.executor import (
    FAILED,
    RUNNING,
    ExternalExecutor,
    SubmitResult,
)
from backend.app.persistence.database import ExecutionRun

# Airflow dag-run state → platform run status.
AIRFLOW_STATE_TO_PLATFORM: dict[str, str] = {
    "queued": RUNNING,
    "running": RUNNING,
    "success": "succeeded",
    "failed": FAILED,
}

_AUDIT_KEY = "airflow_dag_run_id"


def dag_run_id_for(run_id: str) -> str:
    return f"graphrun__{run_id}"


class AirflowExecutor(ExternalExecutor):
    name = "airflow"

    def __init__(self, config: AirflowConfig, transport: AirflowTransport | None = None) -> None:
        self._cfg = config
        self._transport: AirflowTransport = transport or HttpxAirflowTransport(config)

    # ------------------------------------------------------------------
    # Correlation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def read_external_ref(run: ExecutionRun) -> str | None:
        if not run.audit_json:
            return None
        try:
            return json.loads(run.audit_json).get(_AUDIT_KEY)
        except (json.JSONDecodeError, AttributeError):
            return None

    def _write_external_ref(self, run: ExecutionRun, dag_run_id: str) -> None:
        blob: dict[str, Any] = {}
        if run.audit_json:
            try:
                blob = json.loads(run.audit_json)
            except json.JSONDecodeError:
                blob = {}
        blob.update({"executor": self.name, "dag_id": self._cfg.dag_id, _AUDIT_KEY: dag_run_id})
        run.audit_json = json.dumps(blob)

    # ------------------------------------------------------------------
    # ExternalExecutor contract
    # ------------------------------------------------------------------

    def submit(self, run: ExecutionRun, order: list[str]) -> SubmitResult:
        dag_run_id = dag_run_id_for(run.id)
        conf = {"graph_run_id": run.id}  # minimal, no credentials, no business data
        self._transport.trigger_dag_run(self._cfg.dag_id, dag_run_id, conf)
        self._write_external_ref(run, dag_run_id)
        return SubmitResult(external_ref=dag_run_id, status=RUNNING)

    def poll_status(self, run: ExecutionRun) -> str:
        ref = self.read_external_ref(run)
        if not ref:
            raise AirflowTransportError(
                f"GraphRun {run.id!r} has no stored Airflow dag_run_id to poll."
            )
        state = self._transport.get_dag_run_state(self._cfg.dag_id, ref)
        return AIRFLOW_STATE_TO_PLATFORM.get(state, RUNNING)

    def fetch_failure(self, run: ExecutionRun) -> str | None:
        ref = self.read_external_ref(run)
        return f"Airflow dag run {ref!r} reported failure." if ref else "Airflow run failed."
