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
    SmallInteger,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
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
        # Partial: only a small minority of sessions ever carry a customer
        # reference (guest checkout never does), so indexing the NULLs would
        # be most of the index and none of the lookups.
        Index(
            "ix_sessions_profile_id",
            "profile_id",
            postgresql_where=text("profile_id IS NOT NULL"),
        ),
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
    # server clock - client_sent_at, in ms, from the first stored flush that
    # carried client_sent_at. Later flushes must stay within
    # main.MAX_OFFSET_DRIFT_MS of it: a client's clock may be wrong, but it
    # must be consistently wrong for the whole session (see main.py).
    clock_offset_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # The customer profile this session was attributed to, if any. Plain
    # VARCHAR, deliberately NO foreign key: sessions are deleted after 24h and
    # profiles live for months, so a FK would let a profile delete abort the
    # retention sweep's single transaction -- and _retention_loop swallows the
    # exception into one log line, so the visible symptom would be raw
    # telemetry quietly never being blanked again for the whole database.
    profile_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Set by the single atomic insert that learns this session. A session
    # teaches a profile at most once, and that is enforced by a unique index
    # rather than by this flag; the flag is what lets the decision path skip
    # the insert entirely on later flushes.
    profile_learned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # True when no person produced this session: backend/demo_seed.py
    # --simulate drove it through the real HTTP path with simulator telemetry.
    # Set by that tool, never by the API -- a client cannot declare itself
    # synthetic, and a flag it could set would be a flag an attacker sets to be
    # left out of an evaluation. Synthetic data is allowed in the demo and
    # never in a measurement, and this column is how every script tells the two
    # apart (see is_synthetic on customer_profiles).
    is_synthetic: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )

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
    # Bit i is set when FEATURE_NAMES[i] was genuinely MEASURED in this flush,
    # i.e. scorer.extract_raw() returned a finite value rather than None.
    # Without it nothing downstream can tell a measured 0.3 from a
    # NEUTRAL_DEFAULTS 0.3 -- and a per-customer profile built over defaults
    # measures "how much telemetry did this session produce", not "is this the
    # same person". Nullable because rows written before the column existed
    # genuinely do not know, and profiles.session_vector() drops those rather
    # than guessing.
    measured_mask: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # True once the retention sweep has blanked this row's raw telemetry. An
    # explicit flag rather than "is the JSON empty?": a keyboard-only flush
    # legitimately has an empty mouse_trajectory, so emptiness cannot
    # distinguish "never had data" from "already purged", and Postgres has no
    # equality operator for the json type to test it with anyway.
    raw_purged: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    session: Mapped["Session"] = relationship(back_populates="behavior_data")


# --- Per-customer behavioural profile ----------------------------------------
#
# A persistent, identity-keyed record of how a named person moves a pointer and
# times their keystrokes, kept in order to confirm it is the same person, is
# BIOMETRIC DATA: GDPR Art. 4(14) covers behavioural characteristics and KVKK
# Art. 6 lists biometric data as ozel nitelikli. Pseudonymisation does not
# change that -- Recital 26 says pseudonymous data is still personal data, so
# "we hash the reference" is not a lawful basis. Three consequences are built
# into the schema below rather than left to the application: a profile row
# exists only with a recorded consent basis, it can be erased by deleting rows
# (there is no derived summary to unwind), and an objection leaves a tombstone
# so the next decision cannot silently recreate what the customer refused.


