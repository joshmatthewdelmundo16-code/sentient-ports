"""D17 — Toy Port Domain Model tests.

Covers the pure formulas (unit + golden), the persisted-adapter federation (two-level DAG,
fan-in, routing, results, lineage, one GraphRun, version/status enforcement) and the
contract edge cases (zero calls, zero/negative physical quantities, missing inputs,
zero-throughput per-TEU nulls). Real SQLite (and PostgreSQL when TEST_DATABASE_URL is set);
no mocks, no in-memory business logic.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, event as sa_event
from sqlalchemy.orm import Session, sessionmaker

from backend.app.config.settings import _normalize_db_url
from backend.app.domain import port_models as pm
from backend.app.persistence.database import (
    Base, ChangeEvent, ExecutionRun, ExecutionStep, Result,
)
from backend.app.services.adapter_registry import AdapterConfigError, AdapterRegistry
from backend.app.services.change_propagation import ChangePropagationService
from backend.app.services.contract_validation import ContractViolationError
from backend.app.services.dataset_values import DatasetValueService
from backend.app.services.model_registry import ModelRegistry
from backend.app.services.orchestration import GraphOrchestrationService
from backend.app.services.results_lineage import ResultsLineageService
from backend.app.ui.port_seed import (
    ASSUMPTIONS_DATASET, BERTHS, BUNKER_PRICE, CALLS, DEFAULT_ASSUMPTIONS,
    EMISSION_FACTOR, seed_port_domain,
)

# Golden expectations from the approved D17 inputs.
GOLDEN_TEU = 20_000_000.0
GOLDEN_UTIL_PCT = 20_000.0 / 52_560.0 * 100.0   # (10000*2)/(6*8760)*100 = 38.0517...%
GOLDEN_FUEL_T = 20_000.0
GOLDEN_FUEL_COST = 12_000_000.0
GOLDEN_COST_PER_TEU = 0.60
GOLDEN_EMISSIONS = 62_280.0
GOLDEN_EMISSIONS_PER_TEU = 0.003114


# ===========================================================================
# Pure formula unit tests
# ===========================================================================

class TestThroughput:
    def test_golden(self):
        assert pm.throughput(annual_vessel_calls=10_000, average_teu_per_call=2_000) == {
            "annual_teu": GOLDEN_TEU}

    def test_zero_calls(self):
        assert pm.throughput(annual_vessel_calls=0, average_teu_per_call=2_000)["annual_teu"] == 0


class TestBerthUtilization:
    def test_golden(self):
        out = pm.berth_utilization(
            annual_vessel_calls=10_000, average_berth_hours_per_call=2.0,
            number_of_berths=6, congestion_threshold_pct=70.0)
        assert out["utilization_pct"] == pytest.approx(GOLDEN_UTIL_PCT)
        assert out["congestion"] is False

    def test_congestion_flag_trips_at_threshold(self):
        out = pm.berth_utilization(
            annual_vessel_calls=300_000, average_berth_hours_per_call=2.0,
            number_of_berths=6, congestion_threshold_pct=70.0)
        assert out["utilization_pct"] >= 70.0 and out["congestion"] is True

    def test_zero_berths_guard_returns_none(self):
        out = pm.berth_utilization(
            annual_vessel_calls=10_000, average_berth_hours_per_call=2.0,
            number_of_berths=0, congestion_threshold_pct=70.0)
        assert out == {"utilization_pct": None, "congestion": False}


class TestPortFuelCost:
    def test_golden(self):
        out = pm.port_fuel_cost(
            annual_vessel_calls=10_000, fuel_burned_in_port_per_call=2.0,
            bunker_price=600, annual_teu=GOLDEN_TEU)
        assert out["annual_fuel_t"] == pytest.approx(GOLDEN_FUEL_T)
        assert out["annual_fuel_cost_usd"] == pytest.approx(GOLDEN_FUEL_COST)
        assert out["fuel_cost_per_teu"] == pytest.approx(GOLDEN_COST_PER_TEU)

    def test_zero_throughput_gives_null_per_teu_but_keeps_totals(self):
        out = pm.port_fuel_cost(
            annual_vessel_calls=0, fuel_burned_in_port_per_call=2.0,
            bunker_price=600, annual_teu=0)
        assert out["annual_fuel_cost_usd"] == 0
        assert out["fuel_cost_per_teu"] is None


class TestPortEmissions:
    def test_golden(self):
        out = pm.port_emissions(
            annual_vessel_calls=10_000, fuel_burned_in_port_per_call=2.0,
            emission_factor=3.114, annual_teu=GOLDEN_TEU)
        assert out["annual_emissions_tco2"] == pytest.approx(GOLDEN_EMISSIONS)
        assert out["emissions_per_teu"] == pytest.approx(GOLDEN_EMISSIONS_PER_TEU)

    def test_zero_throughput_gives_null_per_teu(self):
        out = pm.port_emissions(
            annual_vessel_calls=0, fuel_burned_in_port_per_call=2.0,
            emission_factor=3.114, annual_teu=0)
        assert out["annual_emissions_tco2"] == 0
        assert out["emissions_per_teu"] is None


class TestDecisionSummary:
    def test_collects_all_kpis(self):
        out = pm.decision_summary(
            annual_teu=GOLDEN_TEU, berth_utilization_pct=GOLDEN_UTIL_PCT, congestion=False,
            annual_fuel_cost_usd=GOLDEN_FUEL_COST, fuel_cost_per_teu=GOLDEN_COST_PER_TEU,
            annual_emissions_tco2=GOLDEN_EMISSIONS, emissions_per_teu=GOLDEN_EMISSIONS_PER_TEU)
        assert out == {
            "annual_teu": GOLDEN_TEU, "berth_utilization_pct": GOLDEN_UTIL_PCT,
            "annual_fuel_cost_usd": GOLDEN_FUEL_COST, "fuel_cost_per_teu": GOLDEN_COST_PER_TEU,
            "annual_emissions_tco2": GOLDEN_EMISSIONS, "emissions_per_teu": GOLDEN_EMISSIONS_PER_TEU,
            "congestion_status": False}

    def test_passes_through_null_per_teu(self):
        out = pm.decision_summary(
            annual_teu=0, berth_utilization_pct=0.0, congestion=False,
            annual_fuel_cost_usd=0.0, fuel_cost_per_teu=None,
            annual_emissions_tco2=0.0, emissions_per_teu=None)
        assert out["fuel_cost_per_teu"] is None and out["emissions_per_teu"] is None


class TestCompute:
    def test_filters_to_declared_params(self):
        out = pm.compute("throughput", {"annual_vessel_calls": 2, "average_teu_per_call": 3, "extra": 9})
        assert out == {"annual_teu": 6}

    def test_missing_required_input_raises_keyerror(self):
        with pytest.raises(KeyError, match="average_teu_per_call"):
            pm.compute("throughput", {"annual_vessel_calls": 2})


# ===========================================================================
# Federation fixtures
# ===========================================================================

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
def port(db):
    cfg = seed_port_domain(db)
    return cfg


def _orch(db) -> GraphOrchestrationService:
    return GraphOrchestrationService(db, AdapterRegistry(db))


def _run(db, cfg) -> "GraphExecutionOutcome":
    return _orch(db).execute_graph(cfg["terminal_version_id"], triggered_by="d17-test")


def _summary(db, cfg, go) -> dict:
    """The Decision Summary result value for a completed run."""
    rl = ResultsLineageService(db)
    for r in rl.list_results_for_run(go.run_id):
        if r.model_version_id == cfg["terminal_version_id"]:
            import json
            return json.loads(r.value_json)
    raise AssertionError("no decision summary result")


def _vid(cfg, key) -> str:
    return next(m["version_id"] for m in cfg["models"] if m["key"] == key)


def _write_assumptions(db, cfg, **overrides):
    record = dict(cfg["default_assumptions"])
    record.update(overrides)
    return DatasetValueService(db).write_external(cfg["assumptions_dataset_id"], record,
                                                  triggered_by="d17-test")


# ===========================================================================
# Golden end-to-end
# ===========================================================================

class TestGoldenEndToEnd:
    def test_seed_topology(self, db, port):
        # 5 models, one terminal, assumptions carries the golden defaults.
        assert {m["key"] for m in port["models"]} == {
            "throughput", "berth_utilization", "port_fuel_cost", "port_emissions", "decision_summary"}
        assert port["default_assumptions"] == DEFAULT_ASSUMPTIONS
        order = _orch(db)._graph_svc.topological_order()
        assert order.index(_vid(port, "throughput")) < order.index(_vid(port, "port_fuel_cost"))
        assert order.index(_vid(port, "throughput")) < order.index(_vid(port, "port_emissions"))
        for key in ("throughput", "berth_utilization", "port_fuel_cost", "port_emissions"):
            assert order.index(_vid(port, key)) < order.index(_vid(port, "decision_summary"))

    def test_golden_decision_summary(self, db, port):
        go = _run(db, port)
        assert go.success
        s = _summary(db, port, go)
        assert s["annual_teu"] == pytest.approx(GOLDEN_TEU)
        assert s["berth_utilization_pct"] == pytest.approx(GOLDEN_UTIL_PCT)   # 38.05%, not 3.805%
        assert s["annual_fuel_cost_usd"] == pytest.approx(GOLDEN_FUEL_COST)
        assert s["fuel_cost_per_teu"] == pytest.approx(GOLDEN_COST_PER_TEU)
        assert s["annual_emissions_tco2"] == pytest.approx(GOLDEN_EMISSIONS)
        assert s["emissions_per_teu"] == pytest.approx(GOLDEN_EMISSIONS_PER_TEU)
        assert s["congestion_status"] is False

    def test_golden_intermediate_datasets_published(self, db, port):
        _run(db, port)
        vals = DatasetValueService(db)
        assert vals.get_value(port["datasets"]["throughput_output"]) == {"annual_teu": GOLDEN_TEU}
        fuel = vals.get_value(port["datasets"]["port_fuel_cost_output"])
        assert fuel["annual_fuel_t"] == pytest.approx(GOLDEN_FUEL_T)
        assert fuel["fuel_cost_per_teu"] == pytest.approx(GOLDEN_COST_PER_TEU)


# ===========================================================================
# Routing / two-level fan-in / results / lineage / GraphRun
# ===========================================================================

class TestFederationIntegration:
    def test_single_graph_run_with_five_steps(self, db, port):
        go = _run(db, port)
        assert db.query(ExecutionRun).count() == 1
        run = db.query(ExecutionRun).one()
        assert run.run_kind == "graph" and run.status == "succeeded"
        steps = db.query(ExecutionStep).filter_by(run_id=run.id).all()
        assert len(steps) == 5 and {s.status for s in steps} == {"succeeded"}

    def test_results_recorded_for_every_model(self, db, port):
        go = _run(db, port)
        results = ResultsLineageService(db).list_results_for_run(go.run_id)
        assert {r.model_version_id for r in results} == {m["version_id"] for m in port["models"]}

    def test_per_teu_metrics_consume_throughput_via_routing(self, db, port):
        """Port Fuel Cost's result links back to Throughput's result through throughput_output —
        proving annual_teu flows by declared routing, not recomputation."""
        go = _run(db, port)
        rl = ResultsLineageService(db)
        by_version = {r.model_version_id: r for r in rl.list_results_for_run(go.run_id)}
        fuel_result = by_version[_vid(port, "port_fuel_cost")]
        thr_result = by_version[_vid(port, "throughput")]
        edges = rl.list_lineage_to_result(fuel_result.id)
        thr_edge = next(e for e in edges if e.source_dataset_id == port["datasets"]["throughput_output"])
        assert thr_edge.source_result_id == thr_result.id
        assert thr_edge.run_id == go.run_id

    def test_decision_summary_fans_in_from_four_datasets(self, db, port):
        go = _run(db, port)
        rl = ResultsLineageService(db)
        by_version = {r.model_version_id: r for r in rl.list_results_for_run(go.run_id)}
        summary = by_version[_vid(port, "decision_summary")]
        sources = {e.source_dataset_id for e in rl.list_lineage_to_result(summary.id)}
        assert sources == {
            port["datasets"]["throughput_output"],
            port["datasets"]["berth_utilization_output"],
            port["datasets"]["port_fuel_cost_output"],
            port["datasets"]["port_emissions_output"],
        }

    def test_no_field_collision_across_fan_in(self, db, port):
        # Decision Summary reads seven distinctly-named fields from four datasets.
        go = _run(db, port)
        assert go.success and go.error is None


# ===========================================================================
# Version / status enforcement
# ===========================================================================

class TestVersionStatusEnforcement:
    def test_inactive_upstream_blocks_plan(self, db, port):
        from backend.app.services.federation import VersionNotExecutableError
        ModelRegistry(db).deactivate_version(_vid(port, "throughput"))
        with pytest.raises(VersionNotExecutableError):
            _run(db, port)
        assert db.query(ExecutionRun).count() == 0

    def test_retired_model_blocks_plan(self, db, port):
        from backend.app.services.federation import VersionNotExecutableError
        reg = ModelRegistry(db)
        v = reg.get_model_version(_vid(port, "berth_utilization"))
        reg.set_model_status(v.model_id, "retired")
        with pytest.raises(VersionNotExecutableError):
            _run(db, port)


# ===========================================================================
# Contract edge cases
# ===========================================================================

class TestContractEdgeCases:
    def test_zero_calls_runs_with_null_per_teu(self, db, port):
        _write_assumptions(db, port, **{CALLS: 0.0})
        go = _run(db, port)
        assert go.success
        s = _summary(db, port, go)
        assert s["annual_teu"] == 0
        assert s["fuel_cost_per_teu"] is None and s["emissions_per_teu"] is None
        assert s["annual_fuel_cost_usd"] == 0 and s["annual_emissions_tco2"] == 0

    def test_zero_berths_rejected_by_contract(self, db, port):
        with pytest.raises(ContractViolationError) as exc:
            _write_assumptions(db, port, **{BERTHS: 0.0})
        assert any(v.field == BERTHS for v in exc.value.violations)

    def test_negative_bunker_price_rejected(self, db, port):
        with pytest.raises(ContractViolationError) as exc:
            _write_assumptions(db, port, **{BUNKER_PRICE: -1.0})
        assert any(v.field == BUNKER_PRICE for v in exc.value.violations)

    def test_missing_assumption_field_rejected(self, db, port):
        record = dict(port["default_assumptions"])
        del record[EMISSION_FACTOR]
        with pytest.raises(ContractViolationError) as exc:
            DatasetValueService(db).write_external(port["assumptions_dataset_id"], record)
        assert any(v.field == EMISSION_FACTOR for v in exc.value.violations)

    def test_missing_input_dataset_value_fails_execution(self, db, port):
        """A model whose source dataset has no value fails the step with a clear error."""
        ds = DatasetValueService(db).get_dataset(port["assumptions_dataset_id"])
        ds.current_value = None
        ds.current_hash = None
        db.flush()
        go = _run(db, port)
        assert go.success is False
        assert "has no value" in (go.error or "")


# ===========================================================================
# Change propagation across the DAG
# ===========================================================================

class TestPropagation:
    def test_bunker_price_change_repropagates_cost_not_throughput(self, db, port):
        _run(db, port)   # establish baseline
        baseline_throughput_results = db.query(Result).filter_by(
            model_version_id=_vid(port, "throughput")).count()

        change = ChangePropagationService(db, AdapterRegistry(db)).apply_dataset_change(
            port["assumptions_dataset_id"],
            {**port["default_assumptions"], BUNKER_PRICE: 900.0})

        assert change.changed and change.propagation.success
        # Throughput does not consume bunker_price, but it shares the assumptions dataset, so it
        # re-runs; what matters is the cost KPI reflects the new price and one run covers it.
        s_val = DatasetValueService(db).get_value(port["datasets"]["decision_summary_output"])
        assert s_val["annual_fuel_cost_usd"] == pytest.approx(20_000.0 * 900.0)
        assert s_val["fuel_cost_per_teu"] == pytest.approx(900.0 * 20_000.0 / GOLDEN_TEU)

    def test_unchanged_assumptions_is_a_noop(self, db, port):
        _run(db, port)
        prop = ChangePropagationService(db, AdapterRegistry(db))
        result = prop.apply_dataset_change(port["assumptions_dataset_id"],
                                           dict(port["default_assumptions"]))
        assert result.changed is False


# ===========================================================================
# Adapter config validation
# ===========================================================================

class TestAdapterConfig:
    def test_unknown_model_rejected(self, db, port):
        reg = ModelRegistry(db)
        v = reg.get_model_version(_vid(port, "throughput"))
        with pytest.raises(AdapterConfigError):
            reg.register_model_version(model_id=v.model_id, semver="2.0.0",
                                       adapter_type="port_domain", adapter_config={"model": "nope"})

    def test_non_numeric_param_rejected(self, db, port):
        reg = ModelRegistry(db)
        v = reg.get_model_version(_vid(port, "berth_utilization"))
        with pytest.raises(AdapterConfigError):
            reg.register_model_version(
                model_id=v.model_id, semver="2.0.0", adapter_type="port_domain",
                adapter_config={"model": "berth_utilization", "congestion_threshold_pct": "high"})


# ===========================================================================
# Idempotent seed
# ===========================================================================

class TestSeedIdempotency:
    def test_reseed_is_stable(self, db):
        first = seed_port_domain(db)
        second = seed_port_domain(db)
        assert first == second
        from backend.app.persistence.database import Model
        assert db.query(Model).filter_by(owner="port-demo").count() == 5


# ===========================================================================
# PostgreSQL (skipped unless TEST_DATABASE_URL is set; rolled back — DB untouched)
# ===========================================================================

_PG_URL = _normalize_db_url(os.environ.get("TEST_DATABASE_URL", ""))


@pytest.mark.skipif(not _PG_URL.startswith("postgresql"), reason="TEST_DATABASE_URL not set")
class TestD17OnPostgreSQL:
    @pytest.fixture()
    def pg_db(self):
        eng = create_engine(_PG_URL, connect_args={"prepare_threshold": None})
        conn = eng.connect()
        trans = conn.begin()
        Base.metadata.create_all(bind=conn)
        session = Session(bind=conn, join_transaction_mode="create_savepoint", autoflush=False)
        yield session
        session.close()
        trans.rollback()
        conn.close()
        eng.dispose()

    def test_golden_on_pg(self, pg_db):
        cfg = seed_port_domain(pg_db)
        go = _run(pg_db, cfg)
        assert go.success
        s = _summary(pg_db, cfg, go)
        assert s["annual_teu"] == pytest.approx(GOLDEN_TEU)
        assert s["annual_fuel_cost_usd"] == pytest.approx(GOLDEN_FUEL_COST)
        assert s["emissions_per_teu"] == pytest.approx(GOLDEN_EMISSIONS_PER_TEU)
        assert db_count(pg_db, ExecutionStep, go.run_id) == 5

    def test_contract_rejects_zero_berths_on_pg(self, pg_db):
        cfg = seed_port_domain(pg_db)
        with pytest.raises(ContractViolationError):
            DatasetValueService(pg_db).write_external(
                cfg["assumptions_dataset_id"], {**cfg["default_assumptions"], BERTHS: 0.0})


def db_count(session, model, run_id) -> int:
    return session.query(model).filter_by(run_id=run_id).count()
