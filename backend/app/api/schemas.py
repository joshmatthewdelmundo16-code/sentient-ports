"""API request/response schemas (D11, updated for D16 federation integrity)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Shared config
# ---------------------------------------------------------------------------

class _ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class _Request(BaseModel):
    # Unknown fields are rejected so removed client-owned fields (e.g. the old
    # version_to_output_dataset map) fail loudly instead of being silently ignored.
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Model Registry responses
# ---------------------------------------------------------------------------

class ModelOut(_ORMModel):
    id: str
    name: str
    owner: str
    model_type: str
    status: str
    description: str | None = None
    created_at: datetime


class ModelVersionOut(_ORMModel):
    id: str
    model_id: str
    semver: str
    is_active: bool
    inputs_spec: str | None = None
    outputs_spec: str | None = None
    execution_entrypoint: str | None = None
    adapter_type: str | None = None
    adapter_config: str | None = None
    created_at: datetime


# ---------------------------------------------------------------------------
# Data Contract responses
# ---------------------------------------------------------------------------

class ContractOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    dataset_id: str
    semver: str
    schema_json: str
    created_at: datetime


# ---------------------------------------------------------------------------
# Dependency / Graph responses
# ---------------------------------------------------------------------------

class DependencyOut(_ORMModel):
    id: str
    producer_version_id: str
    output_dataset_id: str
    consumer_version_id: str
    input_dataset_id: str
    dependency_kind: str


class GraphOut(BaseModel):
    execution_order: list[str]
    dependencies: list[DependencyOut]


# ---------------------------------------------------------------------------
# Dataset responses
# ---------------------------------------------------------------------------

class ContractFieldOut(BaseModel):
    name: str
    type: str
    nullable: bool
    required: bool
    min: float | None = None
    max: float | None = None
    unit: str | None = None


class DatasetOut(BaseModel):
    id: str
    name: str
    description: str | None = None
    current_value: Any = None
    current_hash: str | None = None
    updated_at: datetime | None = None
    producer_version_ids: list[str]
    consumer_version_ids: list[str]
    contract_semver: str | None = None
    contract_fields: list[ContractFieldOut] = Field(default_factory=list)


class ChangeEventOut(_ORMModel):
    id: str
    dataset_id: str
    run_id: str | None = None
    source_type: str | None = None
    source_ref: str | None = None
    source_version_id: str | None = None
    produced_by_run_id: str | None = None
    result_id: str | None = None
    old_value_json: str | None = None
    new_value_json: str | None = None
    triggered_by: str | None = None
    created_at: datetime


# ---------------------------------------------------------------------------
# Execution responses
# ---------------------------------------------------------------------------

class ExecutionRunOut(_ORMModel):
    id: str
    run_kind: str
    executor: str
    status: str
    target_version_id: str | None = None
    trigger_type: str | None = None
    triggered_by: str | None = None
    error_message: str | None = None
    subgraph_json: str | None = None
    scenario_id: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime


class ExecutionStepOut(_ORMModel):
    id: str
    run_id: str
    model_version_id: str | None = None
    step_order: int
    status: str
    input_snapshot: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class ExecutionDetailOut(BaseModel):
    run: ExecutionRunOut
    steps: list[ExecutionStepOut]


class ExecutionTriggerOut(BaseModel):
    run_id: str
    step_id: str
    status: str
    outputs: dict[str, Any] | None = None
    error: str | None = None
    recorded_result_ids: list[str] = Field(default_factory=list)


class GraphStepOut(BaseModel):
    run_id: str
    step_id: str
    status: str
    outputs: dict[str, Any] | None = None
    error: str | None = None


class GraphExecutionOut(BaseModel):
    graph_run_id: str | None = None
    status: str | None = None
    success: bool
    execution_order: list[str]
    step_outcomes: dict[str, GraphStepOut]
    first_failure_version_id: str | None = None
    error: str | None = None
    recorded_result_ids: list[str] = Field(default_factory=list)
    input_change_event_ids: list[str] = Field(default_factory=list)
    published_change_event_ids: list[str] = Field(default_factory=list)
    executor: str = "in_process"
    external_ref: str | None = None


# ---------------------------------------------------------------------------
# Airflow optional executor (D21)
# ---------------------------------------------------------------------------

class AirflowCallbackRequest(_Request):
    """Callback issued by the Airflow DAG task to carry out a submitted GraphRun."""

    dag_run_id: str = Field(
        ..., description="Airflow dag_run_id; must match the run's stored correlation id"
    )


class AirflowCallbackOut(BaseModel):
    graph_run_id: str
    status: str
    success: bool
    already_terminal: bool = False
    recorded_result_ids: list[str] = Field(default_factory=list)
    # D28: names of the datasets this run computed, changed or not — lets an orchestrator such as
    # Dagster record them as asset materializations.
    written_datasets: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Dagster optional executor (D28)
# ---------------------------------------------------------------------------

class DagsterCallbackRequest(_Request):
    """Callback issued by the Dagster op to carry out a submitted GraphRun."""

    dagster_run_id: str = Field(
        ..., description="Dagster run id; must match the run's stored correlation id"
    )


class ExecutionReconcileOut(BaseModel):
    graph_run_id: str
    executor: str
    status: str
    error_message: str | None = None


# ---------------------------------------------------------------------------
# Result responses
# ---------------------------------------------------------------------------

class ResultOut(_ORMModel):
    id: str
    run_id: str
    step_id: str
    dataset_id: str
    model_version_id: str | None = None
    value_json: str
    value_numeric: float | None = None
    value_hash: str | None = None
    created_at: datetime


# ---------------------------------------------------------------------------
# Lineage responses
# ---------------------------------------------------------------------------

class LineageEdgeOut(_ORMModel):
    id: str
    run_id: str
    step_id: str
    source_result_id: str | None = None
    target_result_id: str | None = None
    source_dataset_id: str | None = None
    target_dataset_id: str | None = None
    source_change_event_id: str | None = None
    created_at: datetime


class LineageOut(BaseModel):
    result_id: str
    edges_from: list[LineageEdgeOut]
    edges_to: list[LineageEdgeOut]


# ---------------------------------------------------------------------------
# Propagation responses
# ---------------------------------------------------------------------------

class PropagationOut(BaseModel):
    changed: bool
    change_event_id: str | None = None
    source_dataset_id: str
    source_version_id: str | None = None
    old_value: Any = None
    new_value: Any = None
    graph_run_id: str | None = None
    affected_version_ids: list[str] = Field(default_factory=list)
    execution_order: list[str] = Field(default_factory=list)
    success: bool
    error: str | None = None
    already_processed: bool = False
    recorded_result_ids: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class ExecutionRequest(_Request):
    version_id: str = Field(..., description="ModelVersion ID to execute")
    input_data: dict[str, Any] = Field(
        default_factory=dict,
        description="Direct parameters; only accepted by versions with no declared input datasets",
    )


class GraphExecutionRequest(_Request):
    target_version_id: str = Field(..., description="Terminal ModelVersion ID to execute toward")
    input_data: dict[str, Any] = Field(
        default_factory=dict,
        description="Direct parameters for planned versions that have no declared input datasets",
    )
    dataset_values: dict[str, dict[str, Any]] | None = Field(
        None,
        description="Source dataset values (dataset_id → record) written before the run, "
                    "with contract validation and change detection",
    )
    executor: str | None = Field(
        None,
        description="Optional executor: 'in_process' (default), 'airflow' or 'dagster' (optional "
                    "external executors; each requires its own valid configuration). Scenarios always run "
                    "in-process regardless of this field.",
    )


class IngestionOut(BaseModel):
    ingestion_id: str
    status: str                       # ingested | unchanged | rejected
    content_sha256: str | None = None
    mapping_key: str
    source_name: str
    changed: bool
    dataset_ids: list[str] = Field(default_factory=list)
    change_event_id: str | None = None
    graph_run_id: str | None = None
    error: str | None = None
    values: dict[str, Any] | None = None


class IngestionRunOut(_ORMModel):
    id: str
    source_name: str
    content_sha256: str | None = None
    mapping_key: str
    status: str
    error: str | None = None
    dataset_ids_json: str | None = None
    change_event_id: str | None = None
    graph_run_id: str | None = None
    created_at: datetime


class MappingCellOut(BaseModel):
    worksheet: str
    cell: str
    target_dataset: str
    target_field: str
    expected_type: str


class MappingOut(BaseModel):
    key: str
    description: str
    cells: list[MappingCellOut]


# --- Guided Excel workflow (D24) -------------------------------------------

class FieldPreviewOut(BaseModel):
    """One mapped cell, explained: where it came from and what it would change."""

    worksheet: str
    cell: str
    dataset: str
    field: str
    unit: str | None = None
    expected_type: str
    current_value: Any = None
    new_value: Any = None
    changed: bool
    valid: bool
    message: str | None = None


class WorkbookPreviewOut(BaseModel):
    """Dry-run result. Producing it wrote nothing and propagated nothing."""

    mapping_key: str
    mapping_description: str
    source_name: str
    content_sha256: str
    fields: list[FieldPreviewOut] = Field(default_factory=list)
    valid: bool
    changed_fields: list[str] = Field(default_factory=list)
    would_change: bool
    datasets: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class MappingFieldOut(BaseModel):
    """A mapped field with its contract unit and the platform's current value."""

    worksheet: str
    cell: str
    dataset: str
    field: str
    unit: str | None = None
    expected_type: str
    current_value: Any = None