class CustomerProfile(Base):
    """One customer at one merchant. Holds no behaviour itself -- the behaviour
    is in CustomerProfileVector rows -- only the lawful-basis record and the
    counters that bound how often this person may be challenged."""

    __tablename__ = "customer_profiles"
    __table_args__ = (
        # The retention sweep selects idle profiles by this column.
        Index("ix_customer_profiles_last_seen_at", "last_seen_at"),
    )

    # HMAC-SHA256 hex of (merchant_id, customer_ref) under DEEPCHECK_PROFILE_KEY.
    # The raw reference is never stored anywhere in this database.
    profile_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    merchant_id: Mapped[str] = mapped_column(String(32), nullable=False)
    # Rotating the profile key re-derives every id and orphans every profile at
    # once. The column exists so a future rotation can dual-write for a window
    # instead; on a version mismatch a profile is treated as immature and never
    # compared.
    key_version: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    feature_schema_version: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # FALSE by default and never flipped by the decision path: only the
    # merchant-authenticated consent endpoint may turn profiling on. No consent
    # means no row, which means no personal data and no escalation -- and a
    # customer who refuses is never challenged for refusing.
    profiling_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # explicit_consent | contract_necessity | demo | objected | none.
    # Legitimate interest is NOT available for Art. 9 / KVKK Art. 6 data, so
    # there is no value here that means "we decided it was fine".
    consent_basis: Mapped[str] = mapped_column(String(32), nullable=False, default="none")
    consent_recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    erased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Demo-page profiles live in the reserved "demo" merchant namespace and are
    # excluded from every reported measurement, by construction.
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # A SYNTHETIC demo customer: a simulator identity seeded by
    # backend/demo_seed.py so the jury prototype has customers with a history,
    # because the team has no customer base. No person exists behind it and no
    # person consented; its consent_basis is "demo". Always is_demo as well, but
    # the reverse does not hold: a demo profile created by a person typing a
    # reference on the demo page is real behaviour in a demo namespace. Every
    # measurement excludes both, and every screen that shows a synthetic
    # profile must say so.
    is_synthetic: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    # Per-profile challenge budget (profiles.PROFILE_MAX_ESCALATIONS per
    # PROFILE_BUDGET_WINDOW_DAYS). A hard ceiling bounds the discrimination risk
    # -- inferred tremor, assistive input, a shared device -- far more reliably
    # than a statistic does.
    escalation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    escalation_window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Drives self-healing: challenges that the customer PASSED, in a row. Three
    # of those say the profile is wrong, not that the customer is a fraudster.
    consecutive_passed_escalations: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    stats_rebuilt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CustomerProfileVector(Base):
    """The bounded reference buffer -- and the ENTIRE statistic.

    These rows are not a convenience copy of a summary. There is no mean, no
    variance and no Welford state anywhere in this design: centre and scale are
    recomputed from these rows on every read (profiles.feature_stats). That is
    also what makes them defensible under Art. 5(1)(c): they ARE the statistic,
    and they are the evidence a human reviewer needs to answer an Art. 22(3)
    contest -- with a decision's evidence deleted at 24h along with the
    telemetry, a customer who complains on Tuesday about a Friday challenge
    could otherwise not be answered at all.

    No raw telemetry is ever written here. A vector is twelve normalised
    numbers, one session.
    """

    __tablename__ = "customer_profile_vectors"
    __table_args__ = (
        # THIS is the learn-once mechanism. INSERT ... ON CONFLICT DO NOTHING
        # against a unique index makes a double learn physically impossible,
        # which is stronger and far simpler than a read-check (two workers both
        # pass it) or a compare-and-swap.
        Index("ix_profile_vectors_profile_session", "profile_id", "session_id", unique=True),
        # Every comparison reads one profile's vectors for ONE modality,
        # newest first.
        Index("ix_profile_vectors_profile_modality", "profile_id", "modality", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # No foreign key, for the same reason sessions.profile_id has none.
    profile_id: Mapped[str] = mapped_column(String(64), nullable=False)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # touch | mouse | keyboard. A profile is compared only within one modality,
    # with no pooled fallback: touch produces no mousemove at all, so pooling
    # gives a profile that is either blind or hostile to the customer's
    # minority device.
    modality: Mapped[str] = mapped_column(String(16), nullable=False)
    feature_schema_version: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)

    # JSONB, not JSON: Postgres has no equality operator for the json type, so
    # a plain JSON column cannot be compared or deduplicated in SQL at all
    # (this repo has already been bitten by that once).
    # {feature_name: float | null} -- null means "not measured in this session",
    # which is NOT the same as zero and must never be read as zero.
    vec: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # Per-feature within-session MAD. RECORDED, NOT SCORED: the mid-session
    # handover it would catch is already caught per-flush, measured 185/185, by
    # scorer.LEVEL_SHIFT_POINTS.
    disp: Mapped[dict] = mapped_column(JSONB, nullable=False)
    flush_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)

    # True when this session was learned only because a step-up rescued a
    # profile escalation. A probation vector is STORED (evidence for the SOC
    # panel and for human review) but is not a reference: the statistic and the
    # maturity count never see it until it is promoted -- by POST /api/outcome
    # "settled", or by the self-healing rebuild (profiles.promotable_run). One
    # passed step-up used to buy an attacker a reference that shielded his later
    # sessions (docs/profile-evaluation.md section 7). At most
    # profiles.PROFILE_PROBATION_MAX per (profile, modality), in slots of their
    # own, so they can never evict a reference.
    probation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # pending | settled | disputed. Learning happens on an AUTHORISATION, not a
    # settlement, because a demo has no settlement feed and a layer that never
    # matures cannot be measured -- so an undisputed fraud that was never
    # challenged can occupy a reference slot until it is reported. The buffer
    # bounds that to 1/20 and POST /api/outcome removes it exactly.
    outcome: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    # True when the simulator produced this vector (backend/demo_seed.py), false
    # when a person's session did -- which can happen inside a synthetic
    # profile, since the decision path learns whatever it is allowed to learn.
    # Kept per vector for exactly that reason: the profile flag alone cannot
    # say which of its references were a person.
    is_synthetic: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )


