"""
D9 Change Propagation tests.

Covers:
 1. Single-chain propagation A → B → C
 2. Branch propagation A → B → C  and  A → D → E
 3. Unrelated branch exclusion (X → Y not affected)
 4. Deterministic ordering (repeated call yields same order)
 5. Cycle protection
 6. Execution goes through D8/D7 (not direct adapter invocation)
 7. Upstream failure prevents downstream execution
 8. Persisted ChangeEvent consumption
 9. Duplicate event protection (already-processed guard)
10. D0–D8 tests continue to pass
11. /health remains green
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from sqlalchemy import create_engine, event as sa_event
from sqlalchemy.orm import Session, sessionmaker

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("STORAGE_ROOT", "storage_test_d9_tmp")

from backend.app.persistence.database import (  # noqa: E402
    Base,
    Dataset,
    Dependency,
    Model,
    ModelVersion,
)
from backend.app.adapters.synthetic import SyntheticAdapter  # noqa: E402
from backend.app.services.adapter_registry import AdapterRegistry  # noqa: E402
from backend.app.services.change_propagation import (  # noqa: E402
    ChangePropagationService,
    PropagationCycleError,
    PropagationEventNotFoundError,
    PropagationSourceNotFoundError,
    PropagationResult,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _isolated_engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    @sa_event.listens_for(eng, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(bind=eng)
    return eng


def _make_version(session: Session, name: str, owner: str = "d9",
                  semver: str = "1.0.0") -> ModelVersion:
    m = Model(name=name, owner=owner, model_type="python")
    session.add(m)
    session.flush()
    v = ModelVersion(model_id=m.id, semver=semver, is_active=True)
    session.add(v)
    session.flush()
    return v


def _make_dataset(session: Session, name: str) -> Dataset:
    ds = Dataset(name=name, description=f"test dataset {name}")
    session.add(ds)
    session.flush()
    return ds


def _make_dep(session: Session, producer: ModelVersion, consumer: ModelVersion,
              ds_name: str) -> Dataset:
    ds = _make_dataset(session, ds_name)
    dep = Dependency(
        producer_version_id=producer.id,
        output_dataset_id=ds.id,
        consumer_version_id=consumer.id,
        input_dataset_id=ds.id,
        dependency_kind="data",
    )
    session.add(dep)
    session.flush()
    return ds


def _adapter(version: ModelVersion, idx: int, scalar: float = 2.0) -> SyntheticAdapter:
    return SyntheticAdapter(
        adapter_id=f"d9-adapter-{idx}-{version.id}",
        version_id=version.id,
        scalar=scalar,
    )


# ---------------------------------------------------------------------------
# 1. Single-chain propagation: A → B → C
# ---------------------------------------------------------------------------

class TestSingleChainPropagation:
    @pytest.fixture(scope="class")
    def env(self):
        eng = _isolated_engine()
        SM = sessionmaker(bind=eng, autocommit=False, autoflush=False)
        session = SM()

        va = _make_version(session, "chain_A")
        vb = _make_version(session, "chain_B")
        vc = _make_version(session, "chain_C")
        ds_anchor = _make_dataset(session, "chain_anchor_ds")
        _make_dep(session, va, vb, "chain_ds_ab")
        _make_dep(session, vb, vc, "chain_ds_bc")
        session.commit()

        registry = AdapterRegistry(session)
        registry.register_adapter(_adapter(va, 1, scalar=1.0))
        registry.register_adapter(_adapter(vb, 2, scalar=2.0))
        registry.register_adapter(_adapter(vc, 3, scalar=3.0))

        svc = ChangePropagationService(session, registry)
        yield svc, va, vb, vc, ds_anchor, session
        session.close()
        eng.dispose()

    def test_propagation_succeeds(self, env):
        svc, va, vb, vc, ds, *_ = env
        event = svc.record_change(dataset_id=ds.id, source_version_id=va.id,
                                   new_value_json='{"fuel":120}')
        result = svc.propagate(event.id, {"fuel": 120.0})
        assert result.success is True

    def test_propagation_result_type(self, env):
        svc, va, vb, vc, ds, session, *_ = env
        event = svc.record_change(dataset_id=ds.id, source_version_id=va.id)
        # event already processed from previous test — use fresh one via new session
        # Each test uses the shared session so we need separate events per test
        result = svc.propagate(event.id, {})
        assert isinstance(result, PropagationResult)

    def test_all_three_executed(self, env):
        svc, va, vb, vc, ds, *_ = env
        event = svc.record_change(dataset_id=ds.id, source_version_id=va.id)
        result = svc.propagate(event.id, {"x": 1.0})
        assert set(result.execution_order) == {va.id, vb.id, vc.id}

    def test_source_is_in_affected(self, env):
        svc, va, vb, vc, ds, *_ = env
        event = svc.record_change(dataset_id=ds.id, source_version_id=va.id)
        result = svc.propagate(event.id, {})
        assert va.id in result.affected_version_ids

    def test_change_event_id_in_result(self, env):
        svc, va, vb, vc, ds, *_ = env
        event = svc.record_change(dataset_id=ds.id, source_version_id=va.id)
        result = svc.propagate(event.id, {})
        assert result.change_event_id == event.id

    def test_source_version_id_in_result(self, env):
        svc, va, vb, vc, ds, *_ = env
        event = svc.record_change(dataset_id=ds.id, source_version_id=va.id)
        result = svc.propagate(event.id, {})
        assert result.source_version_id == va.id

    # 4. Deterministic ordering
    def test_order_is_dependency_respecting(self, env):
        svc, va, vb, vc, ds, *_ = env
        event = svc.record_change(dataset_id=ds.id, source_version_id=va.id)
        result = svc.propagate(event.id, {})
        order = result.execution_order
        assert order.index(va.id) < order.index(vb.id)
        assert order.index(vb.id) < order.index(vc.id)

    def test_source_first_in_order(self, env):
        svc, va, vb, vc, ds, *_ = env
        event = svc.record_change(dataset_id=ds.id, source_version_id=va.id)
        result = svc.propagate(event.id, {})
        assert result.execution_order[0] == va.id


# ---------------------------------------------------------------------------
# 4. Deterministic ordering — repeated propagation yields same order
# ---------------------------------------------------------------------------

class TestDeterministicOrdering:
    @pytest.fixture(scope="class")
    def env(self):
        eng = _isolated_engine()
        SM = sessionmaker(bind=eng, autocommit=False, autoflush=False)
        session = SM()

        va = _make_version(session, "det_A")
        vb = _make_version(session, "det_B")
        vc = _make_version(session, "det_C")
        ds = _make_dataset(session, "det_anchor")
        _make_dep(session, va, vb, "det_ds_ab")
        _make_dep(session, vb, vc, "det_ds_bc")
        session.commit()

        registry = AdapterRegistry(session)
        registry.register_adapter(_adapter(va, 10, scalar=1.0))
        registry.register_adapter(_adapter(vb, 11, scalar=2.0))
        registry.register_adapter(_adapter(vc, 12, scalar=2.0))

        svc = ChangePropagationService(session, registry)
        yield svc, va, vb, vc, ds, session
        session.close()
        eng.dispose()

    def test_repeated_propagation_same_order(self, env):
        svc, va, vb, vc, ds, *_ = env
        e1 = svc.record_change(dataset_id=ds.id, source_version_id=va.id)
        e2 = svc.record_change(dataset_id=ds.id, source_version_id=va.id)
        r1 = svc.propagate(e1.id, {})
        r2 = svc.propagate(e2.id, {})
        assert r1.execution_order == r2.execution_order


# ---------------------------------------------------------------------------
# 2. Branch propagation: A → B → C  and  A → D → E
# ---------------------------------------------------------------------------

class TestBranchPropagation:
    @pytest.fixture(scope="class")
    def env(self):
        eng = _isolated_engine()
        SM = sessionmaker(bind=eng, autocommit=False, autoflush=False)
        session = SM()

        va = _make_version(session, "br_A")
        vb = _make_version(session, "br_B")
        vc = _make_version(session, "br_C")
        vd = _make_version(session, "br_D")
        ve = _make_version(session, "br_E")
        ds = _make_dataset(session, "br_anchor")
        # A → B → C
        _make_dep(session, va, vb, "br_ds_ab")
        _make_dep(session, vb, vc, "br_ds_bc")
        # A → D → E
        _make_dep(session, va, vd, "br_ds_ad")
        _make_dep(session, vd, ve, "br_ds_de")
        session.commit()

        registry = AdapterRegistry(session)
        for i, v in enumerate([va, vb, vc, vd, ve]):
            registry.register_adapter(_adapter(v, 20 + i, scalar=1.0))

        svc = ChangePropagationService(session, registry)
        yield svc, va, vb, vc, vd, ve, ds, session
        session.close()
        eng.dispose()

    def test_both_branches_executed(self, env):
        svc, va, vb, vc, vd, ve, ds, *_ = env
        event = svc.record_change(dataset_id=ds.id, source_version_id=va.id)
        result = svc.propagate(event.id, {})
        executed = set(result.execution_order)
        assert {va.id, vb.id, vc.id, vd.id, ve.id} == executed

    def test_branch_propagation_succeeds(self, env):
        svc, va, vb, vc, vd, ve, ds, *_ = env
        event = svc.record_change(dataset_id=ds.id, source_version_id=va.id)
        result = svc.propagate(event.id, {})
        assert result.success is True

    def test_a_before_b_and_d(self, env):
        svc, va, vb, vc, vd, ve, ds, *_ = env
        event = svc.record_change(dataset_id=ds.id, source_version_id=va.id)
        result = svc.propagate(event.id, {})
        order = result.execution_order
        a_idx = order.index(va.id)
        assert a_idx < order.index(vb.id)
        assert a_idx < order.index(vd.id)


# ---------------------------------------------------------------------------
# 3. Unrelated branch exclusion: X → Y separate from A → B → C
# ---------------------------------------------------------------------------

class TestUnrelatedBranchExclusion:
    @pytest.fixture(scope="class")
    def env(self):
        eng = _isolated_engine()
        SM = sessionmaker(bind=eng, autocommit=False, autoflush=False)
        session = SM()

        va = _make_version(session, "unrel_A")
        vb = _make_version(session, "unrel_B")
        # Unrelated branch
        vx = _make_version(session, "unrel_X")
        vy = _make_version(session, "unrel_Y")
        ds = _make_dataset(session, "unrel_anchor")
        _make_dep(session, va, vb, "unrel_ds_ab")
        _make_dep(session, vx, vy, "unrel_ds_xy")   # separate branch
        session.commit()

        registry = AdapterRegistry(session)
        registry.register_adapter(_adapter(va, 30, scalar=1.0))
        registry.register_adapter(_adapter(vb, 31, scalar=2.0))
        registry.register_adapter(_adapter(vx, 32, scalar=1.0))
        registry.register_adapter(_adapter(vy, 33, scalar=2.0))

        svc = ChangePropagationService(session, registry)
        yield svc, va, vb, vx, vy, ds, session
        session.close()
        eng.dispose()

    def test_only_affected_branch_executed(self, env):
        svc, va, vb, vx, vy, ds, *_ = env
        event = svc.record_change(dataset_id=ds.id, source_version_id=va.id)
        result = svc.propagate(event.id, {})
        assert va.id in result.execution_order
        assert vb.id in result.execution_order
        # Unrelated branch must NOT be executed
        assert vx.id not in result.execution_order
        assert vy.id not in result.execution_order

    def test_affected_version_ids_excludes_unrelated(self, env):
        svc, va, vb, vx, vy, ds, *_ = env
        event = svc.record_change(dataset_id=ds.id, source_version_id=va.id)
        result = svc.propagate(event.id, {})
        assert vx.id not in result.affected_version_ids
        assert vy.id not in result.affected_version_ids


# ---------------------------------------------------------------------------
# 5. Cycle protection
# ---------------------------------------------------------------------------

class TestCycleProtection:
    @pytest.fixture(scope="class")
    def env(self):
        eng = _isolated_engine()
        SM = sessionmaker(bind=eng, autocommit=False, autoflush=False)
        session = SM()

        va = _make_version(session, "cyc9_A")
        vb = _make_version(session, "cyc9_B")
        vc = _make_version(session, "cyc9_C")
        ds = _make_dataset(session, "cyc9_anchor")
        _make_dep(session, va, vb, "cyc9_ds_ab")
        _make_dep(session, vb, vc, "cyc9_ds_bc")
        _make_dep(session, vc, va, "cyc9_ds_ca")   # creates cycle
        session.commit()

        registry = AdapterRegistry(session)
        svc = ChangePropagationService(session, registry)
        yield svc, va, vb, vc, ds, session
        session.close()
        eng.dispose()

    def test_cyclic_graph_raises(self, env):
        svc, va, vb, vc, ds, *_ = env
        event = svc.record_change(dataset_id=ds.id, source_version_id=va.id)
        with pytest.raises(PropagationCycleError):
            svc.propagate(event.id, {})

    def test_cycle_error_has_nodes(self, env):
        svc, va, vb, vc, ds, *_ = env
        event = svc.record_change(dataset_id=ds.id, source_version_id=va.id)
        try:
            svc.propagate(event.id, {})
        except PropagationCycleError as exc:
            assert len(exc.cycle_nodes) > 0


# ---------------------------------------------------------------------------
# 7. Upstream failure behavior
# ---------------------------------------------------------------------------

class FailingAdapter(SyntheticAdapter):
    def invoke(self, inputs: dict) -> dict:
        raise RuntimeError("d9 upstream failure")


class TestUpstreamFailure:
    @pytest.fixture(scope="class")
    def env(self):
        eng = _isolated_engine()
        SM = sessionmaker(bind=eng, autocommit=False, autoflush=False)
        session = SM()

        va = _make_version(session, "fail9_A")
        vb = _make_version(session, "fail9_B")
        vc = _make_version(session, "fail9_C")
        ds = _make_dataset(session, "fail9_anchor")
        _make_dep(session, va, vb, "fail9_ds_ab")
        _make_dep(session, vb, vc, "fail9_ds_bc")
        session.commit()

        registry = AdapterRegistry(session)
        # A fails; B and C should not execute
        registry.register_adapter(
            FailingAdapter(adapter_id=f"fail9-a-{va.id}", version_id=va.id)
        )
        registry.register_adapter(_adapter(vb, 40, scalar=2.0))
        registry.register_adapter(_adapter(vc, 41, scalar=2.0))

        svc = ChangePropagationService(session, registry)
        yield svc, va, vb, vc, ds, session
        session.close()
        eng.dispose()

    def test_propagation_fails_when_source_fails(self, env):
        svc, va, vb, vc, ds, *_ = env
        event = svc.record_change(dataset_id=ds.id, source_version_id=va.id)
        result = svc.propagate(event.id, {})
        assert result.success is False

    def test_downstream_not_in_step_outcomes(self, env):
        svc, va, vb, vc, ds, *_ = env
        event = svc.record_change(dataset_id=ds.id, source_version_id=va.id)
        result = svc.propagate(event.id, {})
        assert result.graph_outcome is not None
        executed_ids = set(result.graph_outcome.step_outcomes.keys())
        assert vb.id not in executed_ids
        assert vc.id not in executed_ids

    def test_error_message_present(self, env):
        svc, va, vb, vc, ds, *_ = env
        event = svc.record_change(dataset_id=ds.id, source_version_id=va.id)
        result = svc.propagate(event.id, {})
        assert result.error is not None


# ---------------------------------------------------------------------------
# 8. Persisted ChangeEvent consumption
# ---------------------------------------------------------------------------

class TestPersistedEventConsumption:
    @pytest.fixture(scope="class")
    def env(self):
        eng = _isolated_engine()
        SM = sessionmaker(bind=eng, autocommit=False, autoflush=False)
        session = SM()

        va = _make_version(session, "persist9_A")
        vb = _make_version(session, "persist9_B")
        ds = _make_dataset(session, "persist9_anchor")
        _make_dep(session, va, vb, "persist9_ds_ab")
        session.commit()

        registry = AdapterRegistry(session)
        registry.register_adapter(_adapter(va, 50, scalar=1.0))
        registry.register_adapter(_adapter(vb, 51, scalar=2.0))

        svc = ChangePropagationService(session, registry)
        yield svc, va, vb, ds, session
        session.close()
        eng.dispose()

    def test_persisted_event_id_used(self, env):
        svc, va, vb, ds, *_ = env
        event = svc.record_change(
            dataset_id=ds.id,
            source_version_id=va.id,
            new_value_json='{"fuel":120}',
            old_value_json='{"fuel":100}',
        )
        # event is persisted (has id)
        assert event.id is not None
        result = svc.propagate(event.id, {"fuel": 120.0})
        assert result.change_event_id == event.id
        assert result.success is True

    def test_missing_event_raises(self, env):
        svc, *_ = env
        with pytest.raises(PropagationEventNotFoundError):
            svc.propagate("ghost-event-id", {})

    def test_missing_source_version_raises(self, env):
        # D16: an unknown source version is rejected when the trigger is recorded,
        # so no ChangeEvent can reference a non-existent model version.
        svc, va, vb, ds, session, *_ = env
        with pytest.raises(PropagationSourceNotFoundError):
            svc.record_change(
                dataset_id=ds.id,
                source_version_id="nonexistent-version-id",
            )


# ---------------------------------------------------------------------------
# 9. Duplicate event protection
# ---------------------------------------------------------------------------

class TestDuplicateEventProtection:
    @pytest.fixture(scope="class")
    def env(self):
        eng = _isolated_engine()
        SM = sessionmaker(bind=eng, autocommit=False, autoflush=False)
        session = SM()

        va = _make_version(session, "dup9_A")
        vb = _make_version(session, "dup9_B")
        ds = _make_dataset(session, "dup9_anchor")
        _make_dep(session, va, vb, "dup9_ds_ab")
        session.commit()

        registry = AdapterRegistry(session)
        registry.register_adapter(_adapter(va, 60, scalar=1.0))
        registry.register_adapter(_adapter(vb, 61, scalar=2.0))

        svc = ChangePropagationService(session, registry)
        yield svc, va, vb, ds, session
        session.close()
        eng.dispose()

    def test_second_propagation_returns_already_processed(self, env):
        svc, va, vb, ds, *_ = env
        event = svc.record_change(dataset_id=ds.id, source_version_id=va.id)
        # First propagation
        r1 = svc.propagate(event.id, {})
        assert r1.already_processed is False
        # Second propagation of same event
        r2 = svc.propagate(event.id, {})
        assert r2.already_processed is True

    def test_second_propagation_succeeds(self, env):
        svc, va, vb, ds, *_ = env
        event = svc.record_change(dataset_id=ds.id, source_version_id=va.id)
        svc.propagate(event.id, {})
        r2 = svc.propagate(event.id, {})
        assert r2.success is True


# ---------------------------------------------------------------------------
# 6. D8/D7 reuse verified — graph_outcome contains step_outcomes from D7
# ---------------------------------------------------------------------------

class TestD8D7Reuse:
    @pytest.fixture(scope="class")
    def env(self):
        eng = _isolated_engine()
        SM = sessionmaker(bind=eng, autocommit=False, autoflush=False)
        session = SM()

        va = _make_version(session, "reuse9_A")
        vb = _make_version(session, "reuse9_B")
        ds = _make_dataset(session, "reuse9_anchor")
        _make_dep(session, va, vb, "reuse9_ds_ab")
        session.commit()

        registry = AdapterRegistry(session)
        registry.register_adapter(_adapter(va, 70, scalar=1.0))
        registry.register_adapter(_adapter(vb, 71, scalar=3.0))

        svc = ChangePropagationService(session, registry)
        yield svc, va, vb, ds, session
        session.close()
        eng.dispose()

    def test_graph_outcome_present(self, env):
        svc, va, vb, ds, *_ = env
        event = svc.record_change(dataset_id=ds.id, source_version_id=va.id)
        result = svc.propagate(event.id, {"val": 10.0})
        assert result.graph_outcome is not None

    def test_step_outcomes_contain_run_ids(self, env):
        """Each step outcome from D7 has a run_id — proves D7 was called."""
        svc, va, vb, ds, *_ = env
        event = svc.record_change(dataset_id=ds.id, source_version_id=va.id)
        result = svc.propagate(event.id, {"val": 5.0})
        for step_out in result.graph_outcome.step_outcomes.values():
            assert step_out.run_id is not None

    def test_d7_outputs_flow_through(self, env):
        """D7 scalar outputs propagate: A(scalar=1)→B(scalar=3), val=10 → B gets 10 → 30."""
        svc, va, vb, ds, *_ = env
        event = svc.record_change(dataset_id=ds.id, source_version_id=va.id)
        result = svc.propagate(event.id, {"val": 10.0})
        b_outputs = result.graph_outcome.step_outcomes[vb.id].outputs
        assert b_outputs is not None
        assert b_outputs["val"] == pytest.approx(30.0)


# ---------------------------------------------------------------------------
# 10–11. /health still green
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
