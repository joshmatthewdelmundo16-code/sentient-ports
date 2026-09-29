"""D19 — Scenarios & Baseline tests.

Covers the persisted baseline/scenario/override domain, the read-only scenario-execution
invariant, and the deterministic baseline↔scenario comparison — all on top of the existing
D16 GraphRun machinery (no second execution engine). Real SQLite (and PostgreSQL when
TEST_DATABASE_URL is set); no mocks.

Acceptance reconciliation
-------------------------
The D19 phase doc's expected outputs (annual TEU 20,000,000; fuel cost USD 12,000,000;
emissions 62,280 tCO2; berth utilization 38.0517%) are produced by the real D17 golden
baseline — the seeded DEFAULT_ASSUMPTIONS, whose bunker_price is 600. The documented
"bunker_price 2.0 → 2.5" scenario is a ×1.25 change; applied to the real golden baseline it
is 600 → 750, which reproduces every documented comparison figure exactly:
fuel cost 12,000,000 → 15,000,000 (Δ +3,000,000) and cost/TEU 0.60 → 0.75 (Δ +0.15), with
throughput / berth / emissions unchanged. DEFAULT_ASSUMPTIONS and the D17 tests are untouched.
"""

from __future__ import annotations

import json
import os

import pytest
from sqlalchemy import create_engine, event as sa_event
from sqlalchemy.orm import Session, sessionmaker

from backend.app.config.settings import _normalize_db_url
from backend.app.persistence.database import (
    Base, Baseline, ChangeEvent, Dataset, ExecutionStep, Result, Scenario, ScenarioOverride,
)
from backend.app.services.adapter_registry import AdapterRegistry
from backend.app.services.dataset_values import DatasetValueService
from backend.app.services.results_lineage import ResultsLineageService
from backend.app.services.scenario_comparison import (
    ComparisonRunNotFoundError,
    ScenarioComparisonService,
)
from backend.app.services.scenarios import (
    DuplicateBaselineError,
    DuplicateScenarioError,
    ScenarioExecutionError,
    ScenarioNotFoundError,
    ScenarioOverrideError,
    ScenarioService,
)
from backend.app.ui.port_seed import BUNKER_PRICE, seed_port_domain

# Golden baseline (seeded DEFAULT_ASSUMPTIONS) and the ×1.25 scenario bunker price.
BASELINE_BUNKER = 600.0
SCENARIO_BUNKER = 750.0
EXP_TEU = 20_000_000.0
EXP_FUEL_T = 20_000.0
EXP_BASE_FUEL_COST = 12_000_000.0
EXP_SCEN_FUEL_COST = 15_000_000.0
EXP_BASE_COST_PER_TEU = 0.60
EXP_SCEN_COST_PER_TEU = 0.75
EXP_EMISSIONS = 62_280.0
EXP_UTIL_PCT = 20_000.0 / 52_560.0 * 100.0


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

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
    return seed_port_domain(db)


def _svc(db) -> ScenarioService:
    return ScenarioService(db, AdapterRegistry(db))


def _new_baseline(db, port, name="Golden Baseline") -> Baseline:
    return _svc(db).create_baseline(name=name, target_version_id=port["terminal_version_id"])


def _executed_baseline(db, port, name="Golden Baseline") -> Baseline:
    svc = _svc(db)
    b = svc.create_baseline(name=name, target_version_id=port["terminal_version_id"])
    go = svc.execute_baseline(b.id)
    assert go.success, go.error
    return b


def _scenario_with_bunker(db, port, baseline, price=SCENARIO_BUNKER, name="High Bunker"):
    svc = _svc(db)
    s = svc.create_scenario(baseline_id=baseline.id, name=name)
    svc.set_override(s.id, port["assumptions_dataset_id"], BUNKER_PRICE, price)
    return s


def _result_record(db, run_id, dataset_id) -> dict:
    for r in ResultsLineageService(db).list_results_for_run(run_id):
        if r.dataset_id == dataset_id:
            return json.loads(r.value_json)
    raise AssertionError(f"no result for dataset {dataset_id} in run {run_id}")


