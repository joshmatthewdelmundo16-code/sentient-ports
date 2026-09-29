"""Persistence-layer exceptions — D2."""

from __future__ import annotations


class NotFoundError(Exception):
    """Entity not found in the database."""


class DuplicateError(Exception):
    """Unique constraint violation."""


class PersistenceError(Exception):
    """Unexpected database failure."""
