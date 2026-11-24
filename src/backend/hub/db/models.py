"""The hub's tables, at home (wiki: Overview > Database). Alembic migrations are made from these models."""

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    DDL,
    JSON,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    TypeDecorator,
    event,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from backend.common.vocab import (
    Channel,
    ControlKind,
    ControlState,
    Decision,
    DeviceAction,
    DeviceType,
    Independence,
    MappingStatus,
    Risk,
    RoutineSource,
    RunResult,
    Trigger,
)


class UtcTime(TypeDecorator):
    """UTC ISO 8601 text with milliseconds, for example 2026-11-17T12:05:03.120Z."""

    impl = String(24)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("times must be timezone-aware UTC")
        return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def process_result_value(self, value: str | None, dialect):
        return None if value is None else datetime.fromisoformat(value.replace("Z", "+00:00"))


def one_of(column: str, values: type[StrEnum] | list[str], nullable: bool = False) -> CheckConstraint:
    vals = [v.value for v in values] if isinstance(values, type) else values
    cond = f"{column} IN ({', '.join(repr(v) for v in vals)})"
    if nullable:
        cond = f"{column} IS NULL OR {cond}"
    return CheckConstraint(cond, name=f"ck_{column}")


class Base(DeclarativeBase):
    pass


# --- Setup: S1 and S2 -----------------------------------------------------------


class Scan(Base):
    __tablename__ = "scans"
    scan_id: Mapped[str] = mapped_column(String, primary_key=True)
    spot_id: Mapped[str] = mapped_column(String)
    started_at: Mapped[datetime] = mapped_column(UtcTime)
    done_at: Mapped[datetime | None] = mapped_column(UtcTime)
    found: Mapped[int | None] = mapped_column(Integer)
    skipped: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[str | None] = mapped_column(Text)


class Crop(Base):
    __tablename__ = "crops"
    __table_args__ = (one_of("kind", ["control", "device_before", "device_after", "check"]),)
    crop_id: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String)
    path: Mapped[str] = mapped_column(String)
    w: Mapped[int] = mapped_column(Integer)
    h: Mapped[int] = mapped_column(Integer)
    received_at: Mapped[datetime] = mapped_column(UtcTime)


class Control(Base):
    __tablename__ = "controls"
    __table_args__ = (one_of("kind", ControlKind), one_of("state", ControlState))
    control_id: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String)
    state: Mapped[str] = mapped_column(String)
    state_confidence: Mapped[float] = mapped_column(Float)
    actions: Mapped[list] = mapped_column(JSON)
    panel_point: Mapped[list | None] = mapped_column(JSON)
    # 384 float32, little-endian; null until the enclave computes fingerprints
    fingerprint: Mapped[bytes | None] = mapped_column(LargeBinary)
    crop_id: Mapped[str | None] = mapped_column(ForeignKey("crops.crop_id"))
    seen_spot_id: Mapped[str] = mapped_column(String)
    press_spot_id: Mapped[str | None] = mapped_column(String)
    seen_at: Mapped[datetime] = mapped_column(UtcTime)
    created_at: Mapped[datetime] = mapped_column(UtcTime)


class Device(Base):
    __tablename__ = "devices"
    __table_args__ = (one_of("type", DeviceType), one_of("risk", Risk))
    device_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    type: Mapped[str] = mapped_column(String)
    room: Mapped[str] = mapped_column(String)
    risk: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(UtcTime)
    updated_at: Mapped[datetime] = mapped_column(UtcTime)


