"""
SQLAlchemy persistence layer.

Defines:
  - engine / SessionLocal factory (SQLite or PostgreSQL)
  - Base declarative class
  - init_db()  — creates all tables
  - get_db()   — FastAPI dependency that yields a session

Entities:
  Model, ModelVersion, ModelIOBinding, Dataset, DataContract, Dependency,
  ExecutionRun (the GraphRun), ExecutionStep, Result, LineageEdge, ChangeEvent
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
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    text,
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

def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def _build_engine(url: str):
    """Engine for SQLite (local) or PostgreSQL (shared — e.g. Supabase).

    PostgreSQL notes (D25):
      * prepare_threshold=None disables server-side prepared statements, which Supavisor /
        PgBouncer transaction pooling cannot route. Harmless on a direct connection.
      * pool_pre_ping + pool_recycle survive pooler/NAT idle reaping without surfacing a
        stale-connection error to a user request.
      * statement_timeout is set per connection only when NOT behind a transaction pooler,
        where startup options are not reliably forwarded.
      * DB_POOL_MODE=null → NullPool (no client-side pooling), for tiny/serverless hosts.
    """
    from sqlalchemy.pool import NullPool

    from backend.app.config import settings as s

    kwargs: dict = {"echo": False}

    if _is_sqlite(url):
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        connect_args: dict = {"prepare_threshold": None, "connect_timeout": s.DB_CONNECT_TIMEOUT_S}
        if not s.DB_TRANSACTION_POOLER and s.DB_STATEMENT_TIMEOUT_MS > 0:
            connect_args["options"] = f"-c statement_timeout={s.DB_STATEMENT_TIMEOUT_MS}"
        kwargs["connect_args"] = connect_args
        kwargs["pool_pre_ping"] = True
        if s.DB_POOL_MODE == "null":
            kwargs["poolclass"] = NullPool
        else:
            kwargs["pool_size"] = s.DB_POOL_SIZE
            kwargs["max_overflow"] = s.DB_MAX_OVERFLOW
            kwargs["pool_timeout"] = s.DB_POOL_TIMEOUT_S
            kwargs["pool_recycle"] = s.DB_POOL_RECYCLE_S

    eng = create_engine(url, **kwargs)

    if _is_sqlite(url):
        @event.listens_for(eng, "connect")
        def _set_sqlite_pragmas(dbapi_conn, _connection_record):  # type: ignore[no-untyped-def]
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return eng


engine = _build_engine(DATABASE_URL)

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
    # D25: model-library card (purpose, formula, domain, provider, provenance…) as JSON.
    card_json: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    # Persisted execution behaviour: adapter kind + JSON config (e.g. {"scalar": 2.0})
    adapter_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    adapter_config: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # D22: optional owning federation participant (additive, nullable; SET NULL on delete).
    owner_participant_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("participant.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    model: Mapped["Model"] = relationship("Model", back_populates="versions")
    steps: Mapped[list["ExecutionStep"]] = relationship(
        "ExecutionStep", back_populates="model_version"
    )
    io_bindings: Mapped[list["ModelIOBinding"]] = relationship(
        "ModelIOBinding", back_populates="model_version", cascade="all, delete-orphan"
    )


class ModelIOBinding(Base):
    """
    Explicit declaration that a model version reads (input) or writes (output) a dataset.

    field_map (JSON, optional):
      input  → {"<dataset_field>": "<model_input_name>"}  (only mapped fields are passed)
      output → {"<model_output_name>": "<dataset_field>"} (only mapped outputs are written)
      NULL   → identity mapping of every field.
    """

    __tablename__ = "model_io_binding"
    __table_args__ = (
        UniqueConstraint(
            "model_version_id", "dataset_id", "direction", name="uq_io_binding"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    model_version_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("model_version.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dataset_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("dataset.id", ondelete="CASCADE"), nullable=False, index=True
    )
    direction: Mapped[str] = mapped_column(String(8), nullable=False)  # "input" | "output"
    field_map: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    model_version: Mapped["ModelVersion"] = relationship(
        "ModelVersion", back_populates="io_bindings"
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
    # D25: business-readable label ("Port assumptions"); NULL → derived from name.
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # JSON-encoded current value (or None before first run)
    current_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    # SHA-256 hash of current_value for change detection
    current_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # D22: optional owning federation participant (additive, nullable; SET NULL on delete).
    owner_participant_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("participant.id", ondelete="SET NULL"), nullable=True, index=True
    )
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
    The GraphRun: one orchestrated execution of a (sub)graph (§77/§137).
    A graph execution is exactly one run whose ExecutionSteps are its model steps.
    run_kind: "graph" | "single" | "legacy" (pre-D16 one-run-per-model rows)
    Status lifecycle: running → succeeded | failed
    """

    __tablename__ = "execution_run"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="graph")
    executor: Mapped[str] = mapped_column(String(32), nullable=False, default="in_process")
    target_version_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("model_version.id", ondelete="SET NULL"), nullable=True
    )
    trigger_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
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
        "ChangeEvent", back_populates="run", foreign_keys="ChangeEvent.run_id"
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
    source_dataset_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("dataset.id", ondelete="SET NULL"), nullable=True
    )
    target_dataset_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("dataset.id", ondelete="SET NULL"), nullable=True
    )
    # Set when the input value originated outside this run (external write or earlier run)
    source_change_event_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("change_event.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    step: Mapped["ExecutionStep"] = relationship("ExecutionStep", back_populates="lineage_edges")
    target_result: Mapped["Result | None"] = relationship(
        "Result", foreign_keys=[target_result_id], back_populates="lineage_edges"
    )


class ChangeEvent(Base):
    """
    Records a change to a dataset value, or a model re-execution trigger (§77/§138).

    source_type: "external" | "seed" | "model_output" | "model_trigger"
    run_id:             run in which this change was propagated (NULL = not yet propagated)
    produced_by_run_id: run that wrote the value (model_output only)
    """

    __tablename__ = "change_event"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    dataset_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("dataset.id", ondelete="CASCADE"), nullable=False, index=True
    )
    run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("execution_run.id", ondelete="SET NULL"), nullable=True
    )
    source_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Free-form provenance reference (e.g. "api", later "excel:<file>!<sheet>!<cell>")
    source_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_version_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("model_version.id", ondelete="SET NULL"), nullable=True
    )
    produced_by_run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("execution_run.id", ondelete="SET NULL"), nullable=True
    )
    result_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("result.id", ondelete="SET NULL"), nullable=True
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
        "ExecutionRun", back_populates="change_events", foreign_keys=[run_id]
    )


