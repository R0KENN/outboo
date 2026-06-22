"""Действия модерации: бан, мут, кик, варн (раздел 4.1 ТЗ).

Вся бизнес-логика наказаний собрана здесь. Хендлеры только вызывают
эти функции. Каждое действие пишется в moderation_log.
"""

import json
import logging
from datetime import UTC, datetime, timedelta

from aiogram import Bot
from aiogram.types import ChatPermissions
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.crud import get_or_create_chat_settings
from database.models import ModerationLog, Warn

logger = logging.getLogger(__name__)


# Какие действия отражаем в статистике и под какой метрикой
_STAT_METRIC = {
    "ban": "bans",
    "mute": "mutes",
    "warn": "warns",
}


async def log_action(
    session: AsyncSession,
    chat_id: int,
    action: str,
    actor_id: int,
    target_id: int,
    reason: str = "",
) -> None:
    """Записывает модераторское действие в журнал и обновляет статистику."""
    entry = ModerationLog(
        chat_id=chat_id,
        action=action,
        actor_id=actor_id,
        target_id=target_id,
        reason=reason,
    )
    session.add(entry)
    await session.commit()

    # Счётчик статистики (только для ключевых действий)
    metric = _STAT_METRIC.get(action)
    if metric:
        from database import crud

        await crud.bump_stat(session, chat_id, metric)


async def ban_user(
    bot: Bot,
    session: AsyncSession,
    chat_id: int,
    user_id: int,
    actor_id: int,
    reason: str = "",
) -> None:
    """Банит (удаляет и блокирует) участника."""
    await bot.ban_chat_member(chat_id, user_id)
    await log_action(session, chat_id, "ban", actor_id, user_id, reason)


async def unban_user(
    bot: Bot,
    session: AsyncSession,
    chat_id: int,
    user_id: int,
    actor_id: int,
) -> None:
    """Снимает блокировку участника."""
    await bot.unban_chat_member(chat_id, user_id, only_if_banned=True)
    await log_action(session, chat_id, "unban", actor_id, user_id)


async def kick_user(
    bot: Bot,
    session: AsyncSession,
    chat_id: int,
    user_id: int,
    actor_id: int,
    reason: str = "",
) -> None:
    """Кик: бан с немедленным разбаном, чтобы участник мог вернуться."""
    await bot.ban_chat_member(chat_id, user_id)
    await bot.unban_chat_member(chat_id, user_id, only_if_banned=True)
    await log_action(session, chat_id, "kick", actor_id, user_id, reason)


async def mute_user(
    bot: Bot,
    session: AsyncSession,
    chat_id: int,
    user_id: int,
    actor_id: int,
    seconds: int,
    reason: str = "",
) -> None:
    """Временно лишает участника права писать.

    Telegram считает ограничение «навсегда», если оно короче 30 секунд,
    поэтому поднимаем нижнюю границу до 31 секунды. Гасим все права на отправку,
    иначе замученный мог бы слать медиа/стикеры/опросы.
    """
    seconds = max(31, seconds)
    until = datetime.now(UTC) + timedelta(seconds=seconds)
    await bot.restrict_chat_member(
        chat_id,
        user_id,
        permissions=ChatPermissions(
            can_send_messages=False,
            can_send_audios=False,
            can_send_documents=False,
            can_send_photos=False,
            can_send_videos=False,
            can_send_video_notes=False,
            can_send_voice_notes=False,
            can_send_polls=False,
            can_send_other_messages=False,
            can_add_web_page_previews=False,
        ),
        use_independent_chat_permissions=True,
        until_date=until,
    )
    await log_action(session, chat_id, "mute", actor_id, user_id, reason)

async def unmute_user(
    bot: Bot,
    session: AsyncSession,
    chat_id: int,
    user_id: int,
    actor_id: int,
    reason: str = "",
) -> None:
    """Снимает мут: возвращает участнику полный набор прав на отправку."""
    await bot.restrict_chat_member(
        chat_id,
        user_id,
        permissions=ChatPermissions(
            can_send_messages=True,
            can_send_audios=True,
            can_send_documents=True,
            can_send_photos=True,
            can_send_videos=True,
            can_send_video_notes=True,
            can_send_voice_notes=True,
            can_send_polls=True,
            can_send_other_messages=True,
            can_add_web_page_previews=True,
        ),
        use_independent_chat_permissions=True,
    )
    await log_action(session, chat_id, "unmute", actor_id, user_id, reason)

async def add_warn(
    bot: Bot,
    session: AsyncSession,
    chat_id: int,
    user_id: int,
    actor_id: int,
    reason: str = "",
) -> tuple[int, int, bool]:
    """Выдаёт варн. Возвращает (текущее_число, порог, сработало_ли_действие).

    При достижении порога автоматически применяет действие из настроек
    (мут или бан) и сбрасывает счётчик.
    """
    chat_settings = await get_or_create_chat_settings(session, chat_id)

    stmt = select(Warn).where(Warn.chat_id == chat_id, Warn.user_id == user_id)
    warn = (await session.execute(stmt)).scalar_one_or_none()
    if warn is None:
        warn = Warn(chat_id=chat_id, user_id=user_id, count=0, history="[]")
        session.add(warn)

    warn.count += 1
    # Дописываем запись в историю выдачи
    try:
        history = json.loads(warn.history or "[]")
    except (ValueError, TypeError):
        history = []
    history.append(
        {
            "actor": actor_id,
            "reason": reason,
            "at": datetime.now(UTC).isoformat(),
        }
    )
    warn.history = json.dumps(history, ensure_ascii=False)

    await log_action(session, chat_id, "warn", actor_id, user_id, reason)

    limit = chat_settings.warn_limit
    triggered = False
    if warn.count >= limit:
        triggered = True
        if chat_settings.warn_action == "ban":
            await ban_user(bot, session, chat_id, user_id, actor_id, "warn limit")
        else:
            await mute_user(
                bot,
                session,
                chat_id,
                user_id,
                actor_id,
                chat_settings.flood_mute_seconds,
                "warn limit",
            )
        warn.count = 0  # сброс после автодействия

    await session.commit()
    return warn.count, limit, triggered


async def remove_warn(
    session: AsyncSession,
    chat_id: int,
    user_id: int,
) -> int:
    """Снимает один варн. Возвращает оставшееся количество."""
    stmt = select(Warn).where(Warn.chat_id == chat_id, Warn.user_id == user_id)
    warn = (await session.execute(stmt)).scalar_one_or_none()
    if warn is None or warn.count == 0:
        return 0
    warn.count -= 1
    await session.commit()
    return warn.count


async def get_warns(
    session: AsyncSession,
    chat_id: int,
    user_id: int,
) -> int:
    """Возвращает текущее число варнов участника."""
    stmt = select(Warn).where(Warn.chat_id == chat_id, Warn.user_id == user_id)
    warn = (await session.execute(stmt)).scalar_one_or_none()
    return warn.count if warn else 0


async def reset_warns(
    session: AsyncSession,
    chat_id: int,
    user_id: int,
) -> bool:
    """Полностью обнуляет счётчик варнов участника. True, если запись была."""
    stmt = select(Warn).where(Warn.chat_id == chat_id, Warn.user_id == user_id)
    warn = (await session.execute(stmt)).scalar_one_or_none()
    if warn is None or warn.count == 0:
        return False
    warn.count = 0
    warn.history = "[]"
    await session.commit()
    return True