# ===========================================================================
# Baseline persistence
# ===========================================================================

class TestBaselinePersistence:
    def test_create_and_get_baseline(self, db, port):
        b = _new_baseline(db, port)
        assert b.id and b.status == "active"
        assert _svc(db).get_baseline(b.id).name == "Golden Baseline"

    def test_duplicate_baseline_name_rejected(self, db, port):
        _new_baseline(db, port)
        with pytest.raises(DuplicateBaselineError):
            _new_baseline(db, port)

    def test_execute_baseline_pins_run(self, db, port):
        b = _executed_baseline(db, port)
        assert b.baseline_run_id is not None
        summary = _result_record(db, b.baseline_run_id, port["datasets"]["decision_summary_output"])
        assert summary["annual_fuel_cost_usd"] == pytest.approx(EXP_BASE_FUEL_COST)

    def test_execute_baseline_without_target_fails(self, db, port):
        svc = _svc(db)
        b = svc.create_baseline(name="No Target")
        with pytest.raises(ScenarioExecutionError):
            svc.execute_baseline(b.id)


# ===========================================================================
# Scenario creation / linkage / overrides
# ===========================================================================

class TestScenarioAndOverrides:
    def test_create_scenario_links_baseline(self, db, port):
        b = _new_baseline(db, port)
        s = _svc(db).create_scenario(baseline_id=b.id, name="S1")
        assert s.baseline_id == b.id and s.status == "draft"

    def test_duplicate_scenario_name_within_baseline_rejected(self, db, port):
        b = _new_baseline(db, port)
        svc = _svc(db)
        svc.create_scenario(baseline_id=b.id, name="S1")
        with pytest.raises(DuplicateScenarioError):
            svc.create_scenario(baseline_id=b.id, name="S1")

    def test_same_scenario_name_allowed_across_baselines(self, db, port):
        svc = _svc(db)
        b1 = svc.create_baseline(name="B1", target_version_id=port["terminal_version_id"])
        b2 = svc.create_baseline(name="B2", target_version_id=port["terminal_version_id"])
        svc.create_scenario(baseline_id=b1.id, name="S")
        svc.create_scenario(baseline_id=b2.id, name="S")  # no error

    def test_get_unknown_scenario_raises(self, db, port):
        with pytest.raises(ScenarioNotFoundError):
            _svc(db).get_scenario("does-not-exist")

    def test_single_and_multiple_overrides_persist(self, db, port):
        b = _new_baseline(db, port)
        svc = _svc(db)
        s = svc.create_scenario(baseline_id=b.id, name="S")
        svc.set_overrides(s.id, [
            {"dataset_id": port["assumptions_dataset_id"], "field_name": BUNKER_PRICE, "value": 750.0},
            {"dataset_id": port["assumptions_dataset_id"], "field_name": "emission_factor", "value": 3.5},
        ])
        overrides = svc.list_overrides(s.id)
        assert len(overrides) == 2
        assert {o.field_name for o in overrides} == {BUNKER_PRICE, "emission_factor"}

    def test_override_replace_is_idempotent(self, db, port):
        b = _new_baseline(db, port)
        svc = _svc(db)
        s = svc.create_scenario(baseline_id=b.id, name="S")
        svc.set_override(s.id, port["assumptions_dataset_id"], BUNKER_PRICE, 700.0)
        svc.set_override(s.id, port["assumptions_dataset_id"], BUNKER_PRICE, 750.0)
        overrides = svc.list_overrides(s.id)
        assert len(overrides) == 1
        assert json.loads(overrides[0].value_json) == 750.0

    def test_override_on_produced_dataset_rejected(self, db, port):
        b = _new_baseline(db, port)
        svc = _svc(db)
        s = svc.create_scenario(baseline_id=b.id, name="S")
        with pytest.raises(ScenarioOverrideError):
            svc.set_override(s.id, port["datasets"]["throughput_output"], "annual_teu", 1.0)

    def test_resolve_overrides_shape(self, db, port):
        b = _new_baseline(db, port)
        s = _scenario_with_bunker(db, port, b)
        resolved = _svc(db).resolve_overrides(s.id)
        assert resolved == {port["assumptions_dataset_id"]: {BUNKER_PRICE: SCENARIO_BUNKER}}


