"""Executor abstraction — D21.

The platform stays authoritative for models, datasets, the graph, GraphRuns, results,
lineage and change events. An *Executor* only decides HOW a platform-created GraphRun is
carried out:

  - InProcessExecutor (default): the GraphRun is executed synchronously, inline, by the
    existing GraphOrchestrationService machinery. This is the unchanged D8/D16 path.
  - AirflowExecutor (optional, external): the GraphRun is created up front, then a stable
    Airflow DAG run is triggered to carry it out asynchronously. Airflow computes nothing
    and persists nothing — when the DAG fires it calls back into the platform, which runs
    the very same in-process execution. Airflow is only an external trigger/scheduler.

External executors are asynchronous: submit() returns while the run is still "running";
poll_status()/fetch_failure() reconcile the platform run status from the external system.

Terminal platform run statuses are "succeeded" and "failed"; "running"/"requested" are
non-terminal. Status is always platform-owned; external states are mapped into it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import NamedTuple

from backend.app.persistence.database import ExecutionRun

# ---------------------------------------------------------------------------
# Status vocabulary
# ---------------------------------------------------------------------------

IN_PROCESS = "in_process"
AIRFLOW = "airflow"
DAGSTER = "dagster"  # D28

RUNNING = "running"
REQUESTED = "requested"
SUCCEEDED = "succeeded"
FAILED = "failed"

TERMINAL_STATUSES: frozenset[str] = frozenset({SUCCEEDED, FAILED})


def is_terminal(status: str | None) -> bool:
    return status in TERMINAL_STATUSES


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class ExecutorSelectionError(Exception):
    """An unknown executor was requested."""


class ExecutorConfigError(Exception):
    """An executor was explicitly requested but is not validly configured/available."""


# ---------------------------------------------------------------------------
# Return type
# ---------------------------------------------------------------------------

class SubmitResult(NamedTuple):
    external_ref: str | None   # external system's run id (Airflow dag_run_id); None inline
    status: str                # platform run status immediately after submit


# ---------------------------------------------------------------------------
# Executor contract
# ---------------------------------------------------------------------------

class Executor(ABC):
    """Base executor. Non-external executors run inline via the orchestration service."""

    name: str = IN_PROCESS
    is_external: bool = False


class ExternalExecutor(Executor):
    """An executor that carries execution out asynchronously in an external system."""

    is_external = True

    @abstractmethod
    def submit(self, run: ExecutionRun, order: list[str]) -> SubmitResult:
        """Trigger external execution of an already-created GraphRun.

        Implementations record their external correlation reference on the run
        (e.g. into ExecutionRun.audit_json) and return it in the SubmitResult.
        """

    @abstractmethod
    def poll_status(self, run: ExecutionRun) -> str:
        """Return the current platform-mapped status of the external run."""

    @abstractmethod
    def fetch_failure(self, run: ExecutionRun) -> str | None:
        """Return a human-readable failure reason if the external run failed."""


class InProcessExecutor(Executor):
    """Default executor: the orchestration service runs the plan inline and synchronously."""

    name = IN_PROCESS
    is_external = False