class MappingPreviewOut(BaseModel):
    """What a mapping reads, and what the platform currently holds for each field."""

    key: str
    description: str
    datasets: list[str] = Field(default_factory=list)
    fields: list[MappingFieldOut] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class PropagationRequest(_Request):
    dataset_id: str = Field(..., description="Source dataset whose value changes")
    value: dict[str, Any] = Field(..., description="New dataset record (validated against its contract)")
    triggered_by: str | None = Field(None, description="Actor/label recorded on the ChangeEvent")
    source_ref: str | None = Field(None, description="Provenance reference recorded on the ChangeEvent")


# ---------------------------------------------------------------------------
# Scenario & Baseline (D19)
# ---------------------------------------------------------------------------

class BaselineCreate(_Request):
    name: str = Field(..., description="Unique baseline name")
    description: str | None = None
    target_version_id: str | None = Field(
        None, description="Terminal ModelVersion ID the baseline executes toward"
    )
    status: str = Field("active", description="Baseline lifecycle status")


class BaselineOut(_ORMModel):
    id: str
    name: str
    description: str | None = None
    status: str
    target_version_id: str | None = None
    baseline_run_id: str | None = None
    created_at: datetime
    updated_at: datetime | None = None


class ScenarioCreate(_Request):
    baseline_id: str = Field(..., description="Baseline this scenario derives from")
    name: str = Field(..., description="Scenario name (unique within the baseline)")
    description: str | None = None
    target_version_id: str | None = Field(
        None, description="Optional per-scenario target; inherits the baseline's when null"
    )


