"""Scenario & Baseline service — D19.

A scenario is an input-state definition plus metadata; it does NOT introduce a second
execution engine. Baselines and scenarios both run through the existing GraphRun machinery
(GraphOrchestrationService → ModelExecutionService → contracts → results/lineage):

  - execute_baseline() runs the target graph against current dataset state and PUBLISHES
    normally; the produced GraphRun is pinned as the baseline's authoritative run.
  - execute_scenario() resolves {baseline value ← override where defined}, runs the same
    graph with publish suppressed (read-only invariant: no Dataset.current_value mutation,
    no output ChangeEvents, no downstream propagation), stamps the run with scenario_id, and
    pins the produced GraphRun on the scenario.

Overrides are generic: keyed by (dataset_id, field_name) with a JSON value. They are applied
in-memory at input assembly and never written back to the dataset.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from backend.app.persistence.database import Baseline, Scenario, ScenarioOverride
from backend.app.persistence.exceptions import DuplicateError, NotFoundError
from backend.app.persistence.scenario_repository import (
    BaselineRepository,
    ScenarioOverrideRepository,
    ScenarioRepository,
)
from backend.app.services.adapter_registry import AdapterRegistry
from backend.app.services.dataset_values import DatasetValueService, normalize_value
from backend.app.services.federation import FederationService
from backend.app.services.orchestration import (
    GraphExecutionOutcome,
    GraphOrchestrationService,
)


# ---------------------------------------------------------------------------
# Service-level errors
# ---------------------------------------------------------------------------

class BaselineNotFoundError(Exception):
    """No baseline with the given ID exists."""


class ScenarioNotFoundError(Exception):
    """No scenario with the given ID exists."""


class DuplicateBaselineError(Exception):
    """A baseline with this name already exists."""


class DuplicateScenarioError(Exception):
    """A scenario with this name already exists for the baseline."""


class ScenarioOverrideError(Exception):
    """An override is invalid (unknown dataset, model-produced dataset, or unserializable)."""


class ScenarioExecutionError(Exception):
    """The scenario or baseline cannot be executed (e.g. no target version)."""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class ScenarioService:
    def __init__(self, db: Session, adapter_registry: AdapterRegistry | None = None) -> None:
        self._db = db
        self._baselines = BaselineRepository(db)
        self._scenarios = ScenarioRepository(db)
        self._overrides = ScenarioOverrideRepository(db)
        self._datasets = DatasetValueService(db)
        self._federation = FederationService(db)
        self._registry = adapter_registry or AdapterRegistry(db)

    def _orchestrator(self) -> GraphOrchestrationService:
        return GraphOrchestrationService(self._db, self._registry)

    # ------------------------------------------------------------------
    # Baseline lifecycle
    # ------------------------------------------------------------------

    def create_baseline(
        self,
        *,
        name: str,
        description: str | None = None,
        target_version_id: str | None = None,
        status: str = "active",
    ) -> Baseline:
        try:
            return self._baselines.add(Baseline(
                name=name, description=description,
                target_version_id=target_version_id, status=status,
            ))
        except DuplicateError as exc:
            raise DuplicateBaselineError(f"Baseline name={name!r} already exists.") from exc

    def get_baseline(self, baseline_id: str) -> Baseline:
        try:
            return self._baselines.get(baseline_id)
        except NotFoundError as exc:
            raise BaselineNotFoundError(f"Baseline {baseline_id!r} not found.") from exc

    def list_baselines(self, limit: int = 50) -> list[Baseline]:
        return self._baselines.list_recent(limit=limit)

    # ------------------------------------------------------------------
    # Scenario lifecycle
    # ------------------------------------------------------------------

    def create_scenario(
        self,
        *,
        baseline_id: str,
        name: str,
        description: str | None = None,
        target_version_id: str | None = None,
    ) -> Scenario:
        self.get_baseline(baseline_id)  # validates existence (404 mapping)
        try:
            return self._scenarios.add(Scenario(
                baseline_id=baseline_id, name=name, description=description,
                target_version_id=target_version_id, status="draft",
            ))
        except DuplicateError as exc:
            raise DuplicateScenarioError(
                f"Scenario name={name!r} already exists for baseline {baseline_id!r}."
            ) from exc

    def get_scenario(self, scenario_id: str) -> Scenario:
        try:
            return self._scenarios.get(scenario_id)
        except NotFoundError as exc:
            raise ScenarioNotFoundError(f"Scenario {scenario_id!r} not found.") from exc

    def list_scenarios(self, *, baseline_id: str | None = None, limit: int = 50) -> list[Scenario]:
        if baseline_id is not None:
            self.get_baseline(baseline_id)
            return self._scenarios.list_by_baseline(baseline_id)
        return self._scenarios.list_recent(limit=limit)

    # ------------------------------------------------------------------
    # Overrides
    # ------------------------------------------------------------------

    def set_override(
        self, scenario_id: str, dataset_id: str, field_name: str, value: Any
    ) -> ScenarioOverride:
        """Create or replace one dataset-field override for a scenario.

        Rejects overrides that target a dataset produced by a model (those values are
        computed each run and an override on them would silently have no effect).
        """
        self.get_scenario(scenario_id)
        try:
            self._datasets.get_dataset(dataset_id)
        except Exception as exc:
            raise ScenarioOverrideError(f"Dataset {dataset_id!r} not found.") from exc
        if self._federation.producers_of(dataset_id):
            raise ScenarioOverrideError(
                f"Dataset {dataset_id!r} is produced by a model; its values are computed "
                "each run and cannot be overridden. Override a source dataset instead."
            )
        try:
            value_json = normalize_value(value)
        except Exception as exc:
            raise ScenarioOverrideError(f"Override value is not serializable: {exc}") from exc

        existing = self._overrides.find(scenario_id, dataset_id, field_name)
        if existing is not None:
            existing.value_json = value_json
            self._db.flush()
            return existing
        return self._overrides.add(ScenarioOverride(
            scenario_id=scenario_id, dataset_id=dataset_id,
            field_name=field_name, value_json=value_json,
        ))

    def set_overrides(
        self, scenario_id: str, overrides: list[dict[str, Any]]
    ) -> list[ScenarioOverride]:
        """Bulk set overrides; each item is {dataset_id, field_name, value}."""
        return [
            self.set_override(scenario_id, o["dataset_id"], o["field_name"], o["value"])
            for o in overrides
        ]

    def list_overrides(self, scenario_id: str) -> list[ScenarioOverride]:
        self.get_scenario(scenario_id)
        return self._overrides.list_by_scenario(scenario_id)

    def resolve_overrides(self, scenario_id: str) -> dict[str, dict[str, Any]]:
        """Materialize overrides as {dataset_id: {field_name: value}} for a run."""
        resolved: dict[str, dict[str, Any]] = {}
        for ov in self._overrides.list_by_scenario(scenario_id):
            resolved.setdefault(ov.dataset_id, {})[ov.field_name] = json.loads(ov.value_json)
        return resolved

    # ------------------------------------------------------------------
    # Execution (reuses the existing GraphRun machinery)
    # ------------------------------------------------------------------

    def execute_baseline(
        self, baseline_id: str, *, triggered_by: str | None = "baseline"
    ) -> GraphExecutionOutcome:
        baseline = self.get_baseline(baseline_id)
        if not baseline.target_version_id:
            raise ScenarioExecutionError(
                f"Baseline {baseline_id!r} has no target_version_id to execute."
            )
        outcome = self._orchestrator().execute_graph(
            baseline.target_version_id,
            triggered_by=triggered_by,
            trigger_type="baseline",
            publish=True,
        )
        baseline.baseline_run_id = outcome.run_id
        self._db.flush()
        return outcome

    def execute_scenario(
        self, scenario_id: str, *, triggered_by: str | None = "scenario"
    ) -> GraphExecutionOutcome:
        scenario = self.get_scenario(scenario_id)
        target = scenario.target_version_id
        if not target:
            baseline = self.get_baseline(scenario.baseline_id)
            target = baseline.target_version_id
        if not target:
            raise ScenarioExecutionError(
                f"Scenario {scenario_id!r} has no target_version_id (and neither does its "
                "baseline) to execute."
            )
        overrides = self.resolve_overrides(scenario_id)
        outcome = self._orchestrator().execute_graph(
            target,
            triggered_by=triggered_by,
            trigger_type="scenario",
            scenario_id=scenario_id,
            overrides=overrides,
            publish=False,   # read-only invariant: never mutate shared baseline state
        )
        scenario.scenario_run_id = outcome.run_id
        if outcome.success:
            scenario.status = "executed"
        self._db.flush()
        return outcome