class Mapping(Base):
    __tablename__ = "mappings"
    __table_args__ = (
        one_of("status", MappingStatus),
        one_of("method", ["watch", "test"]),
        one_of("control_before", ControlState),
        one_of("control_after", ControlState),
        one_of("device_type_seen", DeviceType, nullable=True),
        CheckConstraint("device_id IS NOT NULL OR status = 'Pending'", name="ck_device_unless_pending"),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_confidence"),
        # At most one mapping that is not Rejected per control and device.
        Index(
            "ux_mapping_pair",
            "control_id",
            "device_id",
            unique=True,
            sqlite_where=text("status <> 'Rejected'"),
        ),
    )
    mapping_id: Mapped[str] = mapped_column(String, primary_key=True)
    control_id: Mapped[str] = mapped_column(ForeignKey("controls.control_id"))
    device_id: Mapped[str | None] = mapped_column(ForeignKey("devices.device_id"))
    status: Mapped[str] = mapped_column(String)
    confidence: Mapped[float] = mapped_column(Float)
    method: Mapped[str] = mapped_column(String)
    control_before: Mapped[str] = mapped_column(String)
    control_after: Mapped[str] = mapped_column(String)
    device_box: Mapped[list] = mapped_column(JSON)
    verify_spot_id: Mapped[str] = mapped_column(String)
    change_score: Mapped[float] = mapped_column(Float)
    device_type_seen: Mapped[str | None] = mapped_column(String)
    device_score: Mapped[float | None] = mapped_column(Float)
    before_crop_id: Mapped[str] = mapped_column(ForeignKey("crops.crop_id"))
    after_crop_id: Mapped[str] = mapped_column(ForeignKey("crops.crop_id"))
    status_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UtcTime)
    updated_at: Mapped[datetime] = mapped_column(UtcTime)


class ChannelRow(Base):
    """The Channel record of the data model: scores per device and channel."""

    __tablename__ = "channels"
    __table_args__ = (
        one_of("channel", Channel),
        CheckConstraint("success_score BETWEEN 0 AND 1", name="ck_success_score"),
    )
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.device_id"), primary_key=True)
    channel: Mapped[str] = mapped_column(String, primary_key=True)
    address: Mapped[dict | None] = mapped_column(JSON)
    success_score: Mapped[float] = mapped_column(Float)
    latency_ms: Mapped[int] = mapped_column(Integer)
    tries: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(UtcTime)


# --- Routines: S3 and S4 ------------------------------------------------------------


class Use(Base):
    __tablename__ = "uses"
    __table_args__ = (
        one_of("action", DeviceAction),
        one_of("source", ["watch", "seed"]),
        Index("ix_uses_device_at", "device_id", "at"),
    )
    use_id: Mapped[str] = mapped_column(String, primary_key=True)
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.device_id"))
    control_id: Mapped[str | None] = mapped_column(ForeignKey("controls.control_id"))
    action: Mapped[str] = mapped_column(String)
    at: Mapped[datetime] = mapped_column(UtcTime)
    source: Mapped[str] = mapped_column(String)


class Routine(Base):
    __tablename__ = "routines"
    __table_args__ = (
        one_of("action", DeviceAction),
        one_of("source", RoutineSource),
        one_of("independence", Independence),
    )
    routine_id: Mapped[str] = mapped_column(String, primary_key=True)
    mapping_id: Mapped[str] = mapped_column(ForeignKey("mappings.mapping_id"))
    action: Mapped[str] = mapped_column(String)
    time: Mapped[str | None] = mapped_column(String(5))  # local time of day, "21:00"
    source: Mapped[str] = mapped_column(String)
    independence: Mapped[str] = mapped_column(String)
    priority: Mapped[float] = mapped_column(Float)
    reject_streak: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UtcTime)
    updated_at: Mapped[datetime] = mapped_column(UtcTime)
    removed_at: Mapped[datetime | None] = mapped_column(UtcTime)


class Feedback(Base):
    __tablename__ = "feedback"
    __table_args__ = (one_of("kind", ["keep", "change", "remove", "accept", "reject"]),)
    feedback_id: Mapped[str] = mapped_column(String, primary_key=True)
    routine_id: Mapped[str] = mapped_column(ForeignKey("routines.routine_id"))
    run_id: Mapped[str | None] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)
    detail: Mapped[dict | None] = mapped_column(JSON)
    at: Mapped[datetime] = mapped_column(UtcTime)


# --- Runs: S4 to S7 -------------------------------------------------------------------