class ScenarioOut(_ORMModel):
    id: str
    baseline_id: str
    name: str
    description: str | None = None
    status: str
    target_version_id: str | None = None
    scenario_run_id: str | None = None
    created_at: datetime
    updated_at: datetime | None = None


class OverrideItem(_Request):
    dataset_id: str = Field(..., description="Source dataset whose field is overridden")
    field_name: str = Field(..., description="Contract field name to override")
    value: Any = Field(..., description="Override value (validated against the contract at run time)")


class OverridesRequest(_Request):
    overrides: list[OverrideItem] = Field(..., description="Overrides to set (create or replace)")


class OverrideOut(_ORMModel):
    id: str
    scenario_id: str
    dataset_id: str
    field_name: str
    value_json: str
    created_at: datetime


class ScenarioRunOut(BaseModel):
    scenario_id: str
    graph_run_id: str | None = None
    status: str | None = None
    success: bool
    execution_order: list[str] = Field(default_factory=list)
    error: str | None = None
    recorded_result_ids: list[str] = Field(default_factory=list)
    # Read-only invariant marker: scenario runs never publish shared dataset values.
    published_change_event_ids: list[str] = Field(default_factory=list)


class BaselineRunOut(BaseModel):
    baseline_id: str
    graph_run_id: str | None = None
    status: str | None = None
    success: bool
    execution_order: list[str] = Field(default_factory=list)
    error: str | None = None
    recorded_result_ids: list[str] = Field(default_factory=list)
    published_change_event_ids: list[str] = Field(default_factory=list)


