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
# safe to run on a schema that already has it (IF NOT EXISTS).
_ADDITIVE_MIGRATIONS = (
    "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS verified_at TIMESTAMPTZ",
    "ALTER TABLE behavior_data ADD COLUMN IF NOT EXISTS payload_hash VARCHAR(64)",
    "ALTER TABLE behavior_data ADD COLUMN IF NOT EXISTS newest_event_at BIGINT",
    "CREATE INDEX IF NOT EXISTS ix_behavior_data_payload_hash ON behavior_data (payload_hash)",
)


async def get_db():
    async with get_sessionmaker()() as session:
        yield session
