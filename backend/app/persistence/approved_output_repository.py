"""ApprovedOutput repository — D22."""

from __future__ import annotations

from backend.app.persistence.base_repository import BaseRepository
from backend.app.persistence.database import ApprovedOutput


class ApprovedOutputRepository(BaseRepository[ApprovedOutput]):
    model_class = ApprovedOutput

    def find_active(
        self, participant_id: str, dataset_id: str, field_name: str
    ) -> ApprovedOutput | None:
        """The single active approval for (participant, dataset, field), or None."""
        return (
            self._db.query(ApprovedOutput)
            .filter(
                ApprovedOutput.participant_id == participant_id,
                ApprovedOutput.dataset_id == dataset_id,
                ApprovedOutput.field_name == field_name,
                ApprovedOutput.status == "active",
            )
            .first()
        )

    def list_all(
        self,
        *,
        participant_id: str | None = None,
        dataset_id: str | None = None,
        status: str | None = None,
        limit: int = 500,
    ) -> list[ApprovedOutput]:
        q = self._db.query(ApprovedOutput)
        if participant_id is not None:
            q = q.filter(ApprovedOutput.participant_id == participant_id)
        if dataset_id is not None:
            q = q.filter(ApprovedOutput.dataset_id == dataset_id)
        if status is not None:
            q = q.filter(ApprovedOutput.status == status)
        return (
            q.order_by(ApprovedOutput.created_at.desc(), ApprovedOutput.id.desc())
            .limit(limit)
            .all()
        )
