"""Создание async-движка и фабрики сессий SQLAlchemy 2.x."""

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from config import settings


class Base(DeclarativeBase):
    """Базовый класс для всех ORM-моделей."""

    pass


engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,  # проверка живости соединения (актуально для PostgreSQL)
)

session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_models() -> None:
    """Создаёт таблицы напрямую (используется при первом запуске без Alembic)."""
    from database import models  # noqa: F401  регистрация моделей

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