class MetricDeltaOut(BaseModel):
    dataset_id: str
    field: str
    kind: str
    baseline: Any = None
    scenario: Any = None
    unit: str | None = None
    absolute_delta: float | None = None
    relative_delta: float | None = None
    baseline_zero: bool = False
    direction: str | None = None
    changed: bool


class ComparisonOut(BaseModel):
    baseline_run_id: str
    scenario_run_id: str
    metrics: list[MetricDeltaOut]


# ---------------------------------------------------------------------------
# Governance — Participants & Approved Outputs (D22)
# ---------------------------------------------------------------------------

class ParticipantCreate(_Request):
    participant_key: str = Field(..., description="Stable unique key for the participant")
    name: str = Field(..., description="Human-readable participant name")
    description: str | None = None
    status: str = Field("active", description="active | inactive")
    metadata: dict[str, Any] | None = None


class ParticipantUpdate(_Request):
    name: str | None = None
    description: str | None = None
    status: str | None = Field(None, description="active | inactive")
    metadata: dict[str, Any] | None = None


class ParticipantOut(BaseModel):
    id: str
    participant_key: str
    name: str
    description: str | None = None
    status: str
    metadata: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime | None = None
    organization_id: str | None = None


class ApprovedOutputCreate(_Request):
    participant_id: str = Field(..., description="Approving participant")
    dataset_id: str = Field(..., description="Dataset whose field is approved for exposure")
    field_name: str = Field(..., description="Dataset field approved for output/exposure")
    purpose: str | None = None
    expires_at: datetime | None = Field(None, description="Optional expiry (UTC); must be future")
    metadata: dict[str, Any] | None = None
    audience_organization_id: str | None = Field(
        None, description="Organization the field is shared with; null = the whole network")
    source_run_id: str | None = Field(
        None, description="Share this one run's recorded result instead of the live value")


class ApprovalRevoke(_Request):
    reason: str | None = Field(None, max_length=2000, description="Why the approval was revoked")


class ApprovedOutputOut(BaseModel):
    id: str
    participant_id: str
    dataset_id: str
    field_name: str
    purpose: str | None = None
    status: str
    approved_at: datetime | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    metadata: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime | None = None
    organization_id: str | None = None
    audience_organization_id: str | None = None
    source_run_id: str | None = None
    approved_by_user_id: str | None = None
    revoked_by_user_id: str | None = None
    revocation_reason: str | None = None


class ExposedFieldOut(BaseModel):
    participant_id: str
    participant_key: str
    dataset_id: str
    dataset_name: str
    field_name: str
    purpose: str | None = None
    value: Any = None
    value_present: bool
    approved_at: datetime | None = None
    expires_at: datetime | None = None
    audience_organization_id: str | None = None
    source_run_id: str | None = None
    value_source: str = "published"
    organization_id: str | None = None


class OwnerUpdate(_Request):
    participant_id: str | None = Field(None, description="Owning participant, or null to clear")


class GovernanceSummaryOut(BaseModel):
    participants: int
    active_participants: int
    approvals: int
    active_approvals: int
    effective_approvals: int
    revoked_approvals: int
