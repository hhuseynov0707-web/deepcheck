import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://deepcheck:deepcheck@localhost:5432/deepcheck",
)

# Built on first use rather than at import. create_async_engine() resolves and
# imports the DBAPI driver eagerly, so building it at module scope made
# `import main` fail outright on any machine without asyncpg installed --
# including one running the API's authorization tests, which touch no database
# at all. A security check that cannot be tested without infrastructure is a
# security check that stops being tested.
_engine = None
_sessionmaker = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
    return _engine


def get_sessionmaker():
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _sessionmaker


class Base(DeclarativeBase):
    pass


# Arbitrary but fixed: any constant works as long as every worker uses the
# same one.
_SCHEMA_LOCK_KEY = 728_301


async def init_db() -> None:
    async with get_engine().begin() as conn:
        # entrypoint.sh starts 4 uvicorn workers and each one runs this in its
        # own lifespan, simultaneously. Concurrent CREATE TABLE IF NOT EXISTS
        # is not safe in Postgres -- the existence check and the catalog insert
        # are not atomic, so two workers can both decide to create and the
        # loser dies with a duplicate-key error on pg_type. That surfaces as an
        # intermittent worker crash on startup, i.e. exactly the kind of thing
        # that only shows up in front of an audience. The advisory lock is
        # transaction-scoped and releases on commit.
        await conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _SCHEMA_LOCK_KEY})
        await conn.run_sync(Base.metadata.create_all)
        # create_all never ALTERs an existing table, so a volume created by an
        # earlier version would be missing the columns added since and the
        # first flush would fail with a 500 at request time. Until a real
        # migration tool is adopted, add the known additions idempotently
        # here, at boot, under the same lock.
        for statement in _ADDITIVE_MIGRATIONS:
            await conn.execute(text(statement))