class IngestionRun(Base):
    """
    Durable record of one Excel (or future source) ingestion attempt — D18.

    Persisted for every attempt, including rejected ones (a rejected ingestion writes no
    dataset value and therefore produces no ChangeEvent, so failure metadata has no other
    home). content_sha256 is the authoritative content identity of the uploaded bytes.

    status: received → parsed → ingested | unchanged | rejected
    """

    __tablename__ = "ingestion_run"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    source_name: Mapped[str] = mapped_column(String(512), nullable=False)
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mapping_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="received")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON-encoded list of dataset IDs written by this ingestion
    dataset_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    change_event_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("change_event.id", ondelete="SET NULL"), nullable=True
    )
    graph_run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("execution_run.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, index=True
    )


class Baseline(Base):
    """
    Named, persisted baseline — an authoritative input-state identity for the federation (D19).

    A baseline is metadata plus a pointer to the exact authoritative GraphRun
    (baseline_run_id). It is kept deliberately distinct from dataset values (which live on
    Dataset.current_value) and from execution/results. Executing a baseline is an ordinary
    GraphRun that publishes normally; the produced run is pinned here for exact comparison.
    """

    __tablename__ = "baseline"
    __table_args__ = (
        UniqueConstraint("name", name="uq_baseline_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Lifecycle: active → archived
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    target_version_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("model_version.id", ondelete="SET NULL"), nullable=True
    )
    # The exact authoritative GraphRun (set once the baseline is executed).
    baseline_run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("execution_run.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    scenarios: Mapped[list["Scenario"]] = relationship(
        "Scenario", back_populates="baseline", cascade="all, delete-orphan"
    )


class Scenario(Base):
    """
    Named scenario derived from a baseline (D19).

    A scenario is an input-state definition plus metadata: the baseline it inherits from,
    a set of explicit field overrides (ScenarioOverride), and — once executed — a pointer to
    its exact GraphRun (scenario_run_id). A scenario never overwrites its baseline; its run
    is read-only with respect to shared dataset state (executed with publish suppressed).
    """

    __tablename__ = "scenario"
    __table_args__ = (
        UniqueConstraint("baseline_id", "name", name="uq_scenario_baseline_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    baseline_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("baseline.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Lifecycle: draft → executed → archived
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    # Optional per-scenario target; falls back to the baseline's target when null.
    target_version_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("model_version.id", ondelete="SET NULL"), nullable=True
    )
    # The exact scenario GraphRun (set once the scenario is executed).
    scenario_run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("execution_run.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    baseline: Mapped["Baseline"] = relationship("Baseline", back_populates="scenarios")
    overrides: Mapped[list["ScenarioOverride"]] = relationship(
        "ScenarioOverride", back_populates="scenario", cascade="all, delete-orphan"
    )


class ScenarioOverride(Base):
    """
    One explicit, typed dataset-field override for a scenario (D19).

    Generic against datasets/contracts: keyed by (dataset_id, field_name); the value is
    stored as canonical JSON. Not specific to any domain field. Applied at input-assembly
    time only — it never mutates Dataset.current_value.
    """

    __tablename__ = "scenario_override"
    __table_args__ = (
        UniqueConstraint(
            "scenario_id", "dataset_id", "field_name", name="uq_scenario_override"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    scenario_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("scenario.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dataset_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("dataset.id", ondelete="CASCADE"), nullable=False, index=True
    )
    field_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # JSON-encoded scalar/structured override value for the field.
    value_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    scenario: Mapped["Scenario"] = relationship("Scenario", back_populates="overrides")


class Participant(Base):
    """A federation actor — governance metadata, NOT an application user (D22).

    Participants describe who contributes to / owns parts of the federation and who has
    approved specific outputs for external exposure. There is no authentication, credential,
    password, or access-control semantics here: registering a participant does NOT grant
    access or imply that anything they own is shareable. The platform stays authoritative for
    models, datasets, contracts, GraphRuns, results, lineage, scenarios and provenance.

    status: "active" | "inactive". metadata is free-form JSON (mapped as meta_json; the DB
    column is "metadata_json" to avoid SQLAlchemy's reserved 'metadata' attribute).
    """

    __tablename__ = "participant"
    __table_args__ = (
        UniqueConstraint("participant_key", name="uq_participant_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    participant_key: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", index=True)
    meta_json: Mapped[str | None] = mapped_column("metadata_json", Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    approved_outputs: Mapped[list["ApprovedOutput"]] = relationship(
        "ApprovedOutput", back_populates="participant", cascade="all, delete-orphan"
    )


class ApprovedOutput(Base):
    """A participant's explicit approval of one dataset field for external/output exposure (D22).

    This is governance metadata describing what a participant approved for exposure; it does
    not itself expose anything. A partial unique index enforces at most one ACTIVE approval
    per (participant, dataset, field), while allowing re-approval after a revoke. Expiry
    (expires_at) is validated at check time (an expired row stays 'active' in the DB but is
    treated as not-approved), never by a DB constraint.

    status: "active" | "revoked".
    """

    __tablename__ = "approved_output"
    __table_args__ = (
        # At most one ACTIVE approval per (participant, dataset, field). Revoked rows are
        # excluded, so re-approval after revoke is allowed. Works on SQLite and PostgreSQL.
        Index(
            "uq_approved_output_active",
            "participant_id", "dataset_id", "field_name",
            unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    participant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("participant.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dataset_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("dataset.id", ondelete="CASCADE"), nullable=False, index=True
    )
    field_name: Mapped[str] = mapped_column(String(255), nullable=False)
    purpose: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active", index=True)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    meta_json: Mapped[str | None] = mapped_column("metadata_json", Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    participant: Mapped["Participant"] = relationship(
        "Participant", back_populates="approved_outputs"
    )
    dataset: Mapped["Dataset"] = relationship("Dataset")


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
    FastAPI dependency — yields a SQLAlchemy Session, commits on success,
    rolls back on exception, and closes afterward.

    Usage:
        @router.get("/something")
        def handler(db: Session = Depends(get_db)):
            ...
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
