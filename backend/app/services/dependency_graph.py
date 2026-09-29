"""Dependency Graph service — D5.

Application-layer service for registering dependencies and computing
graph properties (upstream/downstream traversal, cycle detection,
topological ordering).

Graph model: directed edges between ModelVersion nodes.
  producer_version_id → consumer_version_id  (via a shared dataset)

networkx DiGraph is built in-memory from persisted Dependency rows;
persistence remains the repository's responsibility.
"""

from __future__ import annotations

from typing import NamedTuple

import networkx as nx
from sqlalchemy.orm import Session

from backend.app.persistence.database import Dependency
from backend.app.persistence.dependency_repository import DependencyRepository
from backend.app.persistence.dataset_repository import DatasetRepository
from backend.app.persistence.exceptions import DuplicateError, NotFoundError
from backend.app.persistence.io_binding_repository import ModelIOBindingRepository
from backend.app.persistence.model_version_repository import ModelVersionRepository


# ---------------------------------------------------------------------------
# Service-level errors
# ---------------------------------------------------------------------------

class DependencyNotFoundError(Exception):
    """No dependency with the given ID exists."""


class ProducerVersionNotFoundError(Exception):
    """The referenced producer (source) model version does not exist."""


class ConsumerVersionNotFoundError(Exception):
    """The referenced consumer (target) model version does not exist."""


class DependencyDatasetNotFoundError(Exception):
    """A referenced dataset does not exist."""


class DuplicateDependencyError(Exception):
    """An identical dependency edge is already registered."""


class SelfDependencyError(Exception):
    """A model version cannot depend on itself."""


class CycleDetectedError(Exception):
    """The dependency graph contains a cycle; topological ordering is impossible."""

    def __init__(self, message: str, cycle_nodes: list[str]) -> None:
        super().__init__(message)
        self.cycle_nodes = cycle_nodes


class GraphOrderingError(Exception):
    """Topological ordering cannot be computed for this graph."""


class DependencyPersistenceError(Exception):
    """Unexpected persistence failure during a dependency operation."""


# ---------------------------------------------------------------------------
# Return type for cycle detection
# ---------------------------------------------------------------------------

class CycleReport(NamedTuple):
    has_cycle: bool
    cycle_nodes: list[str]  # empty when has_cycle is False


# ---------------------------------------------------------------------------
# Dependency Graph service
# ---------------------------------------------------------------------------

