"""Participant repository — D22."""

from __future__ import annotations

from backend.app.persistence.base_repository import BaseRepository
from backend.app.persistence.database import Participant
from backend.app.persistence.exceptions import NotFoundError


class ParticipantRepository(BaseRepository[Participant]):
    model_class = Participant

    def get_by_key(self, participant_key: str) -> Participant:
        obj = (
            self._db.query(Participant)
            .filter(Participant.participant_key == participant_key)
            .first()
        )
        if obj is None:
            raise NotFoundError(f"Participant participant_key={participant_key!r} not found")
        return obj

    def find_by_key(self, participant_key: str) -> Participant | None:
        return (
            self._db.query(Participant)
            .filter(Participant.participant_key == participant_key)
            .first()
        )

    def list_recent(self, limit: int = 100, *, status: str | None = None) -> list[Participant]:
        q = self._db.query(Participant)
        if status is not None:
            q = q.filter(Participant.status == status)
        return q.order_by(Participant.created_at.desc(), Participant.id.desc()).limit(limit).all()
