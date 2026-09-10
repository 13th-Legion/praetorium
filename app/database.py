"""Database engine and session management."""

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from config import get_settings

settings = get_settings()

engine = create_async_engine(settings.database_url, echo=settings.debug)

# Consumers outside this module must resolve this through the module —
# `from app import database` ... `database.async_session()` — and never
# `from app.database import async_session`. A by-name import binds a private
# copy at import time, so rebinding this attribute (the test suite points it at
# a throwaway sessionmaker) would not reach that copy and the module would keep
# opening connections against the real DATABASE_URL host.
# Function-local by-name imports are fine: they re-resolve on every call.
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    """Base class for all ORM models."""
    pass


async def get_db() -> AsyncSession:
    """Dependency: yield an async DB session."""
    async with async_session() as session:
        yield session
