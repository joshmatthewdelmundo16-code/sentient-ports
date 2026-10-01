"""D28 — governed export feeding Evidence (and Dagster's asset graph).

The exporter must see exactly what the signed-in user sees through the API — nothing more — and
the Evidence project must have no other data source.
"""

from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path

import pytest

from tests.d27_support import network_client

PLATFORM = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("export_platform_data", PLATFORM / "scripts" / "export_platform_data.py")
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)

PASSWORD = __import__("backend.app.ui.network_seed", fromlist=["DEMO_PASSWORD"]).DEMO_PASSWORD


@pytest.fixture(scope="module")
def net():
    with network_client() as n:
        yield n


def _rows(out: Path, name: str) -> list[dict]:
    with (out / f"{name}.csv").open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


@pytest.fixture(scope="module")
def northbay(net, tmp_path_factory):
    out = tmp_path_factory.mktemp("northbay") / "platform"
    net.client.cookies.clear()
    counts = exporter.export(net.client, "port-northbay", out, email="analyst@northbay.example", password=PASSWORD)
    return out, counts


def test_every_table_is_written_with_its_columns(northbay):
    out, counts = northbay
    for name, columns in exporter.TABLES.items():
        with (out / f"{name}.csv").open(encoding="utf-8", newline="") as fh:
            assert next(csv.reader(fh)) == columns
    assert set(counts) == set(exporter.TABLES)
    for name in ("datasets", "dataset_values", "model_links", "runs", "activity",
                 "scenario_comparison", "plan_periods", "exposed_outputs"):
        assert counts[name] > 0, name
    meta = _rows(out, "export_meta")[0]
    assert meta["organization_key"] == "port-northbay" and meta["exported_by"] == "analyst@northbay.example"
    assert not (out / "federation_map.json").exists()       # Evidence would read it as a table
    fmap = json.loads((out.parent / "federation_map.json").read_text(encoding="utf-8"))
    assert {"models", "datasets", "reads", "writes"} <= set(fmap)


def test_shared_outputs_are_exactly_the_governed_boundary(net, northbay):
    out, _ = northbay
    net.login("analyst@northbay.example")
    api = net.get("/api/exposed-outputs", "port-northbay").json()
    exported = _rows(out, "exposed_outputs")
    key = lambda r: (r["participant"], r["dataset"], r["field"])  # noqa: E731
    assert sorted(map(key, exported)) == sorted((e["participant_key"], e["dataset_name"], e["field_name"]) for e in api)


def test_only_the_users_own_organization_is_exported(net, northbay, tmp_path):
    out, _ = northbay
    net.client.cookies.clear()
    east = tmp_path / "east" / "platform"
    exporter.export(net.client, "port-eastmouth", east, email="analyst@eastmouth.example", password=PASSWORD)
    north_runs = {r["run_id"] for r in _rows(out, "runs")}
    east_runs = {r["run_id"] for r in _rows(east, "runs")}
    assert north_runs and east_runs and not (north_runs & east_runs)
    net.client.cookies.clear()
    with pytest.raises(exporter.ExportError, match="not a member"):
        exporter.export(net.client, "port-northbay", tmp_path / "x" / "platform", email="analyst@eastmouth.example",
                        password=PASSWORD)


def test_bad_credentials_fail_cleanly(net, tmp_path):
    net.client.cookies.clear()
    with pytest.raises(exporter.ExportError, match="Sign-in failed"):
        exporter.export(net.client, "port-northbay", tmp_path / "platform", email="analyst@northbay.example",
                        password="wrong")


def test_exporter_and_evidence_never_touch_the_database():
    source = (PLATFORM / "scripts" / "export_platform_data.py").read_text(encoding="utf-8")
    assert "backend.app" not in source and "sqlalchemy" not in source and "DATABASE_URL" not in source
    evidence = PLATFORM / "evidence"
    connections = list((evidence / "sources").rglob("connection.yaml"))
    assert [p.parent.name for p in connections] == ["platform"]
    assert "type: csv" in connections[0].read_text(encoding="utf-8")
    pages = {p.stem for p in (evidence / "pages").glob("*.md")}
    assert {"index", "ripple-effects", "master-plan", "scenarios", "shared-outputs"} <= pages
    referenced = set()
    for page in (evidence / "pages").glob("*.md"):
        referenced |= set(__import__("re").findall(r"platform\.(\w+)", page.read_text(encoding="utf-8")))
    assert referenced <= set(exporter.TABLES), referenced - set(exporter.TABLES)
