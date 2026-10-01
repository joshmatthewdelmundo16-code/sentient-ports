"""Governed data export for Evidence (BI-as-code) and Dagster's asset graph — D28.

Evidence never connects to the platform database. This script signs in to the platform's HTTP
API like any user, reads only what that user may read in one organization (tenant scoping,
roles and the approved-output boundary all apply exactly as in the UI), and writes flat CSV
tables that the Evidence project in ``platform/evidence`` reads as its only data source.

It also writes ``federation_map.json`` next to (not inside) the CSV folder — by default
``evidence/sources/federation_map.json`` — which the optional Dagster code location
(``dagster_platform``, via PLATFORM_FEDERATION_MAP) turns into an asset graph of the federation.

Usage (from platform/):
    python scripts/export_platform_data.py --base-url http://127.0.0.1:8000 \
        --org port-northbay --email analyst@northbay.example
    # password from --password or PLATFORM_EXPORT_PASSWORD; omit both in AUTH_MODE=local

Then: cd evidence && npm run sources && npm run build   (or npm run dev)
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

DEFAULT_OUT = Path(__file__).resolve().parents[1] / "evidence" / "sources" / "platform"

# Every table this script writes, with its columns. Evidence pages query these names.
TABLES: dict[str, list[str]] = {
    "export_meta": ["exported_at", "organization_key", "organization_name", "exported_by", "base_url"],
    "datasets": ["dataset", "label", "role", "produced_by", "consumed_by", "updated_at", "current_source"],
    "dataset_values": ["dataset", "dataset_label", "role", "field", "field_label", "unit", "value", "value_text"],
    "model_links": ["model", "direction", "dataset", "fields"],
    "runs": ["run_id", "run_kind", "executor", "status", "trigger_type", "triggered_by", "started_at",
             "finished_at", "duration_s"],
    "activity": ["occurred_at", "kind", "title", "subject", "context", "status", "field", "field_label",
                 "unit", "value_from", "value_to", "relative_delta", "via_kind", "via_source"],
    "scenario_comparison": ["scenario", "scenario_description", "dataset", "field", "unit", "baseline",
                            "scenario_value", "absolute_delta", "relative_delta", "direction"],
    "plan_periods": ["plan", "parent_plan", "year", "status", "metric", "value"],
    "exposed_outputs": ["participant", "dataset", "field", "purpose", "value", "value_source", "approved_at",
                        "expires_at"],
}


class ExportError(Exception):
    pass


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _text(v: Any) -> str:
    if v is None:
        return ""
    return v if isinstance(v, str) else json.dumps(v, default=str)


class Api:
    """A thin client over any httpx-compatible client (httpx.Client or FastAPI's TestClient)."""

    def __init__(self, client: Any, org_id: str | None = None) -> None:
        self.client, self.org_id = client, org_id

    def get(self, path: str) -> Any:
        headers = {"X-Scope-Org": self.org_id} if self.org_id else {}
        r = self.client.get(path, headers=headers)
        if r.status_code != 200:
            raise ExportError(f"GET {path} → HTTP {r.status_code}: {r.text[:200]}")
        return r.json()


def sign_in(client: Any, org_key: str, email: str | None, password: str | None) -> tuple[Api, dict[str, str]]:
    """Sign in (unless the platform runs in local mode) and resolve the organization scope."""
    if email:
        r = client.post("/api/auth/login", json={"email": email, "password": password or ""})
        if r.status_code != 200:
            raise ExportError(f"Sign-in failed (HTTP {r.status_code}).")
    session = client.get("/api/session").json()
    for m in session.get("memberships") or []:
        org = m.get("organization") or {}
        if org.get("key") == org_key or org.get("id") == org_key:
            who = (session.get("user") or {}).get("email") or "local developer"
            return Api(client, org["id"]), {"key": org["key"], "name": org.get("name", ""), "user": who}
    if session.get("auth_mode") == "local":
        return Api(client, None), {"key": org_key, "name": org_key, "user": "local developer"}
    raise ExportError(f"The signed-in user is not a member of {org_key!r}.")


def collect(api: Api) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    rows: dict[str, list[dict[str, Any]]] = {name: [] for name in TABLES}
    catalog = api.get("/api/catalog")
    by_id = {d["id"]: d for d in catalog}

    for d in catalog:
        source = d.get("current_source")
        rows["datasets"].append({
            "dataset": d["name"], "label": d.get("label") or d["name"], "role": d.get("role"),
            "produced_by": "; ".join(p["model_name"] for p in d.get("produced_by") or []),
            "consumed_by": "; ".join(p["model_name"] for p in d.get("consumed_by") or []),
            "updated_at": d.get("updated_at"),
            "current_source": _text(source.get("source_type") if isinstance(source, dict) else source),
        })
        fields = {f["name"]: f for f in d.get("fields") or []}
        value = d.get("value") if isinstance(d.get("value"), dict) else {}
        for name, v in value.items():
            f = fields.get(name, {})
            rows["dataset_values"].append({
                "dataset": d["name"], "dataset_label": d.get("label") or d["name"], "role": d.get("role"),
                "field": name, "field_label": f.get("label") or name, "unit": f.get("unit") or "",
                "value": _num(v), "value_text": "" if _num(v) is not None else _text(v),
            })

    fmap = api.get("/api/federation/map")
    models = {m["version_id"]: m["name"] for m in fmap.get("models", [])}
    fmap_ds = {d["id"]: d["name"] for d in fmap.get("datasets", [])}
    for direction in ("reads", "writes"):
        for link in fmap.get(direction, []):
            rows["model_links"].append({
                "model": models.get(link["version_id"], link["version_id"]), "direction": direction,
                "dataset": fmap_ds.get(link["dataset_id"], link["dataset_id"]),
                "fields": ", ".join(link.get("fields") or []) or "all",
            })

    for r in api.get("/api/executions?limit=200"):
        start, end = r.get("started_at"), r.get("finished_at")
        try:
            dur = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()
        except (TypeError, ValueError):   # missing, or naive/aware mixed
            dur = None
        rows["runs"].append({"run_id": r["id"], "run_kind": r.get("run_kind"), "executor": r.get("executor"),
                             "status": r.get("status"), "trigger_type": r.get("trigger_type"),
                             "triggered_by": r.get("triggered_by"), "started_at": start, "finished_at": end,
                             "duration_s": dur})

    for a in api.get("/api/activity"):
        h = a.get("headline") or {}
        via = a.get("via") or {}
        rows["activity"].append({
            "occurred_at": a.get("occurred_at"), "kind": a.get("kind"), "title": a.get("title"),
            "subject": a.get("subject"), "context": a.get("context"), "status": a.get("status"),
            "field": h.get("field"), "field_label": h.get("label"), "unit": h.get("unit"),
            "value_from": _num(h.get("from")), "value_to": _num(h.get("to")),
            "relative_delta": _num(h.get("relative_delta")),
            "via_kind": via.get("kind"), "via_source": via.get("source_ref"),
        })

    for s in api.get("/api/scenarios"):
        if s.get("status") != "executed":
            continue
        comp = api.get(f"/api/scenarios/{s['id']}/comparison")
        for m in comp.get("metrics") or []:
            if m.get("kind") != "numeric":
                continue
            rows["scenario_comparison"].append({
                "scenario": s["name"], "scenario_description": s.get("description") or "",
                "dataset": by_id.get(m["dataset_id"], {}).get("name", m["dataset_id"]), "field": m["field"],
                "unit": m.get("unit") or "", "baseline": _num(m.get("baseline")),
                "scenario_value": _num(m.get("scenario")), "absolute_delta": _num(m.get("absolute_delta")),
                "relative_delta": _num(m.get("relative_delta")), "direction": m.get("direction"),
            })

    plans = api.get("/api/plans")
    names = {p["id"]: p["name"] for p in plans}
    for p in plans:
        detail = api.get(f"/api/plans/{p['id']}")
        for period in detail.get("periods") or []:
            for metric, v in (period.get("outputs") or {}).items():
                if _num(v) is None:
                    continue
                rows["plan_periods"].append({
                    "plan": p["name"], "parent_plan": names.get(p.get("parent_plan_id") or "", ""),
                    "year": period["year"], "status": period.get("status"), "metric": metric, "value": _num(v),
                })

    for e in api.get("/api/exposed-outputs"):
        rows["exposed_outputs"].append({
            "participant": e.get("participant_key"), "dataset": e.get("dataset_name"), "field": e.get("field_name"),
            "purpose": e.get("purpose") or "", "value": _num(e.get("value")), "value_source": e.get("value_source"),
            "approved_at": e.get("approved_at"), "expires_at": e.get("expires_at") or "",
        })
    return rows, fmap


def write(rows: dict[str, list[dict[str, Any]]], fmap: dict[str, Any], out: Path) -> dict[str, int]:
    out.mkdir(parents=True, exist_ok=True)
    counts = {}
    for name, columns in TABLES.items():
        with (out / f"{name}.csv").open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
            w.writeheader()
            w.writerows({k: ("" if v is None else v) for k, v in r.items()} for r in rows[name])
        counts[name] = len(rows[name])
    # Outside the CSV folder: Evidence treats every file in a source folder as a table.
    (out.parent / "federation_map.json").write_text(json.dumps(fmap, indent=2, default=str), encoding="utf-8")
    return counts


def export(client: Any, org_key: str, out: Path, *, email: str | None = None, password: str | None = None,
           base_url: str = "") -> dict[str, int]:
    api, org = sign_in(client, org_key, email, password)
    rows, fmap = collect(api)
    rows["export_meta"] = [{"exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                            "organization_key": org["key"], "organization_name": org["name"],
                            "exported_by": org["user"], "base_url": base_url}]
    return write(rows, fmap, out)


def main(argv: Iterable[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base-url", default=os.environ.get("PLATFORM_BASE_URL", "http://127.0.0.1:8000"))
    ap.add_argument("--org", required=True, help="organization key, e.g. port-northbay")
    ap.add_argument("--email", default=os.environ.get("PLATFORM_EXPORT_EMAIL"))
    ap.add_argument("--password", default=os.environ.get("PLATFORM_EXPORT_PASSWORD"))
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args(list(argv) if argv is not None else None)

    import httpx

    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=60, follow_redirects=False) as client:
        try:
            counts = export(client, args.org, args.out, email=args.email, password=args.password,
                            base_url=args.base_url)
        except ExportError as exc:
            print(f"Export failed: {exc}", file=sys.stderr)
            return 1
    for name, n in counts.items():
        print(f"  {name:22s} {n:6d} rows")
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
