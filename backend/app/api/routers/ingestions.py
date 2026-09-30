"""Excel ingestion API — D18.

Smallest surface for: upload an .xlsx, apply a known mapping, return the ingestion
status/result, and retrieve provenance. Rejections are a normal, durable outcome recorded
with status="rejected" and returned as 201 (not an HTTP error), so the record persists.
Only a malformed request (no file) is a 4xx from FastAPI validation.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from backend.app.api.deps import get_ingestion_service
from backend.app.api.schemas import (
    IngestionOut,
    IngestionRunOut,
    MappingCellOut,
    MappingFieldOut,
    MappingOut,
    MappingPreviewOut,
    WorkbookPreviewOut,
)
from backend.app.ingestion.errors import UnknownMappingError
from backend.app.ingestion.mappings import get_mapping, list_mappings
from backend.app.ingestion.service import MAX_WORKBOOK_BYTES, IngestionService
from backend.app.ingestion.workbook_preview import (
    build_template,
    mapping_preview,
    preview_workbook,
)
from backend.app.persistence.database import get_db
from backend.app.persistence.exceptions import NotFoundError

XLSX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

from backend.app.security import audit  # noqa: E402

router = APIRouter(prefix="/api/ingestions", tags=["Ingestion"])


def _read_bounded(file: UploadFile, limit: int) -> bytes:
    """Read at most limit+1 bytes so an oversized upload is detected without loading it all.

    D26: synchronous on purpose. Both upload endpoints are plain `def` so FastAPI runs them
    in its thread pool — parsing a workbook and writing to the database must never block the
    event loop that every other request shares.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = file.file.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        chunks.append(chunk)
        if total > limit:
            break
    return b"".join(chunks)[: limit + 1]


@router.get("/mappings", response_model=list[MappingOut], summary="List known source mappings")
def list_source_mappings() -> list[MappingOut]:
    return [
        MappingOut(
            key=m.key, description=m.description,
            cells=[MappingCellOut(worksheet=c.worksheet, cell=c.cell,
                                  target_dataset=c.target_dataset, target_field=c.target_field,
                                  expected_type=c.expected_type) for c in m.cells],
        )
        for m in list_mappings()
    ]


@router.get("/mappings/{mapping_key}/preview", response_model=MappingPreviewOut,
            summary="What a mapping reads, with units and the current platform values")
def preview_mapping(mapping_key: str, db: Session = Depends(get_db, scope="function")
                    ) -> MappingPreviewOut:
    """Read-only. Shown before any upload so the cell → field correspondence is visible."""
    try:
        return MappingPreviewOut(**mapping_preview(db, mapping_key))
    except UnknownMappingError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/template", summary="Download a pre-filled .xlsx template for a mapping",
            response_class=Response)
def download_template(
    mapping: str = Query(..., description="Known mapping key (see GET /api/ingestions/mappings)"),
    db: Session = Depends(get_db, scope="function"),
) -> Response:
    """Generate a workbook shaped exactly as the connector expects.

    Values are pre-filled from the platform's current dataset values, so the download is a
    faithful starting point rather than a blank form — edit a number, upload, and the
    change is the only difference.
    """
    try:
        content, filename = build_template(db, mapping)
    except UnknownMappingError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return Response(
        content=content,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/excel/validate", response_model=WorkbookPreviewOut,
             summary="Dry-run a workbook: what would change, and is it valid?")
def validate_excel(
    mapping: str = Query(..., description="Known mapping key (see GET /api/ingestions/mappings)"),
    file: UploadFile = File(..., description=".xlsx workbook"),
    db: Session = Depends(get_db, scope="function"),
) -> WorkbookPreviewOut:
    """Writes nothing, records no IngestionRun, and propagates nothing.

    Runs the same contract validation as the commit path, so `valid: true` here means the
    subsequent commit will not be rejected by the contract layer. Workbook-level problems
    (wrong sheet, uncached formula, unreadable file) are returned in `errors` with
    `valid: false` rather than as an HTTP error, so the UI can explain them in place.
    """
    if get_mapping(mapping) is None:
        raise HTTPException(status_code=404, detail=f"Unknown mapping key {mapping!r}.")
    data = _read_bounded(file, MAX_WORKBOOK_BYTES)
    if len(data) > MAX_WORKBOOK_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Workbook exceeds the {MAX_WORKBOOK_BYTES} byte limit.",
        )
    preview = preview_workbook(
        db, source_name=file.filename or "upload.xlsx",
        workbook_bytes=data, mapping_key=mapping,
    )
    return WorkbookPreviewOut(**preview.to_dict())


@router.post("/excel", response_model=IngestionOut, status_code=201,
             summary="Ingest an .xlsx workbook through a named mapping")
def ingest_excel(
    request: Request,
    mapping: str = Query(..., description="Known mapping key (see GET /api/ingestions/mappings)"),
    file: UploadFile = File(..., description=".xlsx workbook"),
    svc: IngestionService = Depends(get_ingestion_service),
) -> IngestionOut:
    if get_mapping(mapping) is None:
        raise HTTPException(status_code=404, detail=f"Unknown mapping key {mapping!r}.")
    data = _read_bounded(file, MAX_WORKBOOK_BYTES)
    result = svc.ingest_excel(
        source_name=file.filename or "upload.xlsx",
        workbook_bytes=data,
        mapping_key=mapping,
    )
    audit.record(
        svc._db, request=request, target_type="ingestion", target_id=result.ingestion_id,
        action="ingestion.rejected" if result.status == "rejected" else "ingestion.committed",
        outcome="failure" if result.status == "rejected" else "success",
        summary=f"{result.source_name} · {result.status}",
        detail={"sha256": result.content_sha256, "mapping": mapping, "run": result.graph_run_id,
                "error": result.error},
    )
    return IngestionOut(**result._asdict())


@router.get("", response_model=list[IngestionRunOut], summary="List recent ingestion runs")
def list_ingestions(limit: int = 50,
                    svc: IngestionService = Depends(get_ingestion_service)) -> list[IngestionRunOut]:
    return [IngestionRunOut.model_validate(r) for r in svc.list_runs(limit=limit)]


@router.get("/{ingestion_id}", response_model=IngestionRunOut, summary="Get ingestion provenance")
def get_ingestion(ingestion_id: str,
                  svc: IngestionService = Depends(get_ingestion_service)) -> IngestionRunOut:
    try:
        return IngestionRunOut.model_validate(svc.get_run(ingestion_id))
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
