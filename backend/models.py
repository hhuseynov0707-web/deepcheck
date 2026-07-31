import uuid
from datetime import datetime

from sqlalchemy import (
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

    risk_score: Mapped[float] = mapped_column(Float, default=0.0)

    session: Mapped["Session"] = relationship(back_populates="behavior_data")
