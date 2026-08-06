from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from app.config import DATABASE_URL

try:
    engine = create_async_engine(DATABASE_URL, echo=False, future=True)
except ModuleNotFoundError as exc:
    raise RuntimeError(
        'Database driver missing or DATABASE_URL uses the wrong dialect. '
        'Ensure DATABASE_URL is set to a valid postgres asyncpg URL (postgresql+asyncpg://...) '
        'and that asyncpg is installed in requirements.'
    ) from exc

async_session = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Declarative base class for all ORM models."""
    pass


async def get_db():
    """FastAPI dependency that yields an async database session."""
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
