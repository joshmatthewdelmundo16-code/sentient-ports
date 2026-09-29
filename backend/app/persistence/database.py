"""
SQLAlchemy persistence layer — D0 scope.

Defines:
  - engine / SessionLocal factory
  - Base declarative class
  - init_db()  — creates all tables (SQLite, demo tier)
  - get_db()   — FastAPI dependency that yields a session

All 12 core entities from §77/§138 are declared here so that
init_db() creates the full schema in one call. Later phases
will add Alembic migrations; until then, create_all() is the
source of truth for the demo.

Entities (demo-minimal — I/O and contracts as JSON columns):
  Model, ModelVersion, Dataset, DataContract, Dependency,
  ExecutionRun, ExecutionStep, Result, LineageEdge, ChangeEvent

Two more (ModelInput, ModelOutput) are represented as JSON
columns on ModelVersion per §138 "demo-minimal" note.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Generator

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)

from backend.app.config.settings import DATABASE_URL


# ---------------------------------------------------------------------------
# Engine & session factory
# ---------------------------------------------------------------------------

# SQLite: enable WAL mode and foreign-key enforcement
_connect_args: dict = {}
if DATABASE_URL.startswith("sqlite"):
    _connect_args = {"check_same_thread": False}

engine = create_engine(
    DATABASE_URL,
    connect_args=_connect_args,
    echo=False,  # set to True for SQL debug logging
)

if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _connection_record):  # type: ignore[no-untyped-def]
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


# ---------------------------------------------------------------------------
# Declarative base
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Domain entities
# ---------------------------------------------------------------------------

class Model(Base):
    """Registry entry for a federated model (identity, not executable unit)."""

    __tablename__ = "model"
    __table_args__ = (
        UniqueConstraint("owner", "name", name="uq_model_owner_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    owner: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_type: Mapped[str] = mapped_column(String(64), nullable=False, default="python")
    # Lifecycle: draft → active → deprecated → retired
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    versions: Mapped[list["ModelVersion"]] = relationship(
        "ModelVersion", back_populates="model", cascade="all, delete-orphan"
    )


class ModelVersion(Base):
    """
    Immutable executable unit of a Model (§135/§77).
    I/O spec stored as JSON strings (demo-minimal per §138).
    """

    __tablename__ = "model_version"
    __table_args__ = (
        UniqueConstraint("model_id", "semver", name="uq_version_model_semver"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    model_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("model.id", ondelete="CASCADE"), nullable=False, index=True
    )
    semver: Mapped[str] = mapped_column(String(32), nullable=False)
    # JSON-encoded list[{name, type, unit?, description?}]
    inputs_spec: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON-encoded list[{name, type, unit?, description?}]
    outputs_spec: Mapped[str | None] = mapped_column(Text, nullable=True)
    # For PythonAdapter: dotted module path of the callable
    execution_entrypoint: Mapped[str | None] = mapped_column(String(512), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    model: Mapped["Model"] = relationship("Model", back_populates="versions")
    steps: Mapped[list["ExecutionStep"]] = relationship(
        "ExecutionStep", back_populates="model_version"
    )


class Dataset(Base):
    """
    Federation join point — the named data artifact that flows between models.
    Producer → Dataset → Consumer relationship (§77/§136).
    """

    __tablename__ = "dataset"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON-encoded current value (or None before first run)
    current_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    # SHA-256 hash of current_value for change detection
    current_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    # Produced by one dependency edge, consumed by potentially many
    produced_by: Mapped[list["Dependency"]] = relationship(
        "Dependency",
        foreign_keys="Dependency.output_dataset_id",
        back_populates="output_dataset",
    )
    consumed_by: Mapped[list["Dependency"]] = relationship(
        "Dependency",
        foreign_keys="Dependency.input_dataset_id",
        back_populates="input_dataset",
    )
    results: Mapped[list["Result"]] = relationship("Result", back_populates="dataset")
    change_events: Mapped[list["ChangeEvent"]] = relationship(
        "ChangeEvent", back_populates="dataset"
    )


class DataContract(Base):
    """
    Typed I/O contract for a Dataset (§138/§77).
    Stores field schema as a JSON document.
    """

    __tablename__ = "data_contract"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    dataset_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("dataset.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # JSON: {fields: [{name, type, unit?, min?, max?, nullable?}]}
    schema_json: Mapped[str] = mapped_column(Text, nullable=False)
    semver: Mapped[str] = mapped_column(String(32), nullable=False, default="1.0.0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Dependency(Base):
    """
    Dataset-mediated dependency edge: producer model_version → dataset → consumer model_version.
    Represents A→B→C chains (§77/§136).
    """

    __tablename__ = "dependency"
    __table_args__ = (
        UniqueConstraint(
            "producer_version_id", "consumer_version_id", "input_dataset_id",
            name="uq_dep_edge"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # The model version that *produces* the output dataset
    producer_version_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("model_version.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The dataset that flows from producer to consumer
    output_dataset_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("dataset.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The model version that *consumes* the dataset
    consumer_version_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("model_version.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The dataset the consumer reads (often == output_dataset_id but modelled separately)
    input_dataset_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("dataset.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    dependency_kind: Mapped[str] = mapped_column(
        String(32), nullable=False, default="execution"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    output_dataset: Mapped["Dataset"] = relationship(
        "Dataset", foreign_keys=[output_dataset_id], back_populates="produced_by"
    )
    input_dataset: Mapped["Dataset"] = relationship(
        "Dataset", foreign_keys=[input_dataset_id], back_populates="consumed_by"
    )


class ExecutionRun(Base):
    """
    One orchestrated execution of a (sub)graph (§77/§137).
    Status lifecycle: requested → validating → running → succeeded/failed/partial
    """

    __tablename__ = "execution_run"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    scenario_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # JSON-encoded list of model_version_ids in execution order
    subgraph_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON-encoded input parameter snapshot for reproducibility
    input_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="requested", index=True
    )
    triggered_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, index=True
    )
    # JSON audit blob for extra metadata
    audit_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    steps: Mapped[list["ExecutionStep"]] = relationship(
        "ExecutionStep", back_populates="run", cascade="all, delete-orphan"
    )
    change_events: Mapped[list["ChangeEvent"]] = relationship(
        "ChangeEvent", back_populates="run"
    )


class ExecutionStep(Base):
    """
    One model execution within a run (§77/§137).
    Status: validating → running → succeeded/failed/skipped
    """

    __tablename__ = "execution_step"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("execution_run.id", ondelete="CASCADE"), nullable=False, index=True
    )
    model_version_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("model_version.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    step_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="validating")
    # JSON-encoded input snapshot for this step
    input_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Output fingerprint hash (SHA-256 of output values)
    output_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    run: Mapped["ExecutionRun"] = relationship("ExecutionRun", back_populates="steps")
    model_version: Mapped["ModelVersion | None"] = relationship(
        "ModelVersion", back_populates="steps"
    )
    results: Mapped[list["Result"]] = relationship(
        "Result", back_populates="step", cascade="all, delete-orphan"
    )
    lineage_edges: Mapped[list["LineageEdge"]] = relationship(
        "LineageEdge", back_populates="step", cascade="all, delete-orphan"
    )


class Result(Base):
    """
    Persisted output value from one step for one dataset (§77/§138).
    Immutable after creation; uniquely tied to (step_id, dataset_id).
    """

    __tablename__ = "result"
    __table_args__ = (
        UniqueConstraint("step_id", "dataset_id", name="uq_result_step_dataset"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("execution_run.id", ondelete="CASCADE"), nullable=False, index=True
    )
    step_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("execution_step.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dataset_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("dataset.id", ondelete="CASCADE"), nullable=False, index=True
    )
    model_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    scenario_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # JSON-encoded value
    value_json: Mapped[str] = mapped_column(Text, nullable=False)
    value_numeric: Mapped[float | None] = mapped_column(Float, nullable=True)
    # SHA-256 of value_json for change detection / dedup
    value_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # JSON-encoded input snapshot that produced this result
    input_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    step: Mapped["ExecutionStep"] = relationship("ExecutionStep", back_populates="results")
    dataset: Mapped["Dataset"] = relationship("Dataset", back_populates="results")
    lineage_edges: Mapped[list["LineageEdge"]] = relationship(
        "LineageEdge",
        foreign_keys="LineageEdge.target_result_id",
        back_populates="target_result",
    )


class LineageEdge(Base):
    """
    Directed lineage edge: source_result → target_result through a step (§77/§138).
    Enables forward and reverse traversal.
    """

    __tablename__ = "lineage_edge"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("execution_run.id", ondelete="CASCADE"), nullable=False, index=True
    )
    step_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("execution_step.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_result_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("result.id", ondelete="SET NULL"), nullable=True
    )
    target_result_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("result.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    step: Mapped["ExecutionStep"] = relationship("ExecutionStep", back_populates="lineage_edges")
    target_result: Mapped["Result | None"] = relationship(
        "Result", foreign_keys=[target_result_id], back_populates="lineage_edges"
    )


class ChangeEvent(Base):
    """
    Records an upstream input change that triggered a propagation run (§77/§138).
    """

    __tablename__ = "change_event"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    dataset_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("dataset.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("execution_run.id", ondelete="SET NULL"), nullable=True
    )
    # JSON-encoded old value
    old_value_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    old_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # JSON-encoded new value
    new_value_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    triggered_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, index=True
    )

    dataset: Mapped["Dataset"] = relationship("Dataset", back_populates="change_events")
    run: Mapped["ExecutionRun | None"] = relationship(
        "ExecutionRun", back_populates="change_events"
    )


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def init_db() -> None:
    """
    Create all tables in the configured database.

    Safe to call on every startup — SQLAlchemy only creates tables that
    do not exist yet (CREATE TABLE IF NOT EXISTS semantics).
    At MVP, Alembic migrations replace this for schema evolution.
    """
    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency — yields a SQLAlchemy Session and closes it afterward.

    Usage:
        @router.get("/something")
        def handler(db: Session = Depends(get_db)):
            ...
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
