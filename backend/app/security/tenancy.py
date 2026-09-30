"""Organization isolation at the ORM layer — D26.

Why here and not in each route
------------------------------
The federation engine (registry, orchestration, propagation, results, lineage, scenarios)
queries the database from dozens of places. Threading an organization filter through every
one of them would mean rewriting the engine and hoping nothing was missed. Instead, a
session carries a TenantScope, and two SQLAlchemy session events enforce it for every
query and every insert that session makes:

  do_orm_execute  adds `organization_id = :org` (via with_loader_criteria) to every ORM
                  SELECT / UPDATE / DELETE touching a tenant table — including
                  Session.get(), relationship lazy loads and count(*) queries. A record in
                  another organization simply does not exist for that session: fetching it
                  by id returns nothing, which the API reports as 404.
  before_flush    stamps organization_id on every new tenant row (runs, steps, results,
                  lineage, change events, datasets… created by the engine) and refuses a
                  flush that would write a row into a different organization.

Sessions without a scope (migrations, seeds run explicitly per organization, tests of the
engine itself, local single-developer mode without a selected scope) behave exactly as
before. Reading across organizations — which only the governed-sharing code needs — is done
in a separate, short-lived session (`cross_org_session`) so the request session's identity
map can never hold another organization's objects.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from sqlalchemy import event, or_
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria

from backend.app.persistence.database import (
    ApprovedOutput,
    Baseline,
    ChangeEvent,
    DataContract,
    Dataset,
    Dependency,
    ExecutionRun,
    ExecutionStep,
    IngestionRun,
    LineageEdge,
    Model,
    ModelIOBinding,
    ModelVersion,
    Participant,
    Result,
    Scenario,
    ScenarioOverride,
)

TENANT_CLASSES: tuple[type, ...] = (
    Model, ModelVersion, ModelIOBinding, Dataset, DataContract, Dependency, ExecutionRun,
    ExecutionStep, Result, LineageEdge, ChangeEvent, IngestionRun, Baseline, Scenario,
    ScenarioOverride, Participant, ApprovedOutput,
)

_SCOPE_KEY = "tenant_scope"


class TenantViolation(RuntimeError):
    """A flush tried to write a row into an organization other than the session's scope."""


@dataclass(frozen=True)
class TenantScope:
    org_id: str
    # Local single-developer mode only: rows created before tenancy (organization_id NULL)
    # stay visible. Never true for a signed-in user.
    include_unowned: bool = False


def register_tenant_class(cls: type) -> None:
    """Later phases add tenant tables (collaboration, connectors, plans…) here."""
    global TENANT_CLASSES
    if cls not in TENANT_CLASSES:
        TENANT_CLASSES = TENANT_CLASSES + (cls,)


def set_scope(db: Session, org_id: str, *, include_unowned: bool = False) -> None:
    db.info[_SCOPE_KEY] = TenantScope(org_id, include_unowned)


def get_scope(db: Session) -> TenantScope | None:
    return db.info.get(_SCOPE_KEY)


def clear_scope(db: Session) -> None:
    db.info.pop(_SCOPE_KEY, None)


@contextmanager
def tenant_scope(db: Session, org_id: str, *, include_unowned: bool = False) -> Iterator[Session]:
    """Run a block (a seed, a script, a background job) inside one organization."""
    previous = db.info.get(_SCOPE_KEY)
    set_scope(db, org_id, include_unowned=include_unowned)
    try:
        yield db
    finally:
        if previous is None:
            db.info.pop(_SCOPE_KEY, None)
        else:
            db.info[_SCOPE_KEY] = previous


@contextmanager
def cross_org_session(db: Session) -> Iterator[Session]:
    """A separate, unscoped, read-only session for governed cross-organization reads.

    Only the sharing code (approved outputs → hubs, collaboration cases) uses this, and it
    returns plain values, never ORM objects, so nothing loaded here can leak into the
    request session's identity map.
    """
    other = Session(bind=db.get_bind(), autoflush=False)
    try:
        yield other
    finally:
        other.rollback()
        other.close()


def _only(org_id: str):
    return lambda cls: cls.organization_id == org_id


def _own_or_unowned(org_id: str):
    return lambda cls: or_(cls.organization_id == org_id, cls.organization_id.is_(None))


@event.listens_for(Session, "do_orm_execute")
def _filter_reads(state: ORMExecuteState) -> None:
    scope: TenantScope | None = state.session.info.get(_SCOPE_KEY)
    if scope is None:
        return
    if not (state.is_select or state.is_update or state.is_delete):
        return
    criteria = _own_or_unowned(scope.org_id) if scope.include_unowned else _only(scope.org_id)
    state.statement = state.statement.options(*(
        with_loader_criteria(cls, criteria, include_aliases=True, track_closure_variables=True)
        for cls in TENANT_CLASSES
    ))


@event.listens_for(Session, "before_flush")
def _stamp_writes(session: Session, _flush_context, _instances) -> None:
    scope: TenantScope | None = session.info.get(_SCOPE_KEY)
    if scope is None:
        return
    for obj in session.new:
        if isinstance(obj, TENANT_CLASSES):
            current = getattr(obj, "organization_id", None)
            if current is None:
                obj.organization_id = scope.org_id
            elif current != scope.org_id:
                raise TenantViolation(
                    f"Refusing to create a {type(obj).__name__} in another organization.")
    for obj in session.dirty:
        if isinstance(obj, TENANT_CLASSES) and session.is_modified(obj):
            current = getattr(obj, "organization_id", None)
            if current is not None and current != scope.org_id:
                raise TenantViolation(
                    f"Refusing to modify a {type(obj).__name__} of another organization.")
    for obj in session.deleted:
        if isinstance(obj, TENANT_CLASSES):
            current = getattr(obj, "organization_id", None)
            if current is not None and current != scope.org_id:
                raise TenantViolation(
                    f"Refusing to delete a {type(obj).__name__} of another organization.")
