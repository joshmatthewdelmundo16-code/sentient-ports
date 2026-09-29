"""Port domain adapter — D17.

Wraps a pure toy-port formula (backend.app.domain.port_models) behind the D6 ModelAdapter
interface. Execution behaviour is persisted on the model version as
`adapter_type="port_domain"` with `adapter_config={"model": "<key>", ...params}`.

Config params that are not dataset inputs (e.g. congestion_threshold_pct) are merged into
the call alongside the assembled dataset inputs.
"""

from __future__ import annotations

from typing import Any

from backend.app.adapters.base import ModelAdapter
from backend.app.domain import port_models


class PortDomainAdapter(ModelAdapter):
    def __init__(self, adapter_id: str, version_id: str, model: str,
                 params: dict[str, Any] | None = None) -> None:
        if model not in port_models.MODEL_FUNCS:
            raise ValueError(
                f"Unknown port model {model!r}; known: {sorted(port_models.MODEL_FUNCS)}"
            )
        self._adapter_id = adapter_id
        self._version_id = version_id
        self._model = model
        self._params = dict(params or {})

    @property
    def adapter_id(self) -> str:
        return self._adapter_id

    @property
    def version_id(self) -> str:
        return self._version_id

    @property
    def model(self) -> str:
        return self._model

    def invoke(self, inputs: dict[str, Any]) -> dict[str, Any]:
        # Config params fill in non-dataset arguments; dataset inputs win on any overlap.
        return port_models.compute(self._model, {**self._params, **inputs})
