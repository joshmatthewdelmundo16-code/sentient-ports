"""Collaboration cases — shared decision zones between organizations (D27).

A case has a host organization, explicit member organizations, a stated purpose and a
status. Members share specific outputs INTO the case (an approval limited to the case); only
case members can see them, and only while the case is open. Closing a case stops that
exposure immediately — without touching any member's records, which the host could not do
anyway (each organization's approvals belong to it).

Case metadata (title, purpose, members) is visible to the host and members only.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.network.service import NetworkService
from backend.app.persistence.database import (
    AuditEvent,
    CaseMember,
    CollaborationCase,
    Organization,
)
from backend.app.product.util import iso
from backend.app.security.tenancy import cross_org_session, get_scope

ROLES = ("host", "participant", "observer")


class CaseError(Exception):
    pass


class CaseNotFound(CaseError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CaseService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def _org(self) -> str:
        scope = get_scope(self._db)
        if scope is None:
            raise CaseError("Collaboration cases need an organization scope.")
        return scope.org_id

    # ------------------------------------------------------------------

    def create(self, *, title: str, purpose: str, member_org_ids: list[str],
               closes_at: datetime | None, user_id: str | None) -> dict[str, Any]:
        host = self._org()
        title, purpose = (title or "").strip(), (purpose or "").strip()
        if not title or not purpose:
            raise CaseError("A case needs a title and a purpose.")
        with cross_org_session(self._db) as s:
            known = {o.id for o in s.execute(select(Organization).where(Organization.status == "active")).scalars()}
        members = [m for m in dict.fromkeys(member_org_ids) if m != host]
        unknown = [m for m in members if m not in known]
        if unknown:
            raise CaseError(f"Unknown or inactive organization(s): {unknown}")
        if not members:
            raise CaseError("Invite at least one other organization.")
        case = CollaborationCase(title=title, purpose=purpose, status="open",
                                 created_by_user_id=user_id, closes_at=closes_at)
        self._db.add(case)
        self._db.flush()
        self._db.add(CaseMember(case_id=case.id, member_organization_id=host, role="host"))
        for m in members:
            self._db.add(CaseMember(case_id=case.id, member_organization_id=m, role="participant"))
        self._db.flush()
        return self.get(case.id)

    def _case_rows(self, s: Session, org_id: str) -> list[CollaborationCase]:
        ids = set(s.execute(select(CaseMember.case_id).where(CaseMember.member_organization_id == org_id)).scalars())
        hosted = s.execute(select(CollaborationCase).where(CollaborationCase.organization_id == org_id)).scalars().all()
        rows = {c.id: c for c in hosted}
        if ids:
            for c in s.execute(select(CollaborationCase).where(CollaborationCase.id.in_(ids))).scalars():
                rows[c.id] = c
        return sorted(rows.values(), key=lambda c: c.created_at or _now(), reverse=True)

    def _brief(self, s: Session, c: CollaborationCase, orgs: dict[str, Organization]) -> dict[str, Any]:
        members = s.execute(select(CaseMember).where(CaseMember.case_id == c.id)).scalars().all()
        return {
            "id": c.id, "title": c.title, "purpose": c.purpose, "status": c.status,
            "host": {"id": c.organization_id, "name": orgs[c.organization_id].name if c.organization_id in orgs else None},
            "members": [{"organization_id": m.member_organization_id,
                         "name": orgs[m.member_organization_id].name if m.member_organization_id in orgs else None,
                         "kind": orgs[m.member_organization_id].kind if m.member_organization_id in orgs else None,
                         "role": m.role} for m in members],
            "closes_at": iso(c.closes_at), "closed_at": iso(c.closed_at), "created_at": iso(c.created_at),
        }

    def list(self) -> list[dict[str, Any]]:
        org = self._org()
        with cross_org_session(self._db) as s:
            orgs = {o.id: o for o in s.execute(select(Organization)).scalars()}
            return [self._brief(s, c, orgs) for c in self._case_rows(s, org)]

    def get(self, case_id: str) -> dict[str, Any]:
        """The host reads its own case through the request session (so a case created or
        changed in this request is visible immediately); a member organization reads it
        through the cross-organization session, which sees committed data only."""
        org = self._org()
        own = self._db.execute(select(CollaborationCase).where(CollaborationCase.id == case_id)).scalars().first()
        if own is not None:
            brief = self._detail(self._db, own)
        else:
            with cross_org_session(self._db) as s:
                case = next((c for c in self._case_rows(s, org) if c.id == case_id), None)
                if case is None:
                    raise CaseNotFound("Not found — or your organization is not a member of this case.")
                brief = self._detail(s, case)
        shared = NetworkService(self._db).shared_with(org, case_id=case_id, include_own=True)
        brief["shared_outputs"] = [asdict(v) for v in shared] if brief["status"] == "open" else []
        brief["my_role"] = next((m["role"] for m in brief["members"] if m["organization_id"] == org), None)
        return brief

    def _detail(self, s: Session, case: CollaborationCase) -> dict[str, Any]:
        orgs = {o.id: o for o in s.execute(select(Organization)).scalars()}
        brief = self._brief(s, case, orgs)
        events = s.execute(select(AuditEvent).where(AuditEvent.target_type == "case",
                                                    AuditEvent.target_id == case.id)
                           .order_by(AuditEvent.occurred_at.desc()).limit(50)).scalars().all()
        brief["audit"] = [{"id": e.id, "occurred_at": iso(e.occurred_at), "action": e.action,
                           "outcome": e.outcome, "actor_label": e.actor_label, "summary": e.summary,
                           "organization": orgs[e.organization_id].name if e.organization_id in orgs else None}
                          for e in events]
        return brief

    def close(self, case_id: str) -> dict[str, Any]:
        org = self._org()
        case = self._db.execute(select(CollaborationCase).where(CollaborationCase.id == case_id)).scalars().first()
        if case is None:                       # scoped read: only the host can see its own case row
            raise CaseNotFound("Only the host organization can close a case.")
        if case.status != "closed":
            case.status = "closed"
            case.closed_at = _now()
            self._db.flush()
        return self.get(case_id) if org else {}

    def add_member(self, case_id: str, member_org_id: str, role: str = "participant") -> dict[str, Any]:
        if role not in ROLES or role == "host":
            raise CaseError("role must be participant or observer.")
        case = self._db.execute(select(CollaborationCase).where(CollaborationCase.id == case_id)).scalars().first()
        if case is None:
            raise CaseNotFound("Only the host organization can add members.")
        if case.status != "open":
            raise CaseError("The case is closed.")
        with cross_org_session(self._db) as s:
            if s.get(Organization, member_org_id) is None:
                raise CaseError("Unknown organization.")
        exists = self._db.execute(select(CaseMember).where(CaseMember.case_id == case_id,
                                                           CaseMember.member_organization_id == member_org_id)).scalars().first()
        if exists is None:
            self._db.add(CaseMember(case_id=case_id, member_organization_id=member_org_id, role=role))
            self._db.flush()
        return self.get(case_id)


def case_meta(case: dict[str, Any]) -> str:
    return json.dumps({"title": case["title"], "members": len(case["members"])})
