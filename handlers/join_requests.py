"""Автоприём заявок на вступление в группу/канал (раздел расширения).

Когда у чата включён режим «заявки», Telegram присылает chat_join_request.
Если в настройках чата autoapprove_enabled=True — бот одобряет заявку
автоматически. Бот должен быть админом с правом «приглашать пользователей».
"""

import logging

from aiogram import Bot, Router
from aiogram.types import ChatJoinRequest

from database.crud import get_or_create_chat_settings
from database.engine import session_factory

logger = logging.getLogger(__name__)
router = Router(name="join_requests")


@router.chat_join_request()
async def on_join_request(event: ChatJoinRequest, bot: Bot) -> None:
    """Одобряет заявку на вступление, если включён автоприём."""
    chat_id = event.chat.id

    async with session_factory() as session:
        cfg = await get_or_create_chat_settings(session, chat_id)

    if not cfg.autoapprove_enabled:
        return  # автоприём выключен — оставляем заявку админам

    try:
        await bot.approve_chat_join_request(
            chat_id=chat_id,
            user_id=event.from_user.id,
        )
        logger.info(
            "Заявка одобрена: %s в %s.",
            event.from_user.id,
            chat_id,
        )
        # По желанию — личное приветствие новому участнику в ЛС
        if cfg.welcome_enabled and cfg.welcome_text:
            try:
                text = cfg.welcome_text.replace("{name}", event.from_user.full_name)
                await bot.send_message(event.from_user.id, text)
            except Exception:
                pass  # пользователь не открывал ЛС с ботом
    except Exception as e:
        logger.warning("Не удалось одобрить заявку в %s: %s", chat_id, e)
