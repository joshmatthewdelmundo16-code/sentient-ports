"""Executor selection — D21, extended in D28.

Selection rules (D21 correction #1 — no silent fallback when an external executor is chosen):

  - executor unspecified            → InProcessExecutor (the default; externals never implicit)
  - "in_process"                    → InProcessExecutor
  - "airflow" + valid config        → AirflowExecutor
  - "dagster" + valid config        → DagsterExecutor (D28)
  - external + missing config       → AirflowConfigError / DagsterConfigError
  - anything else                   → ExecutorSelectionError
"""

from __future__ import annotations

from backend.app.execution.airflow_client import AirflowConfig
from backend.app.execution.airflow_executor import AirflowExecutor
from backend.app.execution.dagster_client import DagsterConfig
from backend.app.execution.dagster_executor import DagsterExecutor
from backend.app.execution.executor import (
    AIRFLOW,
    DAGSTER,
    IN_PROCESS,
    Executor,
    ExecutorSelectionError,
    InProcessExecutor,
)

KNOWN_EXECUTORS = (IN_PROCESS, AIRFLOW, DAGSTER)


def select_executor(requested: str | None) -> Executor:
    if requested is None or requested == IN_PROCESS:
        return InProcessExecutor()
    if requested == AIRFLOW:
        config = AirflowConfig.from_env()
        config.validate()  # raises AirflowConfigError if not validly configured/available
        return AirflowExecutor(config)
    if requested == DAGSTER:
        dconfig = DagsterConfig.from_env()
        dconfig.validate()  # raises DagsterConfigError if not validly configured/available
        return DagsterExecutor(dconfig)
    raise ExecutorSelectionError(
        f"Unknown executor {requested!r}; expected one of {', '.join(repr(e) for e in KNOWN_EXECUTORS)}."
    )
