"""
AI Stock Kundli — Database Engine & Session Management
Async & Sync SQLAlchemy setups with PostgreSQL.
"""

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from app.core.config import get_settings

settings = get_settings()

# ── Async Setup (Used for FastAPI endpoints) ──────────────────
# asyncpg strictly requires `ssl=require` instead of `sslmode=require`
async_url = settings.DATABASE_URL.replace("sslmode=require", "ssl=require").replace("sslmode=true", "ssl=require")

engine = create_async_engine(
    async_url,
    echo=settings.DEBUG,
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models."""
    pass


async def get_db() -> AsyncSession:
    """FastAPI dependency — yields an async database session."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# ── Sync Setup (Used for Celery background tasks & seeders) ──
sync_url = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
# Fix SSL param mismatch between asyncpg (ssl=require) and psycopg2 (sslmode=require)
sync_url = sync_url.replace("ssl=require", "sslmode=require").replace("ssl=true", "sslmode=require")

sync_engine = create_engine(
    sync_url,
    echo=settings.DEBUG,
    pool_size=10,
    max_overflow=5,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=sync_engine,
)