# Columns and indexes added after the first release. Every statement must be
# safe to run on a schema that already has it (IF NOT EXISTS), and the two
# data repairs at the end must be safe to run on data that has already been
# repaired.
_ADDITIVE_MIGRATIONS = (
    "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS verified_at TIMESTAMPTZ",
    "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS clock_offset_ms BIGINT",
    "ALTER TABLE behavior_data ADD COLUMN IF NOT EXISTS payload_hash VARCHAR(64)",
    "ALTER TABLE behavior_data ADD COLUMN IF NOT EXISTS newest_event_at BIGINT",
    "ALTER TABLE behavior_data ADD COLUMN IF NOT EXISTS client_signals JSON",
    "ALTER TABLE behavior_data ADD COLUMN IF NOT EXISTS raw_purged BOOLEAN NOT NULL DEFAULT FALSE",
    # Structural features added alongside the browser-lab work.
    "ALTER TABLE behavior_data ADD COLUMN IF NOT EXISTS hiz_otokorelasyonu DOUBLE PRECISION DEFAULT 0",
    "ALTER TABLE behavior_data ADD COLUMN IF NOT EXISTS yon_tutarliligi DOUBLE PRECISION DEFAULT 0",
    "ALTER TABLE behavior_data ADD COLUMN IF NOT EXISTS zaman_kuantasyonu DOUBLE PRECISION DEFAULT 0",
    "ALTER TABLE behavior_data ADD COLUMN IF NOT EXISTS duraklama_dagilimi DOUBLE PRECISION DEFAULT 0",
    "ALTER TABLE behavior_data ADD COLUMN IF NOT EXISTS tiklama_oncesi_hareket DOUBLE PRECISION DEFAULT 0",
    "ALTER TABLE behavior_data ADD COLUMN IF NOT EXISTS kanal_gecis_gecikmesi DOUBLE PRECISION DEFAULT 0",
    "ALTER TABLE behavior_data ADD COLUMN IF NOT EXISTS behavior_bucket VARCHAR(32)",
    "CREATE INDEX IF NOT EXISTS ix_behavior_data_bucket_created ON behavior_data (behavior_bucket, created_at)",
    # Unique: see the note in models.py. Created concurrently-safe and
    # tolerant of an existing non-unique index of the same name.
    "DROP INDEX IF EXISTS ix_behavior_data_payload_hash",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_behavior_data_payload_hash ON behavior_data (payload_hash)",
    # --- Per-customer behavioural profile ------------------------------------
    # Columns and indexes only. The four new tables are created by create_all
    # above; what needs repeating here is everything create_all will not add to
    # a table that already exists. Every statement in THIS block is
    # metadata-only, so boot stays fast for all four workers queued behind the
    # advisory lock, and there is no ADD CONSTRAINT anywhere -- Postgres has no
    # "ADD CONSTRAINT IF NOT EXISTS", and this design deliberately has no
    # foreign keys to add.
    "ALTER TABLE behavior_data ADD COLUMN IF NOT EXISTS measured_mask INTEGER",
    "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS profile_id VARCHAR(64)",
    "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS profile_learned BOOLEAN NOT NULL DEFAULT FALSE",
    "CREATE INDEX IF NOT EXISTS ix_sessions_profile_id ON sessions (profile_id) WHERE profile_id IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS ix_customer_profiles_last_seen_at ON customer_profiles (last_seen_at)",
    # The unique index IS the learn-once mechanism (see models.py): the
    # ON CONFLICT clause that stops a double learn needs this index to exist,
    # so on an upgraded database it must be created here and not only by
    # create_all.
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_profile_vectors_profile_session ON customer_profile_vectors (profile_id, session_id)",
    "CREATE INDEX IF NOT EXISTS ix_profile_vectors_profile_modality ON customer_profile_vectors (profile_id, modality, created_at)",
    "CREATE INDEX IF NOT EXISTS ix_decision_audit_decided_at ON decision_audit (decided_at)",
    "CREATE INDEX IF NOT EXISTS ix_decision_audit_session ON decision_audit (session_id)",
    "CREATE INDEX IF NOT EXISTS ix_profile_access_audit_accessed_at ON profile_access_audit (accessed_at)",
    # Probation vectors stopped being references, so the audit row reports
    # them beside reference_n instead of inside it.
    "ALTER TABLE decision_audit ADD COLUMN IF NOT EXISTS probation_n SMALLINT",
    # --- Synthetic demo data (backend/demo_seed.py) ----------------------------
    # The jury prototype runs on synthetic customers; these flags are how every
    # screen labels them and every measurement leaves them out. DEFAULT FALSE
    # with NOT NULL is metadata-only in Postgres 11+, so existing rows are
    # "not synthetic" without a table rewrite -- which is true of every row
    # written before the seed existed.
    "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS is_synthetic BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE customer_profiles ADD COLUMN IF NOT EXISTS is_synthetic BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE customer_profile_vectors ADD COLUMN IF NOT EXISTS is_synthetic BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE decision_audit ADD COLUMN IF NOT EXISTS is_synthetic BOOLEAN NOT NULL DEFAULT FALSE",
    # The compared session vector on a deviating decision, for the human
    # review once the flushes are gone (models.DecisionAudit.candidate_vec).
    "ALTER TABLE decision_audit ADD COLUMN IF NOT EXISTS candidate_vec JSONB",
    # The only statements here that touch data rather than the schema. The two
    # JSONB columns used to store Python None as the JSON value `null` instead
    # of SQL NULL (see models.DecisionAudit), so `IS NOT NULL` matched every
    # row ever written -- including rows an erasure had already cleared. New
    # rows are correct; these fix the ones already in the table. They are
    # idempotent: after the first boot they match nothing. They are a
    # sequential scan of decision_audit, which is bounded by
    # DECISION_AUDIT_RETENTION_DAYS, and they run under the same boot lock as
    # the rest, so one worker does the work and the others wait.
    "UPDATE decision_audit SET candidate_vec = NULL WHERE jsonb_typeof(candidate_vec) = 'null'",
    "UPDATE decision_audit SET top_features = NULL WHERE jsonb_typeof(top_features) = 'null'",
)


async def get_db():
    async with get_sessionmaker()() as session:
        yield session
