"""Middleware определения прав пользователя (раздел 6, безопасность)."""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.enums import ChatMemberStatus
from aiogram.types import Message

from config import settings
from database.crud import get_moderator
from database.engine import session_factory

logger = logging.getLogger(__name__)

import time

# Кеш статуса админа: (chat_id, user_id) -> (is_admin, expires_at)
_admin_cache: dict[tuple[int, int], tuple[bool, float]] = {}
_ADMIN_TTL = 300  # секунд


def _cached_admin(chat_id: int, user_id: int) -> bool | None:
    item = _admin_cache.get((chat_id, user_id))
    if item and item[1] > time.monotonic():
        return item[0]
    return None


def _store_admin(chat_id: int, user_id: int, value: bool) -> None:
    _admin_cache[(chat_id, user_id)] = (value, time.monotonic() + _ADMIN_TTL)


class AdminCheckMiddleware(BaseMiddleware):
    """Добавляет в data ключи is_admin и is_moderator для текущего пользователя."""

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        is_admin = False
        is_moderator = False

        if event.from_user and event.from_user.id in settings.admin_ids:
            # Владельцы бота — администраторы в любом контексте, включая личку
            is_admin = True
        elif event.chat and event.from_user and event.chat.type != "private":
            cached = _cached_admin(event.chat.id, event.from_user.id)
            if cached is not None:
                is_admin = cached
            else:
                try:
                    member = await event.bot.get_chat_member(event.chat.id, event.from_user.id)
                    is_admin = member.status in (
                        ChatMemberStatus.ADMINISTRATOR,
                        ChatMemberStatus.CREATOR,
                    )
                except Exception as e:
                    logger.warning(
                        "Не удалось получить статус %s в чате %s: %s",
                        event.from_user.id,
                        event.chat.id,
                        e,
                    )
                    is_admin = False
                _store_admin(event.chat.id, event.from_user.id, is_admin)

            if not is_admin:
                async with session_factory() as session:
                    mod = await get_moderator(session, event.chat.id, event.from_user.id)
                    is_moderator = mod is not None
                    if mod is not None:
                        data["mod_permissions"] = set(
                            p.strip() for p in (mod.permissions or "").split(",") if p.strip()
                        )

        data["is_admin"] = is_admin
        data["is_moderator"] = is_moderator
        data.setdefault("mod_permissions", set())
        return await handler(event, data)
