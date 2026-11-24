from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Session

from app.core.config import get_settings


class Base(DeclarativeBase):
    """Shared declarative base for all SQLAlchemy models."""


settings = get_settings()
_is_sqlite = settings.database_url.startswith("sqlite")
_engine_kwargs = (
    {
        "echo": False,
        "connect_args": {"timeout": 20},
    }
    if _is_sqlite
    else {
        "echo": False,
        "pool_pre_ping": True,
        "pool_size": 10,
        "max_overflow": 20,
    }
)
engine = create_async_engine(settings.database_url, **_engine_kwargs)
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


@event.listens_for(engine.sync_engine, "connect")
def _configure_sqlite_wal(dbapi_connection, _connection_record) -> None:
    """Enable SQLite WAL journal mode and busy timeout so concurrent background workers do not lock."""
    if not _is_sqlite:
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=15000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


@event.listens_for(Session, "after_begin")
def _reapply_rls_on_tx_begin(session: Session, _transaction, connection) -> None:
    """Re-bind transaction-local PostgreSQL RLS GUCs across subsequent commits in a request."""
    if connection.dialect.name != "postgresql":
        return
    if "rls_bypass" not in session.info and "rls_user_id" not in session.info:
        return
    bypass = bool(session.info.get("rls_bypass", False))
    user_id = session.info.get("rls_user_id")
    connection.execute(text("SELECT set_config('app.rls_bypass', :b, true)"), {"b": "on" if bypass else "off"})
    if user_id:
        connection.execute(text("SELECT set_config('app.current_user_id', :uid, true)"), {"uid": user_id})


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async database session for FastAPI endpoints."""
    async with async_session_factory() as session:
        yield session


async def apply_rls_context(db: AsyncSession, user_id: str | None = None, bypass: bool = False) -> None:
    """Bind PostgreSQL Row-Level Security local session GUCs when running on PostgreSQL."""
    db.info["rls_bypass"] = bypass
    db.info["rls_user_id"] = user_id or ""
    bind = db.get_bind()
    if bind is None or bind.dialect.name != "postgresql":
        return
    await db.execute(text("SELECT set_config('app.rls_bypass', :b, true)"), {"b": "on" if bypass else "off"})
    await db.execute(text("SELECT set_config('app.current_user_id', :uid, true)"), {"uid": user_id or ""})
