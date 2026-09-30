"""D16 — Federation Integrity tests.

Proves that data flows through datasets and explicit I/O declarations, that contracts
are enforced, dataset values/hashes are the source of truth, one graph execution is one
GraphRun, results/lineage are server-authoritative, and fan-in routes values without
collisions. Real SQLite (and PostgreSQL when TEST_DATABASE_URL is set); no mocks.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, event as sa_event, inspect
from sqlalchemy.orm import Session, sessionmaker

from backend.app.adapters.base import ModelAdapter
from backend.app.config.settings import _normalize_db_url
from backend.app.persistence.database import (
    Base, ChangeEvent, Dataset, Dependency, ExecutionRun, ExecutionStep, LineageEdge,
    Model, ModelVersion, Result,
)
from backend.app.services.adapter_registry import AdapterConfigError, AdapterRegistry
from backend.app.services.change_propagation import ChangePropagationService
from backend.app.services.contract_validation import ContractViolationError
from backend.app.services.data_contract_manager import DataContractManager
from backend.app.services.dataset_values import DatasetOwnershipError, DatasetValueService
from backend.app.services.federation import FederationService, VersionNotExecutableError
from backend.app.services.model_registry import ActiveVersionConflictError, ModelRegistry
from backend.app.services.orchestration import (
    DirectInputNotAcceptedError,
    GraphOrchestrationService,
)
from backend.app.services.results_lineage import ResultsLineageService
from backend.app.ui.demo_seed import seed_demo_data


# ---------------------------------------------------------------------------
# Test adapters (explicit in-memory overrides; production uses persisted config)
# ---------------------------------------------------------------------------

class _FnAdapter(ModelAdapter):
    def __init__(self, version_id: str, fn) -> None:
        self._vid = version_id
        self._fn = fn
        self.calls: list[dict[str, Any]] = []

    @property
    def adapter_id(self) -> str:
        return f"fn-{self._vid}"

    @property
    def version_id(self) -> str:
        return self._vid

    def invoke(self, inputs: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(dict(inputs))
        return self._fn(inputs)


def _num(name: str = "value", **extra) -> dict:
    return {"name": name, "type": "float", **extra}


# ---------------------------------------------------------------------------
# Federation builder
# ---------------------------------------------------------------------------

class Fed:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.tag = uuid.uuid4().hex[:6]
        self.registry = ModelRegistry(db)
        self.federation = FederationService(db)
        self.contracts = DataContractManager(db)
        self.values = DatasetValueService(db)
        self.adapters = AdapterRegistry(db)

    def dataset(self, name: str, fields: list[dict] | None = None, **schema) -> Dataset:
        ds = Dataset(name=f"{name}-{self.tag}")
        self.db.add(ds)
        self.db.flush()
        if fields is not None:
            self.contracts.register_contract(
                dataset_id=ds.id, schema_json=json.dumps({"fields": fields, **schema}),
            )
        return ds

    def model(self, name: str, scalar: float = 1.0) -> ModelVersion:
        m = self.registry.register_model(
            name=f"{name}-{self.tag}", owner="d16", model_type="synthetic", status="active",
        )
        return self.registry.register_model_version(
            model_id=m.id, semver="1.0.0", is_active=True,
            adapter_type="synthetic", adapter_config={"scalar": scalar},
        )

    def override(self, version: ModelVersion, fn) -> _FnAdapter:
        adapter = _FnAdapter(version.id, fn)
        self.adapters.register_adapter(adapter)
        return adapter

    def orchestrator(self) -> GraphOrchestrationService:
        return GraphOrchestrationService(self.db, self.adapters)

    def propagation(self) -> ChangePropagationService:
        return ChangePropagationService(self.db, self.adapters)

    def value(self, ds: Dataset) -> Any:
        return self.values.get_value(ds.id)


def _sqlite_session() -> Session:
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    @sa_event.listens_for(eng, "connect")
    def _fk(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(bind=eng)
    return sessionmaker(bind=eng, autocommit=False, autoflush=False)()


@pytest.fixture
def db():
    session = _sqlite_session()
    yield session
    session.close()


@pytest.fixture
def fed(db) -> Fed:
    return Fed(db)


def _fan_in(fed: Fed, *, with_field_maps: bool = True):
    """
    Dataset A ──> Model A (×2) ──> Dataset A2 ──┐
                                               ├──> Model C (a + b) ──> Dataset C_out
    Dataset B ──> Model B (×3) ──> Dataset B2 ──┘
    Both A2 and B2 hold a field named "value"; C disambiguates with field maps.
    """
    src_a = fed.dataset("A", [_num(min=0)])
    src_b = fed.dataset("B", [_num(min=0)])
    a2 = fed.dataset("A2", [_num()])
    b2 = fed.dataset("B2", [_num()])
    c_out = fed.dataset("C_out", [_num("total")], additional_fields=False)
    ma, mb, mc = fed.model("ModelA", 2.0), fed.model("ModelB", 3.0), fed.model("ModelC")

    fed.federation.bind_input(ma.id, src_a.id)
    fed.federation.bind_input(mb.id, src_b.id)
    fed.federation.connect(
        producer_version_id=ma.id, dataset_id=a2.id, consumer_version_id=mc.id,
        input_field_map={"value": "a"} if with_field_maps else None,
    )
    fed.federation.connect(
        producer_version_id=mb.id, dataset_id=b2.id, consumer_version_id=mc.id,
        input_field_map={"value": "b"} if with_field_maps else None,
    )
    fed.federation.bind_output(mc.id, c_out.id)
    summer = fed.override(mc, lambda i: {"total": i["a"] + i["b"]})
    fed.values.write_external(src_a.id, {"value": 10.0})
    fed.values.write_external(src_b.id, {"value": 100.0})
    return {"src_a": src_a, "src_b": src_b, "a2": a2, "b2": b2, "c_out": c_out,
            "ma": ma, "mb": mb, "mc": mc, "summer": summer}


# ===========================================================================
# 1. Existing four-model demo executes from persisted configuration
# ===========================================================================

class TestDemoFromPersistedConfig:
    def test_demo_runs_without_in_memory_adapters(self, db):
        cfg = seed_demo_data(db)
        orch = GraphOrchestrationService(db, AdapterRegistry(db))  # nothing registered in memory

        go = orch.execute_graph(cfg["terminal_version_id"])

        assert go.success
        values = [go.step_outcomes[m["version_id"]].outputs["value"] for m in cfg["models"]]
        assert values == [100.0, 200.0, 300.0, 150.0]

    def test_seed_is_idempotent(self, db):
        first = seed_demo_data(db)
        second = seed_demo_data(db)
        assert first == second
        assert db.query(Model).count() == 4
        assert db.query(Dependency).count() == 3

    def test_seed_upgrades_a_pre_d16_database(self, db):
        """A D15-style chain (no adapter config, bindings, contracts) is upgraded in place."""
        vids, dids = [], []
        for name, ds_name in [("Fuel price", "fuel_price_output"), ("Shipping cost", "shipping_cost_output"),
                              ("Operations cost", "ops_cost_output"), ("Emissions", "emissions_output")]:
            m = Model(name=name, owner="demo", model_type="synthetic", status="active")
            db.add(m)
            db.flush()
            v = ModelVersion(model_id=m.id, semver="1.0.0", is_active=True)
            ds = Dataset(name=ds_name)
            db.add_all([v, ds])
            db.flush()
            vids.append(v.id)
            dids.append(ds.id)
        for i in range(3):
            db.add(Dependency(producer_version_id=vids[i], output_dataset_id=dids[i],
                              consumer_version_id=vids[i + 1], input_dataset_id=dids[i]))
        db.flush()

        cfg = seed_demo_data(db)

        assert [m["version_id"] for m in cfg["models"]] == vids
        assert db.query(Dependency).count() == 3
        go = GraphOrchestrationService(db, AdapterRegistry(db)).execute_graph(cfg["terminal_version_id"])
        assert go.success
        assert go.step_outcomes[vids[-1]].outputs == {"value": 150.0}


# ===========================================================================
# 2–5. Dataset values, hashes, no-op, change events
# ===========================================================================

class TestDatasetValues:
    def test_outputs_are_published_to_datasets(self, fed):
        f = _fan_in(fed)
        fed.orchestrator().execute_graph(f["mc"].id)
        assert fed.value(f["a2"]) == {"value": 20.0}
        assert fed.value(f["b2"]) == {"value": 300.0}
        assert fed.value(f["c_out"]) == {"total": 320.0}

    def test_current_hash_is_hash_of_canonical_value(self, fed):
        f = _fan_in(fed)
        fed.orchestrator().execute_graph(f["mc"].id)
        for ds in (f["src_a"], f["a2"], f["c_out"]):
            fed.db.refresh(ds)
            assert ds.current_hash == hashlib.sha256(ds.current_value.encode()).hexdigest()

    def test_same_value_is_a_no_op(self, fed):
        ds = fed.dataset("src", [_num("price"), _num("volume")])
        fed.values.write_external(ds.id, {"price": 5.0, "volume": 2.0})
        events_before = fed.db.query(ChangeEvent).count()

        again = fed.values.write_external(ds.id, {"price": 5.0, "volume": 2.0})
        reordered = fed.values.write_external(ds.id, {"volume": 2.0, "price": 5.0})

        assert again.changed is False and again.event is None
        assert reordered.changed is False  # canonical JSON: key order does not matter
        assert fed.db.query(ChangeEvent).count() == events_before

    def test_integer_and_float_encodings_are_the_same_value(self, fed):
        """JSON clients send 100 where the seed stored 100.0; a float contract makes them equal."""
        ds = fed.dataset("src", [_num()])
        fed.values.write_external(ds.id, {"value": 100.0})
        assert fed.values.write_external(ds.id, {"value": 100}).changed is False
        fed.db.refresh(ds)
        assert ds.current_value == '{"value":100.0}'

    def test_changed_value_creates_change_event(self, fed):
        ds = fed.dataset("src", [_num()])
        first = fed.values.write_external(ds.id, {"value": 5.0})
        second = fed.values.write_external(
            ds.id, {"value": 7.5}, source_ref="unit-test", triggered_by="tester",
        )

        ev = second.event
        assert second.changed is True
        assert json.loads(ev.old_value_json) == {"value": 5.0}
        assert json.loads(ev.new_value_json) == {"value": 7.5}
        assert ev.old_hash == first.event.new_hash
        assert ev.new_hash != ev.old_hash
        assert (ev.source_type, ev.source_ref, ev.triggered_by) == ("external", "unit-test", "tester")
        assert fed.value(ds) == {"value": 7.5}

    def test_model_produced_dataset_rejects_external_writes(self, fed):
        f = _fan_in(fed)
        with pytest.raises(DatasetOwnershipError):
            fed.values.write_external(f["a2"].id, {"value": 1.0})


# ===========================================================================
# 6. Propagation only affects downstream models
# ===========================================================================

class TestPropagationScope:
    def test_change_reruns_only_consumers_and_downstream(self, fed):
        f = _fan_in(fed)
        fed.orchestrator().execute_graph(f["mc"].id)
        b_results_before = fed.db.query(Result).filter_by(model_version_id=f["mb"].id).count()

        change = fed.propagation().apply_dataset_change(f["src_a"].id, {"value": 50.0})

        prop = change.propagation
        assert change.changed and prop.success
        assert prop.execution_order == [f["ma"].id, f["mc"].id]
        assert f["mb"].id not in prop.graph_outcome.step_outcomes
        assert fed.db.query(Result).filter_by(model_version_id=f["mb"].id).count() == b_results_before
        # C combined the new A value with B's persisted value from the earlier run.
        assert fed.value(f["c_out"]) == {"total": 100.0 + 300.0}

    def test_failed_propagation_is_retried_by_resubmitting_the_value(self, fed):
        src = fed.dataset("src", [_num()])
        out = fed.dataset("out", [_num()])
        m = fed.model("Flaky")
        fed.federation.bind_input(m.id, src.id)
        fed.federation.bind_output(m.id, out.id)
        state = {"fail": True}

        def flaky(i):
            if state["fail"]:
                raise RuntimeError("transient outage")
            return {"value": i["value"]}

        fed.override(m, flaky)
        prop = fed.propagation()

        first = prop.apply_dataset_change(src.id, {"value": 3.0})
        assert first.propagation.success is False
        assert fed.value(out) is None

        state["fail"] = False
        retry = prop.apply_dataset_change(src.id, {"value": 3.0})
        assert retry.changed is False
        assert retry.propagation is not None and retry.propagation.success
        assert fed.value(out) == {"value": 3.0}

        settled = prop.apply_dataset_change(src.id, {"value": 3.0})
        assert settled.changed is False and settled.propagation is None


# ===========================================================================
# 7–8. Contract enforcement
# ===========================================================================

class TestContracts:
    def test_invalid_external_value_is_rejected(self, fed):
        ds = fed.dataset("src", [_num(min=0, unit="USD/t")])
        with pytest.raises(ContractViolationError) as exc:
            fed.values.write_external(ds.id, {"value": -1.0})
        assert exc.value.violations[0].field == "value"
        assert fed.value(ds) is None
        assert fed.db.query(ChangeEvent).count() == 0

    @pytest.mark.parametrize("record, reason", [
        ({}, "required field is missing"),
        ({"value": None}, "null is not allowed"),
        ({"value": "12"}, "expected number"),
        ({"value": True}, "expected number"),
        ({"value": 1e9}, "above maximum"),
        ({"value": 1.0, "extra": 2}, "not declared"),
    ])
    def test_contract_rules(self, fed, record, reason):
        ds = fed.dataset("src", [_num(min=0, max=1000)], additional_fields=False)
        with pytest.raises(ContractViolationError) as exc:
            fed.values.write_external(ds.id, record)
        assert reason in str(exc.value)

    def test_nullable_and_optional_fields_accepted(self, fed):
        ds = fed.dataset("src", [_num(), _num("note_count", nullable=True, required=False)])
        assert fed.values.write_external(ds.id, {"value": 1.0, "note_count": None}).changed
        assert fed.values.write_external(ds.id, {"value": 2.0}).changed

    def test_invalid_input_prevents_execution(self, fed):
        src = fed.dataset("src")                       # uncontracted when written
        m = fed.model("Consumer")
        fed.federation.bind_input(m.id, src.id)
        adapter = fed.override(m, lambda i: i)
        fed.values.write_external(src.id, {"value": -5.0})
        fed.contracts.register_contract(                # contract tightened afterwards
            dataset_id=src.id, schema_json=json.dumps({"fields": [_num(min=0)]}),
        )

        go = fed.orchestrator().execute_graph(m.id)

        assert go.success is False
        assert adapter.calls == []                     # the model never ran
        assert "Input contract violation" in go.error and "below minimum" in go.error

    def test_invalid_output_fails_step_and_blocks_downstream(self, fed):
        src = fed.dataset("src", [_num()])
        mid = fed.dataset("mid", [_num(max=100)])
        m1, m2 = fed.model("Producer"), fed.model("Consumer", 2.0)
        fed.federation.bind_input(m1.id, src.id)
        fed.federation.connect(producer_version_id=m1.id, dataset_id=mid.id, consumer_version_id=m2.id)
        fed.override(m1, lambda i: {"value": i["value"] * 1000})
        fed.values.write_external(src.id, {"value": 1.0})

        go = fed.orchestrator().execute_graph(m2.id)

        assert go.success is False
        assert go.first_failure_version_id == m1.id
        assert "Output contract violation" in go.error and "above maximum" in go.error
        assert m2.id not in go.step_outcomes
        assert fed.value(mid) is None
        assert fed.db.query(Result).filter_by(run_id=go.run_id).count() == 0


# ===========================================================================
# 9. Version / status enforcement
# ===========================================================================

class TestVersionEnforcement:
    def _single(self, fed):
        src = fed.dataset("src", [_num()])
        v = fed.model("Model")
        fed.federation.bind_input(v.id, src.id)
        fed.values.write_external(src.id, {"value": 1.0})
        return v

    def test_inactive_version_cannot_execute(self, fed):
        v = self._single(fed)
        fed.registry.deactivate_version(v.id)
        with pytest.raises(VersionNotExecutableError, match="not active"):
            fed.orchestrator().execute_graph(v.id)
        assert fed.db.query(ExecutionRun).count() == 0

    @pytest.mark.parametrize("status", ["deprecated", "retired"])
    def test_deprecated_or_retired_model_cannot_execute(self, fed, status):
        v = self._single(fed)
        fed.registry.set_model_status(v.model_id, status)
        with pytest.raises(VersionNotExecutableError, match=status):
            fed.orchestrator().execute_graph(v.id)

    def test_inactive_upstream_blocks_whole_plan(self, fed):
        f = _fan_in(fed)
        fed.registry.deactivate_version(f["mb"].id)
        with pytest.raises(VersionNotExecutableError):
            fed.orchestrator().execute_graph(f["mc"].id)
        assert fed.db.query(ExecutionRun).count() == 0

    def test_single_active_version_per_model(self, fed):
        v1 = fed.model("Versioned")
        with pytest.raises(ActiveVersionConflictError):
            fed.registry.register_model_version(model_id=v1.model_id, semver="2.0.0", is_active=True)
        v2 = fed.registry.register_model_version(model_id=v1.model_id, semver="2.0.0")
        fed.registry.activate_version(v2.id)
        fed.db.refresh(v1)
        assert v1.is_active is False
        assert fed.registry.get_active_version(v1.model_id).id == v2.id

    def test_adapter_config_validated_and_persisted(self, fed):
        v = fed.model("Scaled", scalar=4.0)
        assert (v.adapter_type, json.loads(v.adapter_config)) == ("synthetic", {"scalar": 4.0})
        with pytest.raises(AdapterConfigError):
            fed.registry.register_model_version(
                model_id=v.model_id, semver="9.0.0", adapter_type="no-such-type",
            )
        with pytest.raises(AdapterConfigError):
            fed.registry.register_model_version(
                model_id=v.model_id, semver="9.0.1", adapter_type="synthetic",
                adapter_config={"scalar": "four"},
            )


# ===========================================================================
# 10–11. Server-authoritative results; persistence failures surfaced
# ===========================================================================

class TestServerAuthoritativeResults:
    def test_results_follow_declared_outputs(self, fed):
        f = _fan_in(fed)
        go = fed.orchestrator().execute_graph(f["mc"].id)
        results = ResultsLineageService(fed.db).list_results_for_run(go.run_id)
        assert {(r.model_version_id, r.dataset_id) for r in results} == {
            (f["ma"].id, f["a2"].id), (f["mb"].id, f["b2"].id), (f["mc"].id, f["c_out"].id),
        }
        assert sorted(go.result_ids) == sorted(r.id for r in results)

    def test_output_field_map_shapes_dataset_record(self, fed):
        src = fed.dataset("src", [_num()])
        out = fed.dataset("priced", [_num("price_usd")], additional_fields=False)
        m = fed.model("Renamer", 1.0)
        fed.federation.bind_input(m.id, src.id)
        fed.federation.bind_output(m.id, out.id, field_map={"value": "price_usd"})
        fed.values.write_external(src.id, {"value": 9.0})
        assert fed.orchestrator().execute_graph(m.id).success
        assert fed.value(out) == {"price_usd": 9.0}

    def test_result_persistence_failure_is_surfaced(self, fed):
        src = fed.dataset("src", [_num()])
        out = fed.dataset("uncontracted_out")
        m = fed.model("Unserializable")
        fed.federation.bind_input(m.id, src.id)
        fed.federation.bind_output(m.id, out.id)
        fed.override(m, lambda i: {"value": object()})
        fed.values.write_external(src.id, {"value": 1.0})

        go = fed.orchestrator().execute_graph(m.id)

        assert go.success is False and go.status == "failed"
        assert "Result persistence failed" in go.error
        run = fed.db.get(ExecutionRun, go.run_id)
        assert run.status == "failed" and "Result persistence failed" in run.error_message
        assert go.result_ids == ()
        assert fed.value(out) is None

    def test_direct_input_rejected_when_no_model_accepts_it(self, fed):
        f = _fan_in(fed)
        with pytest.raises(DirectInputNotAcceptedError):
            fed.orchestrator().execute_graph(f["mc"].id, {"value": 1.0})


# ===========================================================================
# 12–13. One graph execution = one GraphRun containing every step
# ===========================================================================

class TestGraphRun:
    def test_one_run_with_all_steps(self, fed):
        f = _fan_in(fed)
        go = fed.orchestrator().execute_graph(f["mc"].id, triggered_by="tester")

        runs = fed.db.query(ExecutionRun).all()
        assert [r.id for r in runs] == [go.run_id]
        run = runs[0]
        assert (run.run_kind, run.status, run.executor) == ("graph", "succeeded", "in_process")
        assert run.target_version_id == f["mc"].id and run.triggered_by == "tester"
        assert run.started_at is not None and run.finished_at is not None
        steps = fed.db.query(ExecutionStep).filter_by(run_id=run.id).order_by(ExecutionStep.step_order).all()
        assert [s.model_version_id for s in steps] == go.execution_order
        assert go.execution_order[-1] == f["mc"].id
        assert {s.status for s in steps} == {"succeeded"}
        assert {o.run_id for o in go.step_outcomes.values()} == {run.id}

    def test_failure_marks_remaining_steps_skipped(self, fed):
        src = fed.dataset("src", [_num()])
        d1, d2 = fed.dataset("d1", [_num()]), fed.dataset("d2", [_num()])
        m1, m2, m3 = fed.model("S1"), fed.model("S2"), fed.model("S3")
        fed.federation.bind_input(m1.id, src.id)
        fed.federation.connect(producer_version_id=m1.id, dataset_id=d1.id, consumer_version_id=m2.id)
        fed.federation.connect(producer_version_id=m2.id, dataset_id=d2.id, consumer_version_id=m3.id)
        fed.override(m2, lambda i: (_ for _ in ()).throw(RuntimeError("step two broke")))
        fed.values.write_external(src.id, {"value": 1.0})

        go = fed.orchestrator().execute_graph(m3.id)

        steps = fed.db.query(ExecutionStep).filter_by(run_id=go.run_id).order_by(ExecutionStep.step_order).all()
        assert [s.status for s in steps] == ["succeeded", "failed", "skipped"]
        assert steps[1].error_message == "step two broke"
        assert fed.db.get(ExecutionRun, go.run_id).status == "failed"
        assert fed.value(d1) is None  # no partial publication


# ===========================================================================
# 14. Fan-in (mandatory)
# ===========================================================================

class TestFanIn:
    def test_fan_in_receives_both_datasets(self, fed):
        f = _fan_in(fed)
        go = fed.orchestrator().execute_graph(f["mc"].id)

        assert go.success
        assert f["summer"].calls == [{"a": 20.0, "b": 300.0}]
        assert go.step_outcomes[f["mc"].id].outputs == {"total": 320.0}
        order = go.execution_order
        assert order.index(f["ma"].id) < order.index(f["mc"].id)
        assert order.index(f["mb"].id) < order.index(f["mc"].id)

    def test_field_name_collision_is_an_explicit_error(self, fed):
        f = _fan_in(fed, with_field_maps=False)
        go = fed.orchestrator().execute_graph(f["mc"].id)

        assert go.success is False
        assert go.first_failure_version_id == f["mc"].id
        assert "Input collision on 'value'" in go.error
        assert f["summer"].calls == []
        assert fed.value(f["a2"]) is None and fed.value(f["b2"]) is None


# ===========================================================================
# 15. Lineage belongs to the graph execution
# ===========================================================================

class TestLineage:
    def test_lineage_edges_carry_run_step_and_dataset_provenance(self, fed):
        f = _fan_in(fed)
        go = fed.orchestrator().execute_graph(f["mc"].id)
        rl = ResultsLineageService(fed.db)
        by_version = {r.model_version_id: r for r in rl.list_results_for_run(go.run_id)}

        edges = rl.list_lineage_for_run(go.run_id)
        assert {e.run_id for e in edges} == {go.run_id}

        into_c = rl.list_lineage_to_result(by_version[f["mc"].id].id)
        assert {(e.source_dataset_id, e.source_result_id) for e in into_c} == {
            (f["a2"].id, by_version[f["ma"].id].id), (f["b2"].id, by_version[f["mb"].id].id),
        }
        assert {e.step_id for e in into_c} == {go.step_outcomes[f["mc"].id].step_id}
        assert {e.target_dataset_id for e in into_c} == {f["c_out"].id}

        into_a = rl.list_lineage_to_result(by_version[f["ma"].id].id)
        (src_edge,) = into_a
        source_event = fed.db.get(ChangeEvent, src_edge.source_change_event_id)
        assert src_edge.source_dataset_id == f["src_a"].id and src_edge.source_result_id is None
        assert source_event.dataset_id == f["src_a"].id and source_event.source_type == "external"

    def test_cross_run_lineage_points_to_the_run_that_produced_the_input(self, fed):
        f = _fan_in(fed)
        first = fed.orchestrator().execute_graph(f["mc"].id)
        b_result = fed.db.query(Result).filter_by(run_id=first.run_id, model_version_id=f["mb"].id).one()

        prop = fed.propagation().apply_dataset_change(f["src_a"].id, {"value": 1.0}).propagation

        c_result = fed.db.query(Result).filter_by(run_id=prop.run_id, model_version_id=f["mc"].id).one()
        b2_edge = next(e for e in ResultsLineageService(fed.db).list_lineage_to_result(c_result.id)
                       if e.source_dataset_id == f["b2"].id)
        assert b2_edge.run_id == prop.run_id
        assert b2_edge.source_result_id == b_result.id
        assert fed.db.get(ChangeEvent, b2_edge.source_change_event_id).produced_by_run_id == first.run_id

    def test_propagation_links_events_to_the_run(self, fed):
        f = _fan_in(fed)
        fed.orchestrator().execute_graph(f["mc"].id)
        change = fed.propagation().apply_dataset_change(f["src_a"].id, {"value": 2.0})
        event = fed.db.get(ChangeEvent, change.change_event_id)
        assert event.run_id == change.propagation.run_id
        published = fed.db.query(ChangeEvent).filter_by(produced_by_run_id=change.propagation.run_id).all()
        assert {e.dataset_id for e in published} == {f["a2"].id, f["c_out"].id}


# ===========================================================================
# Transactions commit before the response is sent
# ===========================================================================

def test_api_sessions_commit_before_responding():
    """
    FastAPI's default 'request' scope ends yield-dependencies after the response is sent,
    so a client could read before the commit and a commit failure would follow a 2xx.
    Every get_db dependency must therefore be function-scoped.
    """
    import re
    api_dir = Path(__file__).resolve().parent.parent / "backend" / "app" / "api"
    offenders = []
    for path in api_dir.rglob("*.py"):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for call in re.findall(r"Depends\(get_db[^)]*\)", line):
                if 'scope="function"' not in call:
                    offenders.append(f"{path.name}:{lineno}: {call}")
    assert offenders == []


# ===========================================================================
# Migration 002
# ===========================================================================

class TestMigration:
    def test_upgrade_matches_orm_and_downgrades(self):
        from alembic import command
        from alembic.config import Config

        db_file = Path(tempfile.mkdtemp()) / "d16_migration.db"
        url = f"sqlite:///{db_file}"
        cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
        cfg.set_main_option("script_location",
                            str(Path(__file__).resolve().parent.parent / "alembic"))
        cfg.attributes["sqlalchemy_url"] = url

        command.upgrade(cfg, "001")
        eng = create_engine(url)
        with eng.begin() as conn:
            conn.exec_driver_sql("INSERT INTO execution_run (id, status) VALUES ('pre-d16', 'succeeded')")
        command.upgrade(cfg, "head")

        insp = inspect(eng)
        for name, table in Base.metadata.tables.items():
            assert {c["name"] for c in insp.get_columns(name)} == {c.name for c in table.columns}, name
        with eng.connect() as conn:
            assert conn.exec_driver_sql("SELECT run_kind FROM execution_run").scalar() == "legacy"
        eng.dispose()

        command.downgrade(cfg, "001")
        eng = create_engine(url)
        assert "model_io_binding" not in inspect(eng).get_table_names()
        eng.dispose()


# ===========================================================================
# PostgreSQL (skipped unless TEST_DATABASE_URL is set). Runs inside a transaction
# that is rolled back, so the target database is left unchanged.
# ===========================================================================

_PG_URL = _normalize_db_url(os.environ.get("TEST_DATABASE_URL", ""))


@pytest.mark.skipif(not _PG_URL.startswith("postgresql"), reason="TEST_DATABASE_URL not set")
class TestD16OnPostgreSQL:
    @pytest.fixture(scope="class")
    def pg(self):
        eng = create_engine(_PG_URL, connect_args={"prepare_threshold": None})
        conn = eng.connect()
        trans = conn.begin()
        Base.metadata.create_all(bind=conn)
        session = Session(bind=conn, join_transaction_mode="create_savepoint",
                          autoflush=False)
        yield session
        session.close()
        trans.rollback()
        conn.close()
        eng.dispose()

    def test_fan_in_graph_run_and_lineage_on_pg(self, pg):
        fed = Fed(pg)
        f = _fan_in(fed)
        go = fed.orchestrator().execute_graph(f["mc"].id)
        assert go.success and go.step_outcomes[f["mc"].id].outputs == {"total": 320.0}
        assert pg.query(ExecutionStep).filter_by(run_id=go.run_id).count() == 3
        assert pg.query(LineageEdge).filter_by(run_id=go.run_id).count() == 4
        assert fed.value(f["c_out"]) == {"total": 320.0}

    def test_propagation_no_op_and_contracts_on_pg(self, pg):
        fed = Fed(pg)
        f = _fan_in(fed)
        fed.orchestrator().execute_graph(f["mc"].id)
        prop = fed.propagation()
        change = prop.apply_dataset_change(f["src_a"].id, {"value": 50.0})
        assert change.propagation.execution_order == [f["ma"].id, f["mc"].id]
        assert fed.value(f["c_out"]) == {"total": 400.0}
        assert prop.apply_dataset_change(f["src_a"].id, {"value": 50.0}).changed is False
        with pytest.raises(ContractViolationError):
            fed.values.write_external(f["src_b"].id, {"value": -1.0})

    def test_inactive_version_blocked_on_pg(self, pg):
        fed = Fed(pg)
        f = _fan_in(fed)
        fed.registry.deactivate_version(f["ma"].id)
        with pytest.raises(VersionNotExecutableError):
            fed.orchestrator().execute_graph(f["mc"].id)