class DependencyGraphService:
    """
    Manages dependency edges between model versions and provides
    in-memory graph analysis.

    One instance per request/use-case: constructed with an open SQLAlchemy
    session; the caller owns commit/rollback.
    """

    def __init__(self, db: Session) -> None:
        self._db = db
        self._deps = DependencyRepository(db)
        self._versions = ModelVersionRepository(db)
        self._datasets = DatasetRepository(db)
        self._bindings = ModelIOBindingRepository(db)

    # ------------------------------------------------------------------
    # Dependency registration
    # ------------------------------------------------------------------

    def register_dependency(
        self,
        *,
        producer_version_id: str,
        output_dataset_id: str,
        consumer_version_id: str,
        input_dataset_id: str,
        dependency_kind: str = "data",
    ) -> Dependency:
        """
        Register a dataset-mediated dependency edge between two model versions.

        Raises SelfDependencyError if producer == consumer.
        Raises ProducerVersionNotFoundError / ConsumerVersionNotFoundError if
            the referenced model versions don't exist.
        Raises DependencyDatasetNotFoundError if a referenced dataset doesn't exist.
        Raises DuplicateDependencyError if an identical edge already exists.
        Raises DependencyPersistenceError on unexpected DB failure.
        """
        # Self-dependency check
        if producer_version_id == consumer_version_id:
            raise SelfDependencyError(
                f"Model version {producer_version_id!r} cannot depend on itself."
            )

        # Verify producer version exists
        try:
            self._versions.get(producer_version_id)
        except NotFoundError as exc:
            raise ProducerVersionNotFoundError(
                f"Producer version {producer_version_id!r} not found."
            ) from exc

        # Verify consumer version exists
        try:
            self._versions.get(consumer_version_id)
        except NotFoundError as exc:
            raise ConsumerVersionNotFoundError(
                f"Consumer version {consumer_version_id!r} not found."
            ) from exc

        # Verify both datasets exist
        for ds_id, label in (
            (output_dataset_id, "output"),
            (input_dataset_id, "input"),
        ):
            try:
                self._datasets.get(ds_id)
            except NotFoundError as exc:
                raise DependencyDatasetNotFoundError(
                    f"Dependency {label}_dataset_id={ds_id!r} not found."
                ) from exc

        entity = Dependency(
            producer_version_id=producer_version_id,
            output_dataset_id=output_dataset_id,
            consumer_version_id=consumer_version_id,
            input_dataset_id=input_dataset_id,
            dependency_kind=dependency_kind,
        )
        try:
            return self._deps.add(entity)
        except DuplicateError as exc:
            raise DuplicateDependencyError(
                f"Dependency producer={producer_version_id!r} "
                f"consumer={consumer_version_id!r} "
                f"input_dataset={input_dataset_id!r} already exists."
            ) from exc
        except Exception as exc:
            raise DependencyPersistenceError(str(exc)) from exc

    # ------------------------------------------------------------------
    # Dependency lookup
    # ------------------------------------------------------------------

    def get_dependency(self, dependency_id: str) -> Dependency:
        try:
            return self._deps.get(dependency_id)
        except NotFoundError as exc:
            raise DependencyNotFoundError(str(exc)) from exc

    def list_dependencies(self, *, limit: int = 500) -> list[Dependency]:
        return self._deps.list(limit=limit)

    def list_by_producer(self, producer_version_id: str) -> list[Dependency]:
        return self._deps.list_by_producer(producer_version_id)

    def list_by_consumer(self, consumer_version_id: str) -> list[Dependency]:
        return self._deps.list_by_consumer(consumer_version_id)

    # ------------------------------------------------------------------
    # Graph construction (internal)
    # ------------------------------------------------------------------

    def _build_graph(self) -> nx.DiGraph:
        """
        Directed graph over model versions.

        Edges: every Dependency row, plus producer → consumer for each dataset that one
        version declares as an output binding and another declares as an input binding.
        Versions with any binding are nodes even without edges.
        """
        G: nx.DiGraph = nx.DiGraph()
        for dep in self._deps.list(limit=10_000):
            G.add_edge(dep.producer_version_id, dep.consumer_version_id)

        producers: dict[str, set[str]] = {}
        consumers: dict[str, set[str]] = {}
        for b in self._bindings.list_all():
            G.add_node(b.model_version_id)
            target = producers if b.direction == "output" else consumers
            target.setdefault(b.dataset_id, set()).add(b.model_version_id)
        for dataset_id, prods in producers.items():
            for p in prods:
                for c in consumers.get(dataset_id, ()):
                    if p != c:
                        G.add_edge(p, c)
        return G

    # ------------------------------------------------------------------
    # Upstream / downstream traversal
    # ------------------------------------------------------------------

    def get_upstream(self, version_id: str) -> list[str]:
        """
        Return all model version IDs that are transitive upstream ancestors
        of the given version (i.e. nodes that can reach version_id).
        Excludes version_id itself. Returns [] if version_id is not in graph.
        """
        G = self._build_graph()
        if version_id not in G:
            return []
        return list(nx.ancestors(G, version_id))

    def get_downstream(self, version_id: str) -> list[str]:
        """
        Return all model version IDs that are transitive downstream descendants
        of the given version (i.e. nodes reachable from version_id).
        Excludes version_id itself. Returns [] if version_id is not in graph.
        """
        G = self._build_graph()
        if version_id not in G:
            return []
        return list(nx.descendants(G, version_id))

    # ------------------------------------------------------------------
    # Cycle detection
    # ------------------------------------------------------------------

    def detect_cycles(self) -> CycleReport:
        """
        Check whether the current graph is acyclic.

        Returns CycleReport(has_cycle=False, cycle_nodes=[]) for DAGs.
        Returns CycleReport(has_cycle=True, cycle_nodes=[...]) for cyclic graphs,
        where cycle_nodes is one detected cycle as a list of version IDs.
        """
        G = self._build_graph()
        if nx.is_directed_acyclic_graph(G):
            return CycleReport(has_cycle=False, cycle_nodes=[])
        # nx.find_cycle returns an edge list; extract unique nodes in order
        try:
            cycle_edges = nx.find_cycle(G)
            cycle_nodes = list(dict.fromkeys(n for edge in cycle_edges for n in edge))
        except nx.NetworkXNoCycle:
            return CycleReport(has_cycle=False, cycle_nodes=[])
        return CycleReport(has_cycle=True, cycle_nodes=cycle_nodes)

    # ------------------------------------------------------------------
    # Topological ordering
    # ------------------------------------------------------------------

    def topological_order(self) -> list[str]:
        """
        Return a deterministic dependency-aware ordering of all model version IDs.

        Raises CycleDetectedError if the graph contains a cycle.
        Returns an empty list if no dependencies are registered.
        """
        G = self._build_graph()
        if not nx.is_directed_acyclic_graph(G):
            report = self.detect_cycles()
            raise CycleDetectedError(
                "Cannot compute topological order: dependency graph contains a cycle.",
                cycle_nodes=report.cycle_nodes,
            )
        # Lexicographic tie-break makes ordering of independent nodes stable across runs.
        return list(nx.lexicographical_topological_sort(G, key=str))
