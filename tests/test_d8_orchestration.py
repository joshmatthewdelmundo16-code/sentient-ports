"""
D8 Graph Orchestration service tests — AC-01 through AC-13.

Synthetic chain: FuelPrice(A) → ShippingCost(B) → OperationsCost(C) → Emissions(D)

Uses real SQLite persistence and the deterministic D6 SyntheticAdapter.
Class-scoped isolated engines prevent unique-constraint collisions across
test classes (same pattern as D5 graph traversal tests).
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from sqlalchemy import create_engine, event as sa_event
from sqlalchemy.orm import Session, sessionmaker

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("STORAGE_ROOT", "storage_test_d8_tmp")

from backend.app.persistence.database import (  # noqa: E402
    Base,
    Dataset,
    Dependency,
    Model,
    ModelVersion,
)
from backend.app.adapters.synthetic import SyntheticAdapter  # noqa: E402
from backend.app.services.adapter_registry import AdapterRegistry  # noqa: E402
from backend.app.services.execution import (  # noqa: E402
    ExecutionAdapterNotFoundError,
)
from backend.app.services.orchestration import (  # noqa: E402
    GraphOrchestrationService,
    GraphExecutionOutcome,
    OrchestrationCycleError,
    OrchestrationTargetNotFoundError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _isolated_engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    @sa_event.listens_for(eng, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(bind=eng)
    return eng


def _make_version(session: Session, name: str, owner: str = "test",
                  semver: str = "1.0.0") -> ModelVersion:
    m = Model(name=name, owner=owner, model_type="python")
    session.add(m)
    session.flush()
    v = ModelVersion(model_id=m.id, semver=semver, is_active=True)
    session.add(v)
    session.flush()
    return v


def _make_dep(session: Session, producer: ModelVersion, consumer: ModelVersion,
              ds_name: str) -> None:
    ds = Dataset(name=ds_name, description=f"dataset {ds_name}")
    session.add(ds)
    session.flush()
    dep = Dependency(
        producer_version_id=producer.id,
        output_dataset_id=ds.id,
        consumer_version_id=consumer.id,
        input_dataset_id=ds.id,
        dependency_kind="data",
    )
    session.add(dep)
    session.flush()


def _build_chain(session: Session, prefix: str):
    """Build A → B → C → D with distinct names using prefix."""
    va = _make_version(session, f"{prefix}_A")
    vb = _make_version(session, f"{prefix}_B")
    vc = _make_version(session, f"{prefix}_C")
    vd = _make_version(session, f"{prefix}_D")
    _make_dep(session, va, vb, f"{prefix}_ds_ab")
    _make_dep(session, vb, vc, f"{prefix}_ds_bc")
    _make_dep(session, vc, vd, f"{prefix}_ds_cd")
    session.commit()
    return va, vb, vc, vd


def _register_adapters(registry: AdapterRegistry,
                       versions: tuple[ModelVersion, ...],
                       scalar: float = 2.0) -> None:
    for i, v in enumerate(versions):
        registry.register_adapter(
            SyntheticAdapter(
                adapter_id=f"orch-adapter-{v.id}",
                version_id=v.id,
                scalar=scalar if i > 0 else 1.0,  # root passes through
            )
        )


# ---------------------------------------------------------------------------
# AC-06: Successful multi-node chain execution
# ---------------------------------------------------------------------------

class TestChainExecution:
    @pytest.fixture(scope="class")
    def chain_env(self):
        eng = _isolated_engine()
        SM = sessionmaker(bind=eng, autocommit=False, autoflush=False)
        session = SM()
        va, vb, vc, vd = _build_chain(session, "chain")
        registry = AdapterRegistry(session)
        _register_adapters(registry, (va, vb, vc, vd), scalar=2.0)
        svc = GraphOrchestrationService(session, registry)
        yield svc, va, vb, vc, vd, session
        session.close()
        eng.dispose()

    def test_execute_graph_succeeds(self, chain_env):
        svc, va, vb, vc, vd, *_ = chain_env
        outcome = svc.execute_graph(vd.id, {"fuel": 100.0})
        assert outcome.success is True

    def test_execute_graph_outcome_type(self, chain_env):
        svc, va, vb, vc, vd, *_ = chain_env
        outcome = svc.execute_graph(vd.id, {})
        assert isinstance(outcome, GraphExecutionOutcome)

    def test_execute_graph_no_error(self, chain_env):
        svc, va, vb, vc, vd, *_ = chain_env
        outcome = svc.execute_graph(vd.id, {})
        assert outcome.error is None
        assert outcome.first_failure_version_id is None

    def test_all_four_nodes_executed(self, chain_env):
        svc, va, vb, vc, vd, *_ = chain_env
        outcome = svc.execute_graph(vd.id, {"val": 1.0})
        assert set(outcome.step_outcomes.keys()) == {va.id, vb.id, vc.id, vd.id}

    def test_all_steps_succeeded(self, chain_env):
        svc, va, vb, vc, vd, *_ = chain_env
        outcome = svc.execute_graph(vd.id, {"val": 1.0})
        for step_out in outcome.step_outcomes.values():
            assert step_out.status == "succeeded"

    # AC-04: dependency order respected
    def test_execution_order_respected(self, chain_env):
        svc, va, vb, vc, vd, *_ = chain_env
        outcome = svc.execute_graph(vd.id, {"val": 1.0})
        order = outcome.execution_order
        # A must come before B, B before C, C before D
        assert order.index(va.id) < order.index(vb.id)
        assert order.index(vb.id) < order.index(vc.id)
        assert order.index(vc.id) < order.index(vd.id)

    def test_target_is_last_in_order(self, chain_env):
        svc, va, vb, vc, vd, *_ = chain_env
        outcome = svc.execute_graph(vd.id, {})
        assert outcome.execution_order[-1] == vd.id

    def test_outputs_forwarded_downstream(self, chain_env):
        svc, va, vb, vc, vd, *_ = chain_env
        # A scalar=1.0, B/C/D scalar=2.0; input fuel=10
        # A: fuel→10, B: fuel→20, C: fuel→40, D: fuel→80
        outcome = svc.execute_graph(vd.id, {"fuel": 10.0})
        d_output = outcome.step_outcomes[vd.id].outputs
        assert d_output is not None
        assert d_output["fuel"] == pytest.approx(80.0)


# ---------------------------------------------------------------------------
# AC-02: Single-node execution (no dependencies registered)
# ---------------------------------------------------------------------------

class TestSingleNodeExecution:
    @pytest.fixture(scope="class")
    def single_env(self):
        eng = _isolated_engine()
        SM = sessionmaker(bind=eng, autocommit=False, autoflush=False)
        session = SM()
        v = _make_version(session, "single_model")
        session.commit()
        registry = AdapterRegistry(session)
        registry.register_adapter(
            SyntheticAdapter(adapter_id=f"single-{v.id}", version_id=v.id, scalar=3.0)
        )
        svc = GraphOrchestrationService(session, registry)
        yield svc, v, session
        session.close()
        eng.dispose()

    def test_single_node_succeeds(self, single_env):
        svc, v, *_ = single_env
        outcome = svc.execute_graph(v.id, {"x": 5.0})
        assert outcome.success is True

    def test_single_node_output_correct(self, single_env):
        svc, v, *_ = single_env
        outcome = svc.execute_graph(v.id, {"x": 5.0})
        assert outcome.step_outcomes[v.id].outputs["x"] == pytest.approx(15.0)

    def test_single_node_execution_order_contains_target(self, single_env):
        svc, v, *_ = single_env
        outcome = svc.execute_graph(v.id, {})
        assert v.id in outcome.execution_order


# ---------------------------------------------------------------------------
# AC-05: Cycle detection blocks execution
# ---------------------------------------------------------------------------

class TestCycleProtection:
    @pytest.fixture(scope="class")
    def cyclic_env(self):
        eng = _isolated_engine()
        SM = sessionmaker(bind=eng, autocommit=False, autoflush=False)
        session = SM()
        va = _make_version(session, "cyc_A")
        vb = _make_version(session, "cyc_B")
        vc = _make_version(session, "cyc_C")
        # A → B → C → A  (cycle)
        _make_dep(session, va, vb, "cyc_ds_ab")
        _make_dep(session, vb, vc, "cyc_ds_bc")
        _make_dep(session, vc, va, "cyc_ds_ca")
        session.commit()
        registry = AdapterRegistry(session)
        svc = GraphOrchestrationService(session, registry)
        yield svc, va, vb, vc, session
        session.close()
        eng.dispose()

    def test_cyclic_graph_raises(self, cyclic_env):
        svc, va, vb, vc, *_ = cyclic_env
        with pytest.raises(OrchestrationCycleError):
            svc.execute_graph(vc.id, {})

    def test_cycle_error_has_nodes(self, cyclic_env):
        svc, va, vb, vc, *_ = cyclic_env
        try:
            svc.execute_graph(va.id, {})
        except OrchestrationCycleError as exc:
            assert len(exc.cycle_nodes) > 0

    def test_no_execution_on_cycle(self, cyclic_env):
        """No adapter invocation should happen before cycle is detected."""
        svc, va, vb, vc, *_ = cyclic_env
        # The registry has no adapters; if execution were attempted it would
        # fail with ExecutionAdapterNotFoundError, not OrchestrationCycleError
        with pytest.raises(OrchestrationCycleError):
            svc.execute_graph(va.id, {})


# ---------------------------------------------------------------------------
# AC-07: Upstream failure stops downstream execution
# ---------------------------------------------------------------------------

class FailingAdapter(SyntheticAdapter):
    def invoke(self, inputs):
        raise RuntimeError("upstream synthetic failure")


class TestUpstreamFailure:
    @pytest.fixture(scope="class")
    def failure_env(self):
        eng = _isolated_engine()
        SM = sessionmaker(bind=eng, autocommit=False, autoflush=False)
        session = SM()
        va = _make_version(session, "fail_A")
        vb = _make_version(session, "fail_B")
        vc = _make_version(session, "fail_C")
        _make_dep(session, va, vb, "fail_ds_ab")
        _make_dep(session, vb, vc, "fail_ds_bc")
        session.commit()
        registry = AdapterRegistry(session)
        # A will fail; B and C have working adapters (but should not be reached)
        registry.register_adapter(FailingAdapter(adapter_id=f"fail-a-{va.id}", version_id=va.id))
        registry.register_adapter(
            SyntheticAdapter(adapter_id=f"fail-b-{vb.id}", version_id=vb.id)
        )
        registry.register_adapter(
            SyntheticAdapter(adapter_id=f"fail-c-{vc.id}", version_id=vc.id)
        )
        svc = GraphOrchestrationService(session, registry)
        yield svc, va, vb, vc, session
        session.close()
        eng.dispose()

    def test_graph_fails_when_upstream_fails(self, failure_env):
        svc, va, vb, vc, *_ = failure_env
        outcome = svc.execute_graph(vc.id, {})
        assert outcome.success is False

    def test_first_failure_identified(self, failure_env):
        svc, va, vb, vc, *_ = failure_env
        outcome = svc.execute_graph(vc.id, {})
        assert outcome.first_failure_version_id == va.id

    def test_downstream_not_executed(self, failure_env):
        svc, va, vb, vc, *_ = failure_env
        outcome = svc.execute_graph(vc.id, {})
        # B and C should not appear in step_outcomes
        assert vb.id not in outcome.step_outcomes
        assert vc.id not in outcome.step_outcomes

    def test_error_message_present(self, failure_env):
        svc, va, vb, vc, *_ = failure_env
        outcome = svc.execute_graph(vc.id, {})
        assert outcome.error is not None
        assert "upstream synthetic failure" in outcome.error


# ---------------------------------------------------------------------------
# AC-08: Missing target version rejected
# ---------------------------------------------------------------------------

class TestMissingTarget:
    @pytest.fixture(scope="class")
    def missing_env(self):
        eng = _isolated_engine()
        SM = sessionmaker(bind=eng, autocommit=False, autoflush=False)
        session = SM()
        registry = AdapterRegistry(session)
        svc = GraphOrchestrationService(session, registry)
        yield svc, session
        session.close()
        eng.dispose()

    def test_nonexistent_target_raises(self, missing_env):
        svc, *_ = missing_env
        with pytest.raises(OrchestrationTargetNotFoundError):
            svc.execute_graph("ghost-version-id", {})


# ---------------------------------------------------------------------------
# AC-09: Missing adapter surfaces through D7
# ---------------------------------------------------------------------------

class TestMissingAdapter:
    @pytest.fixture(scope="class")
    def no_adapter_env(self):
        eng = _isolated_engine()
        SM = sessionmaker(bind=eng, autocommit=False, autoflush=False)
        session = SM()
        v = _make_version(session, "no_adapter_model")
        session.commit()
        registry = AdapterRegistry(session)  # no adapter registered
        svc = GraphOrchestrationService(session, registry)
        yield svc, v, session
        session.close()
        eng.dispose()

    def test_missing_adapter_produces_failed_outcome(self, no_adapter_env):
        svc, v, *_ = no_adapter_env
        # D7 raises ExecutionAdapterNotFoundError; D8 should propagate it
        with pytest.raises(ExecutionAdapterNotFoundError):
            svc.execute_graph(v.id, {})


# ---------------------------------------------------------------------------
# AC-12: /health still green
# ---------------------------------------------------------------------------

class TestHealthStillGreen:
    def test_health_200_ok(self):
        from fastapi.testclient import TestClient
        from backend.app.main import api

        client = TestClient(api, raise_server_exceptions=True)
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("ok") is True
        assert data.get("db") == "ok"
