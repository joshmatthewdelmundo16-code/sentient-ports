"""Executor selection — D21.

Selection rules (D21 correction #1 — no silent fallback when Airflow is explicitly chosen):

  - executor unspecified            → InProcessExecutor (the default; Airflow never implicit)
  - "in_process"                    → InProcessExecutor
  - "airflow" + valid config        → AirflowExecutor
  - "airflow" + missing/unreachable → AirflowConfigError (clear configuration/availability error)
  - anything else                   → ExecutorSelectionError
"""

from __future__ import annotations

from backend.app.execution.airflow_client import AirflowConfig
from backend.app.execution.airflow_executor import AirflowExecutor
from backend.app.execution.executor import (
    AIRFLOW,
    IN_PROCESS,
    Executor,
    ExecutorSelectionError,
    InProcessExecutor,
)


def select_executor(requested: str | None) -> Executor:
    if requested is None or requested == IN_PROCESS:
        return InProcessExecutor()
    if requested == AIRFLOW:
        config = AirflowConfig.from_env()
        config.validate()  # raises AirflowConfigError if not validly configured/available
        return AirflowExecutor(config)
    raise ExecutorSelectionError(
        f"Unknown executor {requested!r}; expected {IN_PROCESS!r} or {AIRFLOW!r}."
    )
