"""FastAPI dependencies for D11 — service wiring."""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session

from backend.app.persistence.database import get_db
from backend.app.services.adapter_registry import AdapterRegistry
from backend.app.services.change_propagation import ChangePropagationService
from backend.app.services.data_contract_manager import DataContractManager
from backend.app.services.dataset_values import DatasetValueService
from backend.app.services.dependency_graph import DependencyGraphService
from backend.app.services.execution import ModelExecutionService
from backend.app.services.model_registry import ModelRegistry
from backend.app.services.orchestration import GraphOrchestrationService
from backend.app.services.results_lineage import ResultsLineageService


def get_adapter_registry(db: Session = Depends(get_db, scope="function")) -> AdapterRegistry:
    """Adapters resolve from persisted ModelVersion config; no in-memory overrides in the app."""
    return AdapterRegistry(db)


def get_model_registry(db: Session = Depends(get_db, scope="function")) -> ModelRegistry:
    return ModelRegistry(db)


def get_contract_manager(db: Session = Depends(get_db, scope="function")) -> DataContractManager:
    return DataContractManager(db)


def get_graph_service(db: Session = Depends(get_db, scope="function")) -> DependencyGraphService:
    return DependencyGraphService(db)


def get_execution_service(
    db: Session = Depends(get_db, scope="function"),
    registry: AdapterRegistry = Depends(get_adapter_registry),
) -> ModelExecutionService:
    return ModelExecutionService(db, registry)


def get_orchestration_service(
    db: Session = Depends(get_db, scope="function"),
    registry: AdapterRegistry = Depends(get_adapter_registry),
) -> GraphOrchestrationService:
    return GraphOrchestrationService(db, registry)


def get_propagation_service(
    db: Session = Depends(get_db, scope="function"),
    registry: AdapterRegistry = Depends(get_adapter_registry),
) -> ChangePropagationService:
    return ChangePropagationService(db, registry)


def get_results_service(db: Session = Depends(get_db, scope="function")) -> ResultsLineageService:
    return ResultsLineageService(db)


def get_dataset_value_service(db: Session = Depends(get_db, scope="function")) -> DatasetValueService:
    return DatasetValueService(db)


def get_ingestion_service(db: Session = Depends(get_db, scope="function")):
    from backend.app.ingestion.service import IngestionService
    return IngestionService(db)


def get_scenario_service(
    db: Session = Depends(get_db, scope="function"),
    registry: AdapterRegistry = Depends(get_adapter_registry),
):
    from backend.app.services.scenarios import ScenarioService
    return ScenarioService(db, registry)


def get_scenario_comparison_service(db: Session = Depends(get_db, scope="function")):
    from backend.app.services.scenario_comparison import ScenarioComparisonService
    return ScenarioComparisonService(db)


def get_governance_service(db: Session = Depends(get_db, scope="function")):
    from backend.app.services.governance import GovernanceService
    return GovernanceService(db)
