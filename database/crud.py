"""Слой доступа к данным. Вся работа с БД — только здесь (раздел 3 ТЗ)."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import ChatSettings, Moderator


async def get_or_create_chat_settings(
    session: AsyncSession, chat_id: int
) -> ChatSettings:
    """Возвращает настройки чата, создавая запись со значениями по умолчанию."""
    obj = await session.get(ChatSettings, chat_id)
    if obj is None:
        obj = ChatSettings(chat_id=chat_id)
        session.add(obj)
        await session.commit()
        await session.refresh(obj)
    return obj


async def get_moderator(
    session: AsyncSession, chat_id: int, user_id: int
) -> Moderator | None:
    """Возвращает запись младшего модератора, если он назначен."""
    stmt = select(Moderator).where(
        Moderator.chat_id == chat_id, Moderator.user_id == user_id
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()

from datetime import datetime, timezone
from database.models import MemberJoin


async def record_join(session: AsyncSession, chat_id: int, user_id: int) -> None:
    """Запоминает время входа участника (для карантина)."""
    stmt = select(MemberJoin).where(
        MemberJoin.chat_id == chat_id, MemberJoin.user_id == user_id
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing is None:
        session.add(MemberJoin(chat_id=chat_id, user_id=user_id))
        await session.commit()


async def get_join_time(
    session: AsyncSession, chat_id: int, user_id: int
) -> datetime | None:
    """Возвращает время входа участника или None, если запись не найдена."""
    stmt = select(MemberJoin).where(
        MemberJoin.chat_id == chat_id, MemberJoin.user_id == user_id
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    return row.joined_at if row else None
