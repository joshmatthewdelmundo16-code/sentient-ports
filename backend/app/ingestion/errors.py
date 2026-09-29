"""Ingestion error taxonomy — D18.

Every rejection is one of these typed errors so the service can persist a useful
`ingestion_run.status="rejected"` with a specific reason, and the API can map it to a
4xx response. Contract violations from the D16 layer are re-raised unchanged.
"""

from __future__ import annotations


class IngestionError(Exception):
    """Base class for all ingestion rejections."""


class UnsupportedFileTypeError(IngestionError):
    """The upload is not a .xlsx file."""


class FileTooLargeError(IngestionError):
    """The upload exceeds the configured size bound."""


class WorkbookUnreadableError(IngestionError):
    """openpyxl could not open the bytes as a workbook (corrupt / not a zip / encrypted)."""


class WorksheetNotFoundError(IngestionError):
    """A mapped worksheet does not exist in the workbook."""


class CellNotFoundError(IngestionError):
    """A mapped cell is outside the worksheet's used range / cannot be addressed."""


class UnknownMappingError(IngestionError):
    """No source mapping is registered under the requested key."""


class FormulaWithoutCachedValueError(IngestionError):
    """A mapped cell holds a formula with no cached value (data_only cannot read it)."""
