"""Adapter abstraction — D6.

Defines the minimum contract a model adapter must satisfy so that
the future execution service can resolve and invoke implementations
without knowing their internals.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class ModelAdapter(ABC):
    """
    Base class for all model adapters.

    Each concrete adapter:
    - declares a stable adapter_id (unique across the registry)
    - declares the version_id it supports
    - exposes an invoke() boundary for the future execution service
    """

    @property
    @abstractmethod
    def adapter_id(self) -> str:
        """Stable, unique identifier for this adapter."""

    @property
    @abstractmethod
    def version_id(self) -> str:
        """The model version ID this adapter supports."""

    @abstractmethod
    def invoke(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """
        Execute the model with the given inputs and return outputs.

        Not called during D6. Defined here so the execution service
        can call it in the next phase without changing the interface.
        """
