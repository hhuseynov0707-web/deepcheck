import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base

# Last line of defence against a non-finite risk score reaching storage.
# Postgres orders NaN as greater than every other float, so `NaN >= 0` is true
# but `NaN <= 100` is false -- BETWEEN therefore rejects it, as does any score
# outside the documented 0-100 scale. Note create_all() only applies this to
# tables it creates: an existing deployment needs the constraint added by hand
# (or via a migration, see the Alembic item in the audit).
_RISK_SCORE_RANGE = "risk_score BETWEEN 0 AND 100"


class Session(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        CheckConstraint(_RISK_SCORE_RANGE, name="ck_sessions_risk_score_range"),
        # /api/sessions orders every row by last_seen_at on each 3s dashboard poll.
        Index("ix_sessions_last_seen_at", "last_seen_at"),
    )

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    label: Mapped[str] = mapped_column(String, default="Gerçek Kullanıcı")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    shap_explanation: Mapped[dict] = mapped_column(JSON, default=list)
    response_time_ms: Mapped[float] = mapped_column(Float, default=0.0)
    # Set by POST /api/demo/verify when the step-up code is accepted. The
    # decision logic upgrades a "verify" action to "allow" while this is
    # fresh -- never a "block". Lives on the server so the browser cannot
    # claim to have verified.
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    behavior_data: Mapped[list["BehaviorData"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class BehaviorData(Base):
    __tablename__ = "behavior_data"
    __table_args__ = (
        CheckConstraint(_RISK_SCORE_RANGE, name="ck_behavior_data_risk_score_range"),
        # Every /api/analyze runs `WHERE session_id = ? ORDER BY created_at DESC
        # LIMIT 4` for median smoothing, and /api/score/{id} reads the same rows
        # ordered ascending. Postgres does not auto-index foreign keys, so both
        # were sequential scans over a table growing ~0.5 rows/second/user.
        Index("ix_behavior_data_session_created", "session_id", "created_at"),
        # Global lookup for replay detection: the same recording posted under
        # any session hashes to the same value (timestamps are rebased before
        # hashing, so shifting a recording's clock does not change it).
        # UNIQUE, not just indexed. The duplicate check in /api/analyze is a
        # read before a write, so two identical flushes posted concurrently
        # both pass it before either commits -- and three of them would
        # satisfy the evidence rule from a single captured window. Postgres
        # is the only place that race can actually be settled; the handler
        # turns the resulting IntegrityError into the same 422 the check
        # returns. NULLs are exempt, so rows predating the column are fine.
        Index("ix_behavior_data_payload_hash", "payload_hash", unique=True),
        Index("ix_behavior_data_bucket_created", "behavior_bucket", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String, ForeignKey("sessions.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    mouse_trajectory: Mapped[dict] = mapped_column(JSON, default=list)
    click_timing: Mapped[dict] = mapped_column(JSON, default=list)
    scroll_rhythm: Mapped[dict] = mapped_column(JSON, default=list)
    hesitation_intervals: Mapped[dict] = mapped_column(JSON, default=list)
    focus_changes: Mapped[dict] = mapped_column(JSON, default=list)
    key_events: Mapped[dict] = mapped_column(JSON, default=list)

    scroll_hizi_varyansi: Mapped[float] = mapped_column(Float, default=0.0)
    tereddut_skoru: Mapped[float] = mapped_column(Float, default=0.0)
    etkilesim_entropisi: Mapped[float] = mapped_column(Float, default=0.0)
    ivme_degisimi: Mapped[float] = mapped_column(Float, default=0.0)
    tiklama_yogunlugu: Mapped[float] = mapped_column(Float, default=0.0)
    odak_degisimi: Mapped[float] = mapped_column(Float, default=0.0)

    # Structural / cross-channel features (see lstm_model.FEATURE_NAMES).
    hiz_otokorelasyonu: Mapped[float] = mapped_column(Float, default=0.0)
    yon_tutarliligi: Mapped[float] = mapped_column(Float, default=0.0)
    zaman_kuantasyonu: Mapped[float] = mapped_column(Float, default=0.0)
    duraklama_dagilimi: Mapped[float] = mapped_column(Float, default=0.0)
    tiklama_oncesi_hareket: Mapped[float] = mapped_column(Float, default=0.0)
    kanal_gecis_gecikmesi: Mapped[float] = mapped_column(Float, default=0.0)
    risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    # Replay protection (see main.py): a clock-independent fingerprint of the
    # telemetry, and the newest event timestamp in it so the next flush can
    # be required to move forward in time.
    # Coarse behavioural signature (see scorer.behavior_bucket). Indexed
    # because the decision path counts distinct sessions sharing a bucket
    # inside a short window, on every checkout.
    behavior_bucket: Mapped[str | None] = mapped_column(String(32), nullable=True)
    payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    newest_event_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Provenance signals reported by the SDK: how many events arrived with
    # isTrusted false, whether navigator.webdriver was set, and the pointer
    # type mix. RECORDED ONLY -- nothing here reaches the model or the risk
    # score. They are being collected so that, once the real-session
    # evaluation set exists, their value as features can be MEASURED rather
    # than assumed. A signal the client reports about itself is also a signal
    # the client can lie about, which is exactly why it needs measuring
    # before it is trusted.
    client_signals: Mapped[dict] = mapped_column(JSON, default=dict)
    # True once the retention sweep has blanked this row's raw telemetry. An
    # explicit flag rather than "is the JSON empty?": a keyboard-only flush
    # legitimately has an empty mouse_trajectory, so emptiness cannot
    # distinguish "never had data" from "already purged", and Postgres has no
    # equality operator for the json type to test it with anyway.
    raw_purged: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    session: Mapped["Session"] = relationship(back_populates="behavior_data")
