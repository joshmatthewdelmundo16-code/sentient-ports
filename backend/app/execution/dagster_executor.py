"""DagsterExecutor — D28 optional external executor.

Launches the stable Dagster job ``platform_graphrun_job`` to carry out an already-created
platform GraphRun, and reconciles the platform run status from Dagster's run status. Dagster
never computes a model nor persists a result: its single op calls back into the platform, which
runs the same in-process execution. Results and lineage stay platform-owned — the same contract
as the D21 AirflowExecutor.

Correlation:
  - run config sent:  {"ops": {"carry_out_graph_run": {"config": {"graph_run_id": run_id}}}}
  - run tag:          platform/graph_run_id = run_id  (searchable in the Dagster UI)
  - Dagster's run id is stored on ExecutionRun.audit_json; the callback must present it.
"""

from __future__ import annotations

import json
from typing import Any

from backend.app.execution.dagster_client import (
    DagsterConfig,
    DagsterTransport,
    DagsterTransportError,
    HttpxDagsterTransport,
)
from backend.app.execution.executor import DAGSTER, FAILED, RUNNING, SUCCEEDED, ExternalExecutor, SubmitResult
from backend.app.persistence.database import ExecutionRun

OP_NAME = "carry_out_graph_run"
TAG_KEY = "platform/graph_run_id"

# DagsterRunStatus → platform run status. Anything unknown stays non-terminal.
DAGSTER_STATUS_TO_PLATFORM: dict[str, str] = {
    "QUEUED": RUNNING,
    "NOT_STARTED": RUNNING,
    "MANAGED": RUNNING,
    "STARTING": RUNNING,
    "STARTED": RUNNING,
    "CANCELING": RUNNING,
    "SUCCESS": SUCCEEDED,
    "FAILURE": FAILED,
    "CANCELED": FAILED,
}

_AUDIT_KEY = "dagster_run_id"


def run_config_for(run_id: str) -> dict[str, Any]:
    return {"ops": {OP_NAME: {"config": {"graph_run_id": run_id}}}}


class DagsterExecutor(ExternalExecutor):
    name = DAGSTER

    def __init__(self, config: DagsterConfig, transport: DagsterTransport | None = None) -> None:
        self._cfg = config
        self._transport: DagsterTransport = transport or HttpxDagsterTransport(config)

    @staticmethod
    def read_external_ref(run: ExecutionRun) -> str | None:
        if not run.audit_json:
            return None
        try:
            return json.loads(run.audit_json).get(_AUDIT_KEY)
        except (json.JSONDecodeError, AttributeError):
            return None

    def _write_external_ref(self, run: ExecutionRun, dagster_run_id: str) -> None:
        blob: dict[str, Any] = {}
        if run.audit_json:
            try:
                blob = json.loads(run.audit_json)
            except json.JSONDecodeError:
                blob = {}
        blob.update({"executor": self.name, "dagster_job": self._cfg.job_name, _AUDIT_KEY: dagster_run_id})
        run.audit_json = json.dumps(blob)

    def submit(self, run: ExecutionRun, order: list[str]) -> SubmitResult:
        # Minimal payload: the correlation id only — no credentials, no business data.
        dagster_run_id = self._transport.launch_run(
            self._cfg.selector(), run_config_for(run.id), {TAG_KEY: run.id})
        self._write_external_ref(run, dagster_run_id)
        return SubmitResult(external_ref=dagster_run_id, status=RUNNING)

    def poll_status(self, run: ExecutionRun) -> str:
        ref = self.read_external_ref(run)
        if not ref:
            raise DagsterTransportError(f"GraphRun {run.id!r} has no stored Dagster run id to poll.")
        return DAGSTER_STATUS_TO_PLATFORM.get(self._transport.get_run_status(ref), RUNNING)

    def fetch_failure(self, run: ExecutionRun) -> str | None:
        ref = self.read_external_ref(run)
        return f"Dagster run {ref!r} failed or was canceled." if ref else "Dagster run failed."
