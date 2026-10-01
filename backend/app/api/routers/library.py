"""Model library API — D27.

Cards for every model in the current organization (provider, provider kind, domain, versions,
input/output contracts with units, execution method, governance and calibration status,
provenance) plus a compatibility check computed from the real bindings and contracts, and the
catalogue of installable model packs.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.library.packs import PACKS, install_pack, installed
from backend.app.persistence.database import (
    DataContract,
    Dataset,
    Model,
    ModelIOBinding,
    ModelVersion,
    Organization,
    get_db,
)
from backend.app.product.labels import dataset_label, display_unit, field_label
from backend.app.security import audit
from backend.app.security.auth import RequestContext, request_context
from backend.app.services.contract_validation import parse_schema

router = APIRouter(prefix="/api/model-library", tags=["Model library"])

EXECUTION = {
    "synthetic": "Synthetic scalar adapter (engine test harness)",
    "port_domain": "In-process Python function (toy port domain)",
    "model_pack": "In-process Python function from a reviewed model pack",
    None: "No execution method configured",
}


def _contracts(db: Session) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for c in db.execute(select(DataContract).order_by(DataContract.created_at)).scalars():
        try:
            out[c.dataset_id] = {f.name: f for f in parse_schema(json.loads(c.schema_json)).fields}
        except (ValueError, TypeError):
            out[c.dataset_id] = {}
    return out


@router.get("", summary="Model cards with contracts, units, provenance and compatibility")
def library(db: Session = Depends(get_db, scope="function")) -> list[dict[str, Any]]:
    contracts = _contracts(db)
    datasets = {d.id: d for d in db.execute(select(Dataset)).scalars()}
    bindings: dict[str, list[ModelIOBinding]] = {}
    for b in db.execute(select(ModelIOBinding)).scalars():
        bindings.setdefault(b.model_version_id, []).append(b)
    cards = []
    for model in db.execute(select(Model).order_by(Model.name)).scalars():
        card = json.loads(model.card_json) if model.card_json else {}
        versions = []
        for v in db.execute(select(ModelVersion).where(ModelVersion.model_id == model.id)
                            .order_by(ModelVersion.created_at)).scalars():
            ins, outs, problems = [], [], []
            declared_units = {}
            try:
                for spec in json.loads(v.inputs_spec or "[]"):
                    if isinstance(spec, dict) and spec.get("unit"):
                        declared_units[spec["name"]] = spec["unit"]
            except ValueError:
                pass
            for b in bindings.get(v.id, []):
                ds = datasets.get(b.dataset_id)
                fields = contracts.get(b.dataset_id, {})
                fmap = json.loads(b.field_map) if b.field_map else None
                names = list(fmap) if isinstance(fmap, dict) else list(fields)
                entry = {
                    "dataset_id": b.dataset_id, "dataset": ds.name if ds else None,
                    "dataset_label": dataset_label(ds.name, ds.display_name) if ds else "Missing dataset",
                    "fields": [{"name": n, "label": field_label(n, getattr(fields.get(n), "label", None)),
                                "unit": display_unit(getattr(fields.get(n), "unit", None)),
                                "type": getattr(fields.get(n), "type", None)} for n in names],
                }
                if ds is None:
                    problems.append("A bound dataset no longer exists.")
                for n in names:
                    if n not in fields:
                        problems.append(f"{n} is not declared by the {entry['dataset_label']} contract.")
                    elif b.direction == "input" and n in declared_units and fields[n].unit \
                            and declared_units[n] != fields[n].unit:
                        problems.append(f"{n}: model expects {declared_units[n]}, contract says {fields[n].unit}.")
                (ins if b.direction == "input" else outs).append(entry)
            versions.append({
                "version_id": v.id, "semver": v.semver, "is_active": v.is_active,
                "execution_method": EXECUTION.get(v.adapter_type, v.adapter_type),
                "adapter_type": v.adapter_type, "inputs": ins, "outputs": outs,
                "compatible": not problems and bool(ins or outs), "compatibility_problems": problems,
            })
        cards.append({
            "model_id": model.id, "name": model.name, "status": model.status,
            "owner": model.owner, "provider": card.get("provider", model.owner),
            "provider_kind": card.get("provider_kind", "internal"), "domain": card.get("domain"),
            "purpose": card.get("purpose") or model.description, "formula": card.get("formula"),
            "calibration": card.get("calibration", "unknown"), "provenance": card.get("provenance"),
            "governance_status": card.get("governance_status", "unreviewed"),
            "pack": card.get("pack_name"), "disclaimer": card.get("disclaimer"),
            "versions": versions,
        })
    return cards


@router.get("/packs", summary="Installable model packs, and whether each is installed here")
def packs(db: Session = Depends(get_db, scope="function")) -> list[dict[str, Any]]:
    return [{
        "key": p.key, "name": p.name, "description": p.description, "provider": p.provider,
        "provider_kind": p.provider_kind, "calibration": p.calibration, "provenance": p.provenance,
        "models": [{"key": m.key, "name": m.name, "domain": m.domain, "purpose": m.purpose} for m in p.models],
        "domains": sorted({m.domain for m in p.models}),
        "installed": installed(db, p.key),
    } for p in PACKS.values()]


@router.post("/packs/{pack_key}/install", summary="Install a model pack into this organization (admins)")
def install(pack_key: str, request: Request, ctx: RequestContext = Depends(request_context),
            db: Session = Depends(get_db, scope="function")) -> dict[str, Any]:
    if pack_key not in PACKS:
        raise HTTPException(status_code=404, detail="Unknown model pack.")
    if ctx.org_id is None:
        raise HTTPException(status_code=409, detail="Choose an organization scope first.")
    org = db.get(Organization, ctx.org_id)
    out = install_pack(db, pack_key, owner=org.org_key if org else "platform")
    audit.record(db, action="pack.installed", request=request, target_type="pack", target_id=pack_key,
                 summary=f"Installed {PACKS[pack_key].name}")
    return out