# ===========================================================================
# Scenario execution + association
# ===========================================================================

class TestScenarioExecution:
    def test_scenario_run_succeeds_and_is_associated(self, db, port):
        b = _executed_baseline(db, port)
        s = _scenario_with_bunker(db, port, b)
        go = _svc(db).execute_scenario(s.id)
        assert go.success, go.error
        # scenario_run pinned + run.scenario_id + every result stamped
        s = _svc(db).get_scenario(s.id)
        assert s.scenario_run_id == go.run_id and s.status == "executed"
        results = ResultsLineageService(db).list_results_for_run(go.run_id)
        assert results and all(r.scenario_id == s.id for r in results)

    def test_inherited_and_overridden_values_resolve(self, db, port):
        b = _executed_baseline(db, port)
        s = _scenario_with_bunker(db, port, b)
        go = _svc(db).execute_scenario(s.id)
        fuel = _result_record(db, go.run_id, port["datasets"]["port_fuel_cost_output"])
        # overridden bunker changes cost; inherited calls/fuel keep fuel tonnage
        assert fuel["annual_fuel_t"] == pytest.approx(EXP_FUEL_T)          # inherited
        assert fuel["annual_fuel_cost_usd"] == pytest.approx(EXP_SCEN_FUEL_COST)  # overridden branch

    def test_invalid_override_fails_run_without_mutating_baseline(self, db, port):
        b = _executed_baseline(db, port)
        before = DatasetValueService(db).get_value(port["assumptions_dataset_id"])
        svc = _svc(db)
        s = svc.create_scenario(baseline_id=b.id, name="Bad")
        svc.set_override(s.id, port["assumptions_dataset_id"], BUNKER_PRICE, -1.0)  # violates min=0
        go = svc.execute_scenario(s.id)
        assert go.success is False
        after = DatasetValueService(db).get_value(port["assumptions_dataset_id"])
        assert after == before  # baseline assumptions untouched


# ===========================================================================
# Read-only scenario invariant (explicit regression — required)
# ===========================================================================

class TestReadOnlyScenarioInvariant:
    def test_scenario_run_is_read_only_wrt_shared_state(self, db, port):
        b = _executed_baseline(db, port)

        # Snapshot every dataset's shared value/hash + total change-event count after baseline.
        datasets = db.query(Dataset).all()
        before_values = {d.id: (d.current_value, d.current_hash) for d in datasets}
        before_events = db.query(ChangeEvent).count()
        before_model_outputs = db.query(ChangeEvent).filter_by(source_type="model_output").count()

        s = _scenario_with_bunker(db, port, b)
        go = _svc(db).execute_scenario(s.id)
        assert go.success, go.error

        # 1. baseline/shared Dataset.current_value values are unchanged
        for d in db.query(Dataset).all():
            assert (d.current_value, d.current_hash) == before_values[d.id]

        # 2. no scenario-generated output ChangeEvents were created (none at all, and the run
        #    is referenced by no ChangeEvent)
        assert db.query(ChangeEvent).count() == before_events
        assert db.query(ChangeEvent).filter_by(source_type="model_output").count() == before_model_outputs
        assert go.published_change_event_ids == ()
        assert db.query(ChangeEvent).filter_by(run_id=go.run_id).count() == 0

        # 3. the scenario GraphRun still contains its complete results and lineage
        rl = ResultsLineageService(db)
        assert len(rl.list_results_for_run(go.run_id)) == len(port["models"])  # 5
        assert len(rl.list_lineage_for_run(go.run_id)) > 0
        assert db.query(ExecutionStep).filter_by(run_id=go.run_id).count() == len(port["models"])

        # 4. downstream fan-in still works from staged values (decision summary reflects the
        #    overridden bunker even though nothing was published)
        summary = _result_record(db, go.run_id, port["datasets"]["decision_summary_output"])
        assert summary["annual_fuel_cost_usd"] == pytest.approx(EXP_SCEN_FUEL_COST)
        assert summary["fuel_cost_per_teu"] == pytest.approx(EXP_SCEN_COST_PER_TEU)

    def test_normal_baseline_run_still_publishes(self, db, port):
        """Regression: the ordinary (publish=True) path retains change-detection behavior."""
        b = _executed_baseline(db, port)
        # Baseline publishing wrote model_output change events and shared dataset values.
        assert db.query(ChangeEvent).filter_by(source_type="model_output").count() > 0
        val = DatasetValueService(db).get_value(port["datasets"]["decision_summary_output"])
        assert val["annual_fuel_cost_usd"] == pytest.approx(EXP_BASE_FUEL_COST)