class DecisionAudit(Base):
    """Why a decision was made, kept on its own 90-day clock.

    Independent of the telemetry sweep on purpose: today the reason is returned
    to the caller and forgotten while the evidence is deleted in 24 hours. KVKK
    11(1)(g) and GDPR Art. 22(3) both assume a human can review a contested
    automated decision, and that is impossible without a record that outlives
    the telemetry.

    A row is written when the action is not "allow", or when the profile layer
    produced any opinion at all (including shadow mode and including
    abstentions). Not on plain allows, so the hot path stays a read.
    """

    __tablename__ = "decision_audit"
    __table_args__ = (
        Index("ix_decision_audit_decided_at", "decided_at"),
        Index("ix_decision_audit_session", "session_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # NULLed on erasure -- the audit row survives, the link to the person does
    # not. No foreign key, so a profile delete can never abort a sweep.
    profile_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    merchant_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    action: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # The INTERNAL reason, which the scored client is never told.
    reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # What the client was actually told: four internal reasons collapse to one
    # "step_up", because naming the check that convicted a caller is a tuning
    # signal -- submit, read the reason, adjust, repeat.
    public_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    profile_state: Mapped[str | None] = mapped_column(String(24), nullable=True)
    deviation: Mapped[float | None] = mapped_column(Float, nullable=True)
    p_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    # The low tail: a session implausibly CLOSE to the stored centre is what a
    # replay looks like. Recorded, not enforced -- enforcing it needs the lab
    # replay measurement that does not exist yet.
    p_value_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    top_features: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    modality: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # The references the statistic was computed over -- probation vectors
    # excluded -- and, separately, the probation vectors stored for the same
    # modality at decision time, which it did not use. Null when the decision
    # never read the vectors (no profile, suppressed, version mismatch, thin
    # session).
    reference_n: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    probation_n: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    feature_schema_version: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    # True when the layer had an opinion but PROFILE_ESCALATION was off, so the
    # decision was not affected. This is how the layer gets measured before it
    # is allowed to act.
    shadow: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Transaction context: RECORDED, NOT ENFORCED in v1. Gating on a band nobody
    # has measured is inventing a threshold, and this is the first thing to
    # measure when real traffic exists -- challenging a 50 TL top-up on a
    # behavioural wobble is pure cost, challenging a 15.000 TL transfer to a
    # first-time payee is the product.
    amount_band: Mapped[str | None] = mapped_column(String(8), nullable=True)
    new_beneficiary: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # True when synthetic demo data took part in this decision: the session was
    # simulated (sessions.is_synthetic), or the profile it was compared against
    # is a seeded synthetic customer (customer_profiles.is_synthetic). Stored
    # on the row rather than looked up later, because the row outlives both:
    # the session is deleted at 24h and demo_seed.py --reset deletes the
    # profile, and a decision that was made against a simulator must still say
    # so to the SOC panel and to a reviewer 90 days on.
    is_synthetic: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )


class ProfileAccessAudit(Base):
    """Who read a customer profile, and when.

    Kurul 2018/10 expects logged, restricted access to ozel nitelikli veri. This
    also resolves the tension with "never log profile_id": accountability
    logging for special-category access belongs in an access-controlled table,
    and stdout stays clean.
    """

    __tablename__ = "profile_access_audit"
    __table_args__ = (Index("ix_profile_access_audit_accessed_at", "accessed_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    accessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    operator_id: Mapped[str] = mapped_column(String(32), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(64), nullable=False)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    profile_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
