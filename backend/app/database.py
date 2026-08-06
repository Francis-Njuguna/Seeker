"""Async SQLAlchemy engine, session factory and declarative base."""
import os
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.config import settings

# Under pytest each test may run on its own event loop. Pooled asyncpg
# connections are bound to the loop that created them, so reusing them across
# loops raises "attached to a different loop". NullPool sidesteps that by
# opening and closing a connection per checkout.
_TESTING = os.getenv("SEEKER_TESTING") == "1"

engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
    **({"poolclass": NullPool} if _TESTING else {}),
)

SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session
