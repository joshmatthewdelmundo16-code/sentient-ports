"""Maps service-layer errors to HTTP errors. Anything unmapped propagates as a 500."""

from __future__ import annotations

from fastapi import HTTPException

from backend.app.execution.airflow_client import AirflowConfigError, AirflowTransportError
from backend.app.execution.dagster_client import DagsterConfigError, DagsterTransportError
from backend.app.execution.executor import ExecutorConfigError, ExecutorSelectionError
from backend.app.services.change_propagation import PropagationCycleError
from backend.app.services.contract_validation import ContractViolationError
from backend.app.services.governance import (
    ApprovalValidationError,
    ApprovedOutputNotFoundError,
    DuplicateApprovalError,
    DuplicateParticipantError,
    ParticipantInactiveError,
    ParticipantNotFoundError,
    ParticipantValidationError,
)
from backend.app.services.dataset_values import (
    DatasetOwnershipError,
    DatasetValueNotFoundError,
    DatasetValueSerializationError,
)
from backend.app.services.execution import (
    ExecutionAdapterNotFoundError,
    ExecutionVersionNotFoundError,
)
from backend.app.services.federation import VersionNotExecutableError
from backend.app.services.orchestration import (
    DirectInputNotAcceptedError,
    OrchestrationCycleError,
    OrchestrationTargetNotFoundError,
)
from backend.app.services.results_lineage import ResultPersistenceError
from backend.app.services.scenario_comparison import ComparisonRunNotFoundError
from backend.app.services.scenarios import (
    BaselineNotFoundError,
    DuplicateBaselineError,
    DuplicateScenarioError,
    ScenarioExecutionError,
    ScenarioNotFoundError,
    ScenarioOverrideError,
)

_STATUS: list[tuple[type[Exception], int]] = [
    (OrchestrationTargetNotFoundError, 404),
    (ExecutionVersionNotFoundError, 404),
    (DatasetValueNotFoundError, 404),
    (BaselineNotFoundError, 404),
    (ScenarioNotFoundError, 404),
    (ComparisonRunNotFoundError, 404),
    (VersionNotExecutableError, 409),
    (DatasetOwnershipError, 409),
    (DuplicateBaselineError, 409),
    (DuplicateScenarioError, 409),
    (OrchestrationCycleError, 422),
    (PropagationCycleError, 422),
    (ExecutionAdapterNotFoundError, 422),
    (DirectInputNotAcceptedError, 422),
    (DatasetValueSerializationError, 422),
    (ScenarioOverrideError, 422),
    (ScenarioExecutionError, 422),
    (ExecutorSelectionError, 422),
    # Airflow optional executor (D21): configuration/availability vs transport failures.
    (AirflowConfigError, 503),
    (ExecutorConfigError, 503),
    (AirflowTransportError, 502),
    # Dagster optional executor (D28): same split.
    (DagsterConfigError, 503),
    (DagsterTransportError, 502),
    # Governance (D22).
    (ParticipantNotFoundError, 404),
    (ApprovedOutputNotFoundError, 404),
    (DuplicateParticipantError, 409),
    (DuplicateApprovalError, 409),
    (ParticipantInactiveError, 409),
    (ParticipantValidationError, 422),
    (ApprovalValidationError, 422),
    (ResultPersistenceError, 500),
]


def to_http(exc: Exception) -> HTTPException | None:
    if isinstance(exc, ContractViolationError):
        return HTTPException(status_code=422, detail={
            "error": str(exc),
            "dataset_id": exc.dataset_id,
            "dataset_name": exc.dataset_name,
            "contract_semver": exc.contract_semver,
            "violations": [{"field": v.field, "message": v.message} for v in exc.violations],
        })
    for exc_type, status in _STATUS:
        if isinstance(exc, exc_type):
            return HTTPException(status_code=status, detail=str(exc))
    return None


MAPPED_ERRORS: tuple[type[Exception], ...] = (ContractViolationError,) + tuple(t for t, _ in _STATUS)