# ===========================================================================
# Comparison
# ===========================================================================

class TestComparison:
    def _compare(self, db, port):
        b = _executed_baseline(db, port)
        s = _scenario_with_bunker(db, port, b)
        go = _svc(db).execute_scenario(s.id)
        assert go.success, go.error
        result = ScenarioComparisonService(db).compare(b.baseline_run_id, s.scenario_run_id)
        by_key = {(m.dataset_id, m.field): m for m in result.metrics}
        return result, by_key

    def test_absolute_and_relative_delta_on_changed_metric(self, db, port):
        _, by_key = self._compare(db, port)
        m = by_key[(port["datasets"]["port_fuel_cost_output"], "annual_fuel_cost_usd")]
        assert m.kind == "numeric" and m.changed is True
        assert m.absolute_delta == pytest.approx(EXP_SCEN_FUEL_COST - EXP_BASE_FUEL_COST)  # +3,000,000
        assert m.relative_delta == pytest.approx(0.25)  # (15M-12M)/12M
        assert m.direction == "increase"

    def test_cost_per_teu_delta(self, db, port):
        _, by_key = self._compare(db, port)
        m = by_key[(port["datasets"]["port_fuel_cost_output"], "fuel_cost_per_teu")]
        assert m.absolute_delta == pytest.approx(EXP_SCEN_COST_PER_TEU - EXP_BASE_COST_PER_TEU)  # +0.15

    def test_unaffected_metrics_have_zero_delta_and_are_included(self, db, port):
        _, by_key = self._compare(db, port)
        teu = by_key[(port["datasets"]["throughput_output"], "annual_teu")]
        assert teu.changed is False and teu.absolute_delta == pytest.approx(0.0)
        emis = by_key[(port["datasets"]["port_emissions_output"], "annual_emissions_tco2")]
        assert emis.changed is False and emis.absolute_delta == pytest.approx(0.0)
        util = by_key[(port["datasets"]["berth_utilization_output"], "utilization_pct")]
        assert util.changed is False and util.absolute_delta == pytest.approx(0.0)

    def test_units_preserved_from_contract(self, db, port):
        _, by_key = self._compare(db, port)
        assert by_key[(port["datasets"]["port_fuel_cost_output"], "annual_fuel_cost_usd")].unit == "USD/year"
        assert by_key[(port["datasets"]["throughput_output"], "annual_teu")].unit == "TEU/year"

    def test_boolean_metric_unchanged(self, db, port):
        _, by_key = self._compare(db, port)
        cong = by_key[(port["datasets"]["berth_utilization_output"], "congestion")]
        assert cong.kind == "boolean" and cong.changed is False
        assert cong.baseline is False and cong.scenario is False

    def test_boolean_metric_changed(self, db, port):
        """A scenario that trips congestion shows a neutral boolean change."""
        b = _executed_baseline(db, port)
        svc = _svc(db)
        s = svc.create_scenario(baseline_id=b.id, name="Congested")
        # Raise vessel calls far above the congestion threshold (berth utilization branch).
        svc.set_override(s.id, port["assumptions_dataset_id"], "annual_vessel_calls", 300_000.0)
        go = svc.execute_scenario(s.id)
        assert go.success, go.error
        result = ScenarioComparisonService(db).compare(b.baseline_run_id, s.scenario_run_id)
        cong = next(m for m in result.metrics
                    if (m.dataset_id, m.field) == (port["datasets"]["berth_utilization_output"], "congestion"))
        assert cong.kind == "boolean" and cong.changed is True
        assert cong.baseline is False and cong.scenario is True

    def test_zero_baseline_relative_delta_is_safe(self, db, port):
        """When the baseline value is zero, relative delta is None (no division by zero)."""
        svc = _svc(db)
        # Baseline with zero vessel calls → annual_fuel_cost_usd == 0, per-TEU == None.
        DatasetValueService(db).write_external(
            port["assumptions_dataset_id"],
            {**port["default_assumptions"], "annual_vessel_calls": 0.0},
            triggered_by="d19-test",
        )
        b = svc.create_baseline(name="ZeroBase", target_version_id=port["terminal_version_id"])
        assert svc.execute_baseline(b.id).success
        s = svc.create_scenario(baseline_id=b.id, name="NonZero")
        svc.set_override(s.id, port["assumptions_dataset_id"], "annual_vessel_calls", 10_000.0)
        go = svc.execute_scenario(s.id)
        assert go.success, go.error
        result = ScenarioComparisonService(db).compare(b.baseline_run_id, s.scenario_run_id)
        by_key = {(m.dataset_id, m.field): m for m in result.metrics}
        m = by_key[(port["datasets"]["port_fuel_cost_output"], "annual_fuel_cost_usd")]
        assert m.baseline == pytest.approx(0.0) and m.baseline_zero is True
        assert m.relative_delta is None and m.absolute_delta is not None and m.changed is True

    def test_comparison_is_deterministic_by_exact_run_ids(self, db, port):
        result, _ = self._compare(db, port)
        # exact run identities are carried through, comparison is stable across repeats
        again = ScenarioComparisonService(db).compare(result.baseline_run_id, result.scenario_run_id)
        assert [(m.dataset_id, m.field, m.absolute_delta) for m in result.metrics] == \
               [(m.dataset_id, m.field, m.absolute_delta) for m in again.metrics]

    def test_compare_unknown_run_raises(self, db, port):
        b = _executed_baseline(db, port)
        with pytest.raises(ComparisonRunNotFoundError):
            ScenarioComparisonService(db).compare(b.baseline_run_id, "no-such-run")


