"""Governance service — D22 (Participants & Approved Outputs).

A lightweight federation-governance layer. Participants are federation ACTORS, not
application users: there is no authentication, credential, RBAC, or policy engine here.
ApprovedOutputs are governance metadata describing what a participant has explicitly approved
for external/output exposure. Registration never implies shareability, and nothing is ever
auto-approved (in particular, scenario outputs are never auto-approved).

The platform stays authoritative for models, datasets, contracts, GraphRuns, results,
lineage, scenarios and provenance. This service only records governance intent and answers
approval checks; it never mutates execution state and does not touch D21 (Airflow) at all.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from backend.app.persistence.approved_output_repository import ApprovedOutputRepository
from backend.app.persistence.database import ApprovedOutput, Participant
from backend.app.persistence.dataset_repository import DatasetRepository
from backend.app.persistence.exceptions import DuplicateError, NotFoundError
from backend.app.persistence.participant_repository import ParticipantRepository

PARTICIPANT_STATUSES = frozenset({"active", "inactive"})
APPROVAL_STATUSES = frozenset({"active", "revoked"})


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class GovernanceError(Exception):
    """Base governance error."""


class ParticipantNotFoundError(GovernanceError):
    """No participant with the given id/key exists."""


class DuplicateParticipantError(GovernanceError):
    """A participant with this participant_key already exists."""


class ParticipantValidationError(GovernanceError):
    """Invalid participant input (missing key/name, bad status, unserializable metadata)."""


class ParticipantInactiveError(GovernanceError):
    """The participant is inactive and cannot approve outputs."""


class ApprovedOutputNotFoundError(GovernanceError):
    """No approved output with the given id exists."""


class DuplicateApprovalError(GovernanceError):
    """An active approval already exists for this participant/dataset/field."""


class ApprovalValidationError(GovernanceError):
    """Invalid approval input (unknown dataset, empty field, bad expiry/metadata)."""


# ---------------------------------------------------------------------------
# Read-only exposure view row
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExposedField:
    participant_id: str
    participant_key: str
    dataset_id: str
    dataset_name: str
    field_name: str
    purpose: str | None
    value: Any            # the approved field's current value (None if absent/unset)
    value_present: bool   # whether the field was actually present in the dataset value
    approved_at: datetime | None
    expires_at: datetime | None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(dt: datetime | None) -> datetime | None:
    """Treat naive datetimes (SQLite round-trips) as UTC so comparisons are safe."""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _dump_meta(metadata: dict[str, Any] | None) -> str | None:
    if metadata is None:
        return None
    if not isinstance(metadata, dict):
        raise ParticipantValidationError("metadata must be a JSON object.")
    try:
        return json.dumps(metadata)
    except (TypeError, ValueError) as exc:
        raise ParticipantValidationError(f"metadata is not JSON-serializable: {exc}") from exc


def load_meta(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class GovernanceService:
    def __init__(self, db: Session) -> None:
        self._db = db
        self._participants = ParticipantRepository(db)
        self._approvals = ApprovedOutputRepository(db)
        self._datasets = DatasetRepository(db)

    # ------------------------------------------------------------------
    # Participants
    # ------------------------------------------------------------------

    def register_participant(
        self,
        *,
        participant_key: str,
        name: str,
        description: str | None = None,
        status: str = "active",
        metadata: dict[str, Any] | None = None,
    ) -> Participant:
        key = (participant_key or "").strip()
        if not key:
            raise ParticipantValidationError("participant_key is required.")
        if not (name or "").strip():
            raise ParticipantValidationError("name is required.")
        if status not in PARTICIPANT_STATUSES:
            raise ParticipantValidationError(
                f"status must be one of {sorted(PARTICIPANT_STATUSES)}, got {status!r}."
            )
        meta_json = _dump_meta(metadata)
        try:
            return self._participants.add(Participant(
                participant_key=key, name=name.strip(), description=description,
                status=status, meta_json=meta_json,
            ))
        except DuplicateError as exc:
            raise DuplicateParticipantError(
                f"Participant participant_key={key!r} already exists."
            ) from exc

    def get_participant(self, participant_id: str) -> Participant:
        try:
            return self._participants.get(participant_id)
        except NotFoundError as exc:
            raise ParticipantNotFoundError(f"Participant {participant_id!r} not found.") from exc

    def get_participant_by_key(self, participant_key: str) -> Participant:
        try:
            return self._participants.get_by_key(participant_key)
        except NotFoundError as exc:
            raise ParticipantNotFoundError(
                f"Participant participant_key={participant_key!r} not found."
            ) from exc

    def list_participants(self, *, limit: int = 100, status: str | None = None) -> list[Participant]:
        return self._participants.list_recent(limit=limit, status=status)

    def update_participant(
        self,
        participant_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        status: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Participant:
        p = self.get_participant(participant_id)
        if name is not None:
            if not name.strip():
                raise ParticipantValidationError("name cannot be blank.")
            p.name = name.strip()
        if description is not None:
            p.description = description
        if status is not None:
            if status not in PARTICIPANT_STATUSES:
                raise ParticipantValidationError(
                    f"status must be one of {sorted(PARTICIPANT_STATUSES)}, got {status!r}."
                )
            p.status = status
        if metadata is not None:
            p.meta_json = _dump_meta(metadata)
        self._db.flush()
        return p

    def set_status(self, participant_id: str, status: str) -> Participant:
        return self.update_participant(participant_id, status=status)

    # ------------------------------------------------------------------
    # Ownership (minimal, additive; owner may be cleared with None)
    # ------------------------------------------------------------------

    def set_dataset_owner(self, dataset_id: str, participant_id: str | None) -> None:
        try:
            dataset = self._datasets.get(dataset_id)
        except NotFoundError as exc:
            raise ApprovalValidationError(f"Dataset {dataset_id!r} not found.") from exc
        if participant_id is not None:
            self.get_participant(participant_id)  # validate existence
        dataset.owner_participant_id = participant_id
        self._db.flush()

    def set_model_version_owner(self, version_id: str, participant_id: str | None) -> None:
        from backend.app.persistence.model_version_repository import ModelVersionRepository

        versions = ModelVersionRepository(self._db)
        try:
            version = versions.get(version_id)
        except NotFoundError as exc:
            raise ApprovalValidationError(f"Model version {version_id!r} not found.") from exc
        if participant_id is not None:
            self.get_participant(participant_id)
        version.owner_participant_id = participant_id
        self._db.flush()

    # ------------------------------------------------------------------
    # Approved outputs
    # ------------------------------------------------------------------

    def create_approval(
        self,
        *,
        participant_id: str,
        dataset_id: str,
        field_name: str,
        purpose: str | None = None,
        expires_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ApprovedOutput:
        participant = self.get_participant(participant_id)
        if participant.status != "active":
            raise ParticipantInactiveError(
                f"Participant {participant_id!r} is inactive and cannot approve outputs."
            )
        field = (field_name or "").strip()
        if not field:
            raise ApprovalValidationError("field_name is required.")
        try:
            self._datasets.get(dataset_id)
        except NotFoundError as exc:
            raise ApprovalValidationError(f"Dataset {dataset_id!r} not found.") from exc
        if expires_at is not None and _as_aware(expires_at) <= _now():
            raise ApprovalValidationError("expires_at must be in the future.")
        meta_json = _dump_meta(metadata)

        # Explicit duplicate check (the partial unique index is the hard guarantee).
        if self._approvals.find_active(participant_id, dataset_id, field) is not None:
            raise DuplicateApprovalError(
                f"An active approval already exists for participant={participant_id!r}, "
                f"dataset={dataset_id!r}, field={field!r}."
            )
        try:
            return self._approvals.add(ApprovedOutput(
                participant_id=participant_id, dataset_id=dataset_id, field_name=field,
                purpose=purpose, status="active", approved_at=_now(),
                expires_at=expires_at, meta_json=meta_json,
            ))
        except DuplicateError as exc:  # race against the partial unique index
            raise DuplicateApprovalError(
                f"An active approval already exists for participant={participant_id!r}, "
                f"dataset={dataset_id!r}, field={field!r}."
            ) from exc

    def get_approval(self, approval_id: str) -> ApprovedOutput:
        try:
            return self._approvals.get(approval_id)
        except NotFoundError as exc:
            raise ApprovedOutputNotFoundError(f"Approved output {approval_id!r} not found.") from exc

    def revoke_approval(self, approval_id: str) -> ApprovedOutput:
        approval = self.get_approval(approval_id)
        if approval.status != "revoked":
            approval.status = "revoked"
            approval.revoked_at = _now()
            self._db.flush()
        return approval

    def list_approvals(
        self,
        *,
        participant_id: str | None = None,
        dataset_id: str | None = None,
        status: str | None = None,
        limit: int = 500,
    ) -> list[ApprovedOutput]:
        return self._approvals.list_all(
            participant_id=participant_id, dataset_id=dataset_id, status=status, limit=limit,
        )

    # ------------------------------------------------------------------
    # Approval checks + read-only exposure view
    # ------------------------------------------------------------------

    def is_expired(self, approval: ApprovedOutput, *, at: datetime | None = None) -> bool:
        exp = _as_aware(approval.expires_at)
        return exp is not None and exp <= (at or _now())

    def is_approved(
        self, participant_id: str, dataset_id: str, field_name: str, *, at: datetime | None = None
    ) -> bool:
        """True iff there is an active, non-expired approval for the (active) participant."""
        approval = self._approvals.find_active(participant_id, dataset_id, (field_name or "").strip())
        if approval is None or self.is_expired(approval, at=at):
            return False
        participant = self._db.get(Participant, participant_id)
        return participant is not None and participant.status == "active"

    def approved_outputs_view(
        self,
        *,
        participant_id: str | None = None,
        dataset_id: str | None = None,
        at: datetime | None = None,
    ) -> list[ExposedField]:
        """Read-only exposure boundary: only approved, non-expired fields of active
        participants, each carrying the current value of that single field.

        This exposes individual approved fields, never whole datasets, and never scenario
        outputs (which are never approved). It reads current dataset values but mutates
        nothing; internal execution and results remain fully available elsewhere.
        """
        now = at or _now()
        rows = self._approvals.list_all(
            participant_id=participant_id, dataset_id=dataset_id, status="active",
        )
        view: list[ExposedField] = []
        for a in rows:
            if self.is_expired(a, at=now):
                continue
            participant = self._db.get(Participant, a.participant_id)
            if participant is None or participant.status != "active":
                continue
            try:
                dataset = self._datasets.get(a.dataset_id)
            except NotFoundError:
                continue
            value: Any = None
            present = False
            if dataset.current_value is not None:
                record = json.loads(dataset.current_value)
                if isinstance(record, dict) and a.field_name in record:
                    value = record[a.field_name]
                    present = True
            view.append(ExposedField(
                participant_id=participant.id,
                participant_key=participant.participant_key,
                dataset_id=dataset.id,
                dataset_name=dataset.name,
                field_name=a.field_name,
                purpose=a.purpose,
                value=value,
                value_present=present,
                approved_at=a.approved_at,
                expires_at=a.expires_at,
            ))
        return view

    # ------------------------------------------------------------------
    # Summary (small governance overview for the UI/API)
    # ------------------------------------------------------------------

    def summary(self) -> dict[str, int]:
        participants = self._participants.list_recent(limit=10_000)
        approvals = self._approvals.list_all(limit=10_000)
        now = _now()
        active_effective = sum(
            1 for a in approvals if a.status == "active" and not self.is_expired(a, at=now)
        )
        return {
            "participants": len(participants),
            "active_participants": sum(1 for p in participants if p.status == "active"),
            "approvals": len(approvals),
            "active_approvals": sum(1 for a in approvals if a.status == "active"),
            "effective_approvals": active_effective,
            "revoked_approvals": sum(1 for a in approvals if a.status == "revoked"),
        }
