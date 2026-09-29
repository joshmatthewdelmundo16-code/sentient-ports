"""Baseline / Scenario / ScenarioOverride repositories — D19."""

from __future__ import annotations

from backend.app.persistence.base_repository import BaseRepository
from backend.app.persistence.database import Baseline, Scenario, ScenarioOverride
from backend.app.persistence.exceptions import NotFoundError


class BaselineRepository(BaseRepository[Baseline]):
    model_class = Baseline

    def get_by_name(self, name: str) -> Baseline:
        obj = self._db.query(Baseline).filter(Baseline.name == name).first()
        if obj is None:
            raise NotFoundError(f"Baseline name={name!r} not found")
        return obj

    def list_recent(self, limit: int = 50) -> list[Baseline]:
        return (
            self._db.query(Baseline)
            .order_by(Baseline.created_at.desc(), Baseline.id.desc())
            .limit(limit)
            .all()
        )


class ScenarioRepository(BaseRepository[Scenario]):
    model_class = Scenario

    def list_by_baseline(self, baseline_id: str) -> list[Scenario]:
        return (
            self._db.query(Scenario)
            .filter(Scenario.baseline_id == baseline_id)
            .order_by(Scenario.created_at.desc(), Scenario.id.desc())
            .all()
        )

    def list_recent(self, limit: int = 50) -> list[Scenario]:
        return (
            self._db.query(Scenario)
            .order_by(Scenario.created_at.desc(), Scenario.id.desc())
            .limit(limit)
            .all()
        )


class ScenarioOverrideRepository(BaseRepository[ScenarioOverride]):
    model_class = ScenarioOverride

    def list_by_scenario(self, scenario_id: str) -> list[ScenarioOverride]:
        return (
            self._db.query(ScenarioOverride)
            .filter(ScenarioOverride.scenario_id == scenario_id)
            .order_by(ScenarioOverride.dataset_id, ScenarioOverride.field_name)
            .all()
        )

    def find(self, scenario_id: str, dataset_id: str, field_name: str) -> ScenarioOverride | None:
        return (
            self._db.query(ScenarioOverride)
            .filter(
                ScenarioOverride.scenario_id == scenario_id,
                ScenarioOverride.dataset_id == dataset_id,
                ScenarioOverride.field_name == field_name,
            )
            .first()
        )