# ===========================================================================
# D17 bunker-price acceptance scenario (end to end)
# ===========================================================================

class TestD19Acceptance:
    def test_bunker_price_scenario_matches_expected_deltas(self, db, port):
        svc = _svc(db)
        # Baseline: the D17 golden (seeded DEFAULT_ASSUMPTIONS).
        baseline = svc.create_baseline(name="Acceptance", target_version_id=port["terminal_version_id"])
        assert svc.execute_baseline(baseline.id).success
        base_summary = _result_record(db, baseline.baseline_run_id,
                                      port["datasets"]["decision_summary_output"])
        assert base_summary["annual_teu"] == pytest.approx(EXP_TEU)
        assert base_summary["annual_fuel_cost_usd"] == pytest.approx(EXP_BASE_FUEL_COST)
        assert base_summary["fuel_cost_per_teu"] == pytest.approx(EXP_BASE_COST_PER_TEU)
        assert base_summary["annual_emissions_tco2"] == pytest.approx(EXP_EMISSIONS)
        assert base_summary["berth_utilization_pct"] == pytest.approx(EXP_UTIL_PCT)

        # Scenario: inherit baseline, override bunker (×1.25).
        scenario = svc.create_scenario(baseline_id=baseline.id, name="High Bunker")
        svc.set_override(scenario.id, port["assumptions_dataset_id"], BUNKER_PRICE, SCENARIO_BUNKER)
        go = svc.execute_scenario(scenario.id)
        assert go.success, go.error
        scen_summary = _result_record(db, go.run_id, port["datasets"]["decision_summary_output"])
        assert scen_summary["annual_teu"] == pytest.approx(EXP_TEU)                    # unchanged
        assert scen_summary["annual_fuel_cost_usd"] == pytest.approx(EXP_SCEN_FUEL_COST)  # 15,000,000
        assert scen_summary["fuel_cost_per_teu"] == pytest.approx(EXP_SCEN_COST_PER_TEU)  # 0.75
        assert scen_summary["annual_emissions_tco2"] == pytest.approx(EXP_EMISSIONS)   # unchanged
        assert scen_summary["berth_utilization_pct"] == pytest.approx(EXP_UTIL_PCT)    # unchanged

        # Comparison deltas.
        result = ScenarioComparisonService(db).compare(baseline.baseline_run_id, go.run_id)
        by_key = {(m.dataset_id, m.field): m for m in result.metrics}
        ds = port["datasets"]
        assert by_key[(ds["decision_summary_output"], "annual_fuel_cost_usd")].absolute_delta == \
            pytest.approx(3_000_000.0)
        assert by_key[(ds["decision_summary_output"], "fuel_cost_per_teu")].absolute_delta == \
            pytest.approx(0.15)
        # unaffected metrics: zero deltas
        for field_name in ("annual_teu", "annual_emissions_tco2", "emissions_per_teu",
                           "berth_utilization_pct"):
            assert by_key[(ds["decision_summary_output"], field_name)].absolute_delta == \
                pytest.approx(0.0)


