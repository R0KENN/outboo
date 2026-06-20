"""Middleware определения прав пользователя (раздел 6, безопасность)."""
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.enums import ChatMemberStatus
from aiogram.types import Message

from database.crud import get_moderator
from database.engine import session_factory
from config import settings


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
            try:
                member = await event.bot.get_chat_member(
                    event.chat.id, event.from_user.id
                )
                is_admin = member.status in (
                    ChatMemberStatus.ADMINISTRATOR,
                    ChatMemberStatus.CREATOR,
                )
            except Exception:
                is_admin = False

            if not is_admin:
                async with session_factory() as session:
                    mod = await get_moderator(
                        session, event.chat.id, event.from_user.id
                    )
                    is_moderator = mod is not None
                    if mod is not None:
                        data["mod_permissions"] = set(
                            p.strip() for p in (mod.permissions or "").split(",")
                            if p.strip()
                        )

        data["is_admin"] = is_admin
        data["is_moderator"] = is_moderator
        data.setdefault("mod_permissions", set())
        return await handler(event, data)