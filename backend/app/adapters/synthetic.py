"""Synthetic adapter — D6.

A deterministic, in-process adapter for testing and demonstration.
Requires no external systems, network, or containers.
"""

from __future__ import annotations

from typing import Any

from backend.app.adapters.base import ModelAdapter


class SyntheticAdapter(ModelAdapter):
    """
    Deterministic adapter that multiplies every numeric input value
    by a fixed scalar and returns the results.

    Used for tests and as the demo-tier adapter when no real
    model runtime is available.
    """

    def __init__(self, adapter_id: str, version_id: str, scalar: float = 1.0) -> None:
        self._adapter_id = adapter_id
        self._version_id = version_id
        self._scalar = scalar

    @property
    def adapter_id(self) -> str:
        return self._adapter_id

    @property
    def version_id(self) -> str:
        return self._version_id

    def invoke(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """
        Multiply every numeric input value by self._scalar.
        Non-numeric values are passed through unchanged.
        """
        return {
            k: (v * self._scalar if isinstance(v, (int, float)) else v)
            for k, v in inputs.items()
        }