# ===========================================================================
# PostgreSQL (skipped unless TEST_DATABASE_URL is set; rolled back — DB untouched)
# ===========================================================================

_PG_URL = _normalize_db_url(os.environ.get("TEST_DATABASE_URL", ""))


@pytest.mark.skipif(not _PG_URL.startswith("postgresql"), reason="TEST_DATABASE_URL not set")
class TestD19OnPostgreSQL:
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

    def test_acceptance_and_read_only_on_pg(self, pg_db):
        port = seed_port_domain(pg_db)
        svc = ScenarioService(pg_db, AdapterRegistry(pg_db))
        baseline = svc.create_baseline(name="Acceptance", target_version_id=port["terminal_version_id"])
        assert svc.execute_baseline(baseline.id).success

        before_values = {d.id: (d.current_value, d.current_hash) for d in pg_db.query(Dataset).all()}
        before_events = pg_db.query(ChangeEvent).count()

        scenario = svc.create_scenario(baseline_id=baseline.id, name="High Bunker")
        svc.set_override(scenario.id, port["assumptions_dataset_id"], BUNKER_PRICE, SCENARIO_BUNKER)
        go = svc.execute_scenario(scenario.id)
        assert go.success, go.error

        # read-only invariant holds on PostgreSQL too
        for d in pg_db.query(Dataset).all():
            assert (d.current_value, d.current_hash) == before_values[d.id]
        assert pg_db.query(ChangeEvent).count() == before_events

        result = ScenarioComparisonService(pg_db).compare(baseline.baseline_run_id, go.run_id)
        by_key = {(m.dataset_id, m.field): m for m in result.metrics}
        assert by_key[(port["datasets"]["port_fuel_cost_output"], "annual_fuel_cost_usd")].absolute_delta == \
            pytest.approx(3_000_000.0)

    def test_scenario_tables_created_on_pg(self, pg_db):
        # Additive tables exist and are usable under PostgreSQL.
        b = ScenarioService(pg_db, AdapterRegistry(pg_db)).create_baseline(name="B")
        assert pg_db.query(Baseline).filter_by(id=b.id).one() is not None
        assert pg_db.query(Scenario).count() == 0
        assert pg_db.query(ScenarioOverride).count() == 0
