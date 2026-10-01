"""Dagster code location — D28 (optional).

Two things, both strictly on the platform's terms:

1. ``platform_graphrun_job`` — ONE stable job that carries out a platform-owned GraphRun. Its
   single op computes nothing and persists nothing: it calls back into the platform, which runs
   the same in-process execution and owns every result and lineage edge. The platform launches
   this job when a graph execution is requested with ``"executor": "dagster"``.

2. The federation as a Dagster asset graph — every platform dataset becomes an (external) asset,
   with the model links between datasets as asset dependencies, loaded from a federation map the
   platform exports (``scripts/export_platform_data.py``). When the job carries out a run, the
   datasets that run computed are recorded as asset materializations, so Dagster's UI shows a
   change rippling through the system of systems as data events, not only as a task that ran.

This package is intentionally NOT imported by the platform or its test suite, and Dagster is
not a platform dependency. Install ``dagster_platform/requirements.txt`` only where Dagster runs.

Configuration (environment on the Dagster side — never in run config):
  PLATFORM_CALLBACK_BASE_URL   e.g. http://platform:8000
  PLATFORM_CALLBACK_TOKEN      the platform's PLATFORM_SERVICE_TOKEN (required when the platform
                               runs with AUTH_MODE=required)
  PLATFORM_FEDERATION_MAP      optional path to the exported federation_map.json
"""

# No `from __future__ import annotations`: Dagster resolves op config and context types from
# real (not stringified) annotations.
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import dagster as dg

OP_NAME = "carry_out_graph_run"
JOB_NAME = "platform_graphrun_job"
ASSET_PREFIX = "federation"

# The platform commits a submitted GraphRun at the end of the request that launched this job,
# so a very fast callback can arrive before the run is visible (404) or before its Dagster run
# id is stored (409). Both are retried briefly; any other refusal fails the op immediately.
_RETRY_STATUSES = frozenset({404, 409})


class GraphRunConfig(dg.Config):
    graph_run_id: str


def _callback(graph_run_id: str, dagster_run_id: str, log: Any) -> dict[str, Any]:
    base = os.environ.get("PLATFORM_CALLBACK_BASE_URL", "").rstrip("/")
    if not base:
        raise dg.Failure("PLATFORM_CALLBACK_BASE_URL is not set on the Dagster side.")
    url = f"{base}/api/executions/{graph_run_id}/dagster-callback"
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("PLATFORM_CALLBACK_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps({"dagster_run_id": dagster_run_id}).encode()
    attempts = max(1, int(os.environ.get("PLATFORM_CALLBACK_ATTEMPTS", "6")))

    for attempt in range(1, attempts + 1):
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:  # noqa: S310 (operator-set URL)
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:500]
            if exc.code in _RETRY_STATUSES and attempt < attempts:
                log.warning(f"Platform callback HTTP {exc.code} (attempt {attempt}/{attempts}); retrying.")
                time.sleep(min(2 ** attempt, 10))
                continue
            raise dg.Failure(f"Platform callback refused (HTTP {exc.code}): {detail}") from exc
        except urllib.error.URLError as exc:
            raise dg.Failure(f"Platform unreachable at {base}: {exc.reason}") from exc
    raise dg.Failure("Platform callback did not succeed.")  # unreachable; keeps type-checkers calm


@dg.op(name=OP_NAME, description="Ask the platform to carry out one GraphRun; record the datasets it published.")
def carry_out_graph_run(context: dg.OpExecutionContext, config: GraphRunConfig) -> dict[str, Any]:
    payload = _callback(config.graph_run_id, context.run_id, context.log)
    for name in payload.get("written_datasets") or []:
        context.log_event(dg.AssetMaterialization(
            asset_key=dg.AssetKey([ASSET_PREFIX, name]),
            description="Computed by a platform GraphRun",
            metadata={"graph_run_id": config.graph_run_id},
        ))
    if not payload.get("success"):
        raise dg.Failure(f"Platform GraphRun {config.graph_run_id} did not succeed "
                         f"(status={payload.get('status')!r}).")
    context.log.info(f"GraphRun {config.graph_run_id} succeeded; "
                     f"{len(payload.get('recorded_result_ids') or [])} results recorded by the platform.")
    return payload


@dg.job(name=JOB_NAME, description="Carry out a platform-owned GraphRun (Dagster triggers; the platform computes).",
        tags={"platform/executor": "dagster"})
def platform_graphrun_job() -> None:
    carry_out_graph_run()


def federation_asset_specs(path: str | os.PathLike[str] | None) -> list[dg.AssetSpec]:
    """Datasets as assets; a dataset depends on the datasets read by the model that writes it."""
    if not path or not Path(path).is_file():
        return []
    fmap = json.loads(Path(path).read_text(encoding="utf-8"))
    datasets = {d["id"]: d for d in fmap.get("datasets", [])}
    models = {m["version_id"]: m for m in fmap.get("models", [])}
    reads: dict[str, set[str]] = {}
    for r in fmap.get("reads", []):
        reads.setdefault(r["version_id"], set()).add(r["dataset_id"])
    producers: dict[str, list[str]] = {}
    for w in fmap.get("writes", []):
        producers.setdefault(w["dataset_id"], []).append(w["version_id"])

    specs = []
    for ds_id, d in sorted(datasets.items(), key=lambda kv: kv[1]["name"]):
        writers = producers.get(ds_id, [])
        upstream = sorted({datasets[r]["name"] for v in writers for r in reads.get(v, ()) if r in datasets})
        specs.append(dg.AssetSpec(
            key=dg.AssetKey([ASSET_PREFIX, d["name"]]),
            deps=[dg.AssetKey([ASSET_PREFIX, u]) for u in upstream],
            description=d.get("label") or d["name"],
            group_name=ASSET_PREFIX,
            kinds={"platform"},
            metadata={
                "role": d.get("role") or "",
                "produced_by": ", ".join(models[v]["name"] for v in writers if v in models) or "external source",
            },
        ))
    return specs


defs = dg.Definitions(
    jobs=[platform_graphrun_job],
    assets=federation_asset_specs(os.environ.get("PLATFORM_FEDERATION_MAP")),
)