class OpenRun(Base):
    __tablename__ = "open_runs"
    __table_args__ = (
        one_of("trigger", Trigger),
        one_of("action", DeviceAction),
        one_of("decision", Decision),
        one_of("risk", Risk),
        one_of("phase", ["waiting_answer", "running"]),
    )
    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    trigger: Mapped[str] = mapped_column(String)
    routine_id: Mapped[str | None] = mapped_column(ForeignKey("routines.routine_id"))
    device_id: Mapped[str] = mapped_column(ForeignKey("devices.device_id"))
    mapping_id: Mapped[str] = mapped_column(ForeignKey("mappings.mapping_id"))
    action: Mapped[str] = mapped_column(String)
    decision: Mapped[str] = mapped_column(String)
    confidence: Mapped[float] = mapped_column(Float)
    risk: Mapped[str] = mapped_column(String)
    phase: Mapped[str] = mapped_column(String)
    started_at: Mapped[datetime] = mapped_column(UtcTime)
    dedup_key: Mapped[str] = mapped_column(String, index=True)


class Run(Base):
    """The log. Append-only (L-02): a trigger refuses UPDATE and DELETE."""

    __tablename__ = "runs"
    __table_args__ = (
        one_of("trigger", Trigger),
        one_of("decision", Decision),
        one_of("channel", Channel, nullable=True),
        one_of("result", RunResult),
        Index("ix_runs_started_at", "started_at"),
    )
    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(UtcTime)
    ended_at: Mapped[datetime] = mapped_column(UtcTime)
    trigger: Mapped[str] = mapped_column(String)
    routine_id: Mapped[str | None] = mapped_column(String)
    device_id: Mapped[str | None] = mapped_column(String)
    mapping_id: Mapped[str | None] = mapped_column(String)
    decision: Mapped[str] = mapped_column(String)
    channel: Mapped[str | None] = mapped_column(String)
    result: Mapped[str] = mapped_column(String)
    last_crop_id: Mapped[str | None] = mapped_column(String)
    entry: Mapped[dict] = mapped_column(JSON)  # the whole run entry of execution-log.md


APPEND_ONLY_TRIGGERS = [
    DDL(
        "CREATE TRIGGER runs_no_update BEFORE UPDATE ON runs "
        "BEGIN SELECT RAISE(ABORT, 'runs is append-only (L-02)'); END"
    ),
    DDL(
        "CREATE TRIGGER runs_no_delete BEFORE DELETE ON runs "
        "BEGIN SELECT RAISE(ABORT, 'runs is append-only (L-02)'); END"
    ),
]
for ddl in APPEND_ONLY_TRIGGERS:
    event.listen(Run.__table__, "after_create", ddl.execute_if(dialect="sqlite"))


SITUATION_STATUSES = [
    "received",
    "planning",
    "waiting_for_answer",
    "running",
    "done",
    "needs_attention",
    "suggested",
    "dismissed",
    "cancelled",
]


class Situation(Base):
    """One trigger and what the hub planned and did for it (wiki: API > situations). `plan` holds one
    item per device: decision, method, wireless result, run and the resident's choice after a failure."""

    __tablename__ = "situations"
    __table_args__ = (
        one_of("trigger", Trigger),
        one_of("status", SITUATION_STATUSES),
        Index("ix_situations_received_at", "received_at"),
    )
    situation_id: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String)
    trigger: Mapped[str] = mapped_column(String)
    summary: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String)
    plan: Mapped[list] = mapped_column(JSON)
    received_at: Mapped[datetime] = mapped_column(UtcTime)
    updated_at: Mapped[datetime] = mapped_column(UtcTime)


# --- Other ------------------------------------------------------------------------------


class AppState(Base):
    __tablename__ = "app_state"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSON)


class Session(Base):
    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)  # SHA-256 of the cookie value
    created_at: Mapped[datetime] = mapped_column(UtcTime)
    expires_at: Mapped[datetime] = mapped_column(UtcTime, index=True)
