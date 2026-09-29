"""Model Execution service — D7, rebuilt for dataset-aware federation in D16.

run_step() executes one model version as one ExecutionStep of a run (the GraphRun):

    declared inputs (FederationService)
        → read each dataset: value staged earlier in this run, else Dataset.current_value
        → input contract validation → field mapping → collision check
    adapter.invoke(inputs)
        → map outputs to each declared output dataset → output contract validation
        → canonical JSON (result persistence) → Result + LineageEdge rows
        → stage outputs in the run (published to datasets only if the whole run succeeds)

A version with no declared inputs receives the run's direct `input_data`.
execute_model() is the single-model entry point: one run (run_kind="single"), one step,
results recorded, datasets NOT published (downstream would otherwise go stale).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, NamedTuple

from sqlalchemy.orm import Session

from backend.app.adapters.base import ModelAdapter
from backend.app.persistence.database import (
    ChangeEvent,
    ExecutionRun,
    ExecutionStep,
    ModelVersion,
)
from backend.app.persistence.exceptions import NotFoundError
from backend.app.persistence.execution_run_repository import ExecutionRunRepository
from backend.app.persistence.execution_step_repository import ExecutionStepRepository
from backend.app.persistence.model_version_repository import ModelVersionRepository
from backend.app.services.adapter_registry import (
    AdapterConfigError,
    AdapterNotFoundError,
    AdapterRegistry,
)
from backend.app.services.contract_validation import ContractViolationError
from backend.app.services.data_contract_manager import DataContractManager
from backend.app.services.dataset_values import (
    DatasetValueSerializationError,
    DatasetValueService,
    hash_normalized,
    normalize_value,
)
from backend.app.services.federation import (
    FederationService,
    InputAssemblyError,
    apply_field_map,
)
from backend.app.services.results_lineage import InputProvenance, ResultsLineageService


# ---------------------------------------------------------------------------
# Service-level errors
# ---------------------------------------------------------------------------

class ExecutionVersionNotFoundError(Exception):
    """The referenced model version does not exist."""


class ExecutionAdapterNotFoundError(Exception):
    """No adapter is registered or persisted for the requested model version."""


class ExecutionRunNotFoundError(Exception):
    """No execution run with the given ID exists."""


class ExecutionStepNotFoundError(Exception):
    """No execution step with the given ID exists."""


class InvalidExecutionStateError(Exception):
    """The execution is in an unexpected state."""


class AdapterExecutionFailure(Exception):
    """The adapter raised an error during invocation."""


class ExecutionPersistenceError(Exception):
    """Unexpected persistence failure during an execution operation."""


class StepFailure(Exception):
    """A step failed for an explicit, reportable reason (never silently ignored)."""


# ---------------------------------------------------------------------------
# Return / context types
# ---------------------------------------------------------------------------

class ExecutionOutcome(NamedTuple):
    run_id: str
    step_id: str
    status: str                    # "succeeded" or "failed"
    outputs: dict[str, Any] | None  # raw adapter outputs on success
    error: str | None
    result_ids: tuple[str, ...] = ()


@dataclass
class StagedValue:
    record: dict[str, Any]
    normalized: str
    result_id: str
    version_id: str


@dataclass
class RunContext:
    """In-run state shared by the steps of one GraphRun."""
    run: ExecutionRun
    direct_inputs: dict[str, Any]
    staged: dict[str, StagedValue] = field(default_factory=dict)
    # ChangeEvents whose (persisted) values were read as inputs in this run
    consumed_events: dict[str, ChangeEvent] = field(default_factory=dict)
    # Scenario field overrides (D19): dataset_id → {field: value}. Applied to values read
    # from Dataset.current_value at input assembly; never written back to the dataset.
    overrides: dict[str, dict[str, Any]] = field(default_factory=dict)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Execution service
# ---------------------------------------------------------------------------

class ModelExecutionService:
    def __init__(self, db: Session, adapter_registry: AdapterRegistry) -> None:
        self._db = db
        self._registry = adapter_registry
        self._versions = ModelVersionRepository(db)
        self._runs = ExecutionRunRepository(db)
        self._steps = ExecutionStepRepository(db)
        self._federation = FederationService(db)
        self._contracts = DataContractManager(db)
        self._datasets = DatasetValueService(db)
        self._results = ResultsLineageService(db)

    # ------------------------------------------------------------------
    # Resolution (used by planning before any run is created)
    # ------------------------------------------------------------------

    def get_version(self, version_id: str) -> ModelVersion:
        try:
            return self._versions.get(version_id)
        except NotFoundError as exc:
            raise ExecutionVersionNotFoundError(
                f"Model version_id={version_id!r} not found."
            ) from exc

    def resolve_adapter(self, version: ModelVersion) -> ModelAdapter:
        try:
            return self._registry.resolve_for_version(version)
        except (AdapterNotFoundError, AdapterConfigError) as exc:
            raise ExecutionAdapterNotFoundError(str(exc)) from exc

    def has_declared_inputs(self, version_id: str) -> bool:
        return bool(self._federation.declared_inputs(version_id))

    # ------------------------------------------------------------------
    # Single-model execution
    # ------------------------------------------------------------------

    def execute_model(
        self,
        version_id: str,
        input_data: dict[str, Any],
        *,
        triggered_by: str | None = None,
    ) -> ExecutionOutcome:
        version = self.get_version(version_id)
        self._federation.assert_executable(version)
        adapter = self.resolve_adapter(version)

        run = self._runs.add(ExecutionRun(
            run_kind="single",
            target_version_id=version_id,
            subgraph_json=json.dumps([version_id]),
            input_snapshot=json.dumps(input_data),
            status="running",
            triggered_by=triggered_by,
            trigger_type="manual",
            started_at=_now(),
        ))
        step = self._steps.add(ExecutionStep(
            run_id=run.id, model_version_id=version_id, step_order=0, status="pending",
        ))
        ctx = RunContext(run=run, direct_inputs=dict(input_data))
        outcome = self.run_step(ctx, version, adapter, step)

        run.status = outcome.status
        run.error_message = outcome.error
        run.finished_at = _now()
        self._db.flush()
        return outcome

    # ------------------------------------------------------------------
    # One step of a run
    # ------------------------------------------------------------------

    def run_step(
        self,
        ctx: RunContext,
        version: ModelVersion,
        adapter: ModelAdapter,
        step: ExecutionStep,
    ) -> ExecutionOutcome:
        step.status = "running"
        step.started_at = _now()
        self._db.flush()

        try:
            inputs, provenance = self._assemble_inputs(ctx, version)
            try:
                step.input_snapshot = normalize_value(inputs)
            except DatasetValueSerializationError as exc:
                raise StepFailure(f"Inputs are not JSON-serializable: {exc}") from exc

            try:
                outputs = adapter.invoke(inputs)
            except Exception as exc:  # adapter errors are reported, never swallowed
                raise StepFailure(str(exc)) from exc
            if not isinstance(outputs, dict):
                raise StepFailure(
                    f"Adapter returned {type(outputs).__name__}; outputs must be an object."
                )

            records = self._map_outputs(version, outputs)
            for dataset_id in records:
                if dataset_id in ctx.staged:
                    raise StepFailure(
                        f"Dataset {dataset_id!r} was already written in this run by "
                        f"version {ctx.staged[dataset_id].version_id!r}."
                    )
            results = self._results.record_step_results(
                run_id=ctx.run.id,
                step_id=step.id,
                version_id=version.id,
                records=records,
                input_snapshot=step.input_snapshot,
                provenance=provenance,
                scenario_id=ctx.run.scenario_id,
            )
            result_by_ds = {r.dataset_id: r for r in results}
            for dataset_id, (normalized, record) in records.items():
                ctx.staged[dataset_id] = StagedValue(
                    record=record,
                    normalized=normalized,
                    result_id=result_by_ds[dataset_id].id,
                    version_id=version.id,
                )
        except StepFailure as exc:
            return self._fail_step(ctx, step, str(exc))

        step.status = "succeeded"
        try:
            step.output_fingerprint = hash_normalized(normalize_value(outputs))
        except DatasetValueSerializationError:
            step.output_fingerprint = None
        step.finished_at = _now()
        self._db.flush()
        return ExecutionOutcome(
            run_id=ctx.run.id,
            step_id=step.id,
            status="succeeded",
            outputs=outputs,
            error=None,
            result_ids=tuple(r.id for r in results),
        )

    def _fail_step(self, ctx: RunContext, step: ExecutionStep, message: str) -> ExecutionOutcome:
        step.status = "failed"
        step.error_message = message
        step.finished_at = _now()
        self._db.flush()
        return ExecutionOutcome(ctx.run.id, step.id, "failed", None, message)

    # ------------------------------------------------------------------
    # Input assembly
    # ------------------------------------------------------------------

    def _assemble_inputs(
        self, ctx: RunContext, version: ModelVersion
    ) -> tuple[dict[str, Any], list[InputProvenance]]:
        decls = self._federation.declared_inputs(version.id)
        if not decls:
            return dict(ctx.direct_inputs), []

        inputs: dict[str, Any] = {}
        origin: dict[str, str] = {}
        provenance: list[InputProvenance] = []
        for decl in decls:
            staged = ctx.staged.get(decl.dataset_id)
            if staged is not None:
                record = staged.record
                prov = InputProvenance(decl.dataset_id, staged.result_id, None)
            else:
                dataset = self._datasets.get_dataset(decl.dataset_id)
                if dataset.current_value is None:
                    raise StepFailure(f"Input dataset {dataset.name!r} has no value.")
                record = json.loads(dataset.current_value)
                # Scenario overrides (D19) modify the read-in value only, in-memory. The
                # persisted Dataset.current_value is never mutated; contract validation below
                # still runs on the merged record, so an invalid override fails the step.
                field_overrides = ctx.overrides.get(decl.dataset_id)
                if field_overrides:
                    record = {**record, **field_overrides}
                event = self._datasets.current_value_event(decl.dataset_id)
                if event is not None:
                    ctx.consumed_events[event.id] = event
                prov = InputProvenance(
                    decl.dataset_id,
                    event.result_id if event is not None else None,
                    event.id if event is not None else None,
                )

            try:
                record = self._contracts.validate_record(decl.dataset_id, record, phase="input")
            except ContractViolationError as exc:
                raise StepFailure(str(exc)) from exc
            if not isinstance(record, dict):
                raise StepFailure(f"Input dataset {decl.dataset_id!r} does not hold an object.")
            try:
                mapped = apply_field_map(record, decl.field_map)
            except InputAssemblyError as exc:
                raise StepFailure(f"Input dataset {decl.dataset_id!r}: {exc}") from exc

            for name, value in mapped.items():
                if name in inputs:
                    raise StepFailure(
                        f"Input collision on {name!r}: provided by datasets "
                        f"{origin[name]!r} and {decl.dataset_id!r}. Declare a field_map."
                    )
                inputs[name] = value
                origin[name] = decl.dataset_id
            provenance.append(prov)
        return inputs, provenance

    # ------------------------------------------------------------------
    # Output mapping
    # ------------------------------------------------------------------

    def _map_outputs(
        self, version: ModelVersion, outputs: dict[str, Any]
    ) -> dict[str, tuple[str, dict[str, Any]]]:
        records: dict[str, tuple[str, dict[str, Any]]] = {}
        for decl in self._federation.declared_outputs(version.id):
            try:
                record = apply_field_map(outputs, decl.field_map)
            except InputAssemblyError as exc:
                raise StepFailure(f"Output for dataset {decl.dataset_id!r}: {exc}") from exc
            try:
                record = self._contracts.validate_record(decl.dataset_id, record, phase="output")
            except ContractViolationError as exc:
                raise StepFailure(str(exc)) from exc
            try:
                normalized = normalize_value(record)
            except DatasetValueSerializationError as exc:
                raise StepFailure(
                    f"Result persistence failed: output for dataset {decl.dataset_id!r} "
                    f"is not JSON-serializable ({exc})."
                ) from exc
            records[decl.dataset_id] = (normalized, json.loads(normalized))
        return records

    # ------------------------------------------------------------------
    # Execution history lookup
    # ------------------------------------------------------------------

    def get_execution_run(self, run_id: str) -> ExecutionRun:
        try:
            return self._runs.get(run_id)
        except NotFoundError as exc:
            raise ExecutionRunNotFoundError(str(exc)) from exc

    def list_recent_runs(self, limit: int = 20) -> list[ExecutionRun]:
        return self._runs.list_recent(limit=limit)

    def get_execution_step(self, step_id: str) -> ExecutionStep:
        try:
            return self._steps.get(step_id)
        except NotFoundError as exc:
            raise ExecutionStepNotFoundError(str(exc)) from exc

    def list_execution_steps(self, run_id: str) -> list[ExecutionStep]:
        return self._steps.list_by_run(run_id)
