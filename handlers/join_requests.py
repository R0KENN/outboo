"""Заявки на вступление в группу/канал.

Когда у чата включён режим «заявки», Telegram присылает chat_join_request.
Если в настройках чата autoapprove_enabled=True — бот одобряет заявку
автоматически. Бот должен быть админом с правом «приглашать пользователей».

После одобрения (авто или вручную) можно отправить приветствие новому
участнику в личку — это единственный способ «встретить» подписчика канала,
т.к. в самом канале персональных сообщений нет. Личное сообщение пройдёт
только если у пользователя открыта личка с ботом (для заявок Telegram это
обычно разрешает).
"""

import logging

from aiogram import Bot, Router
from aiogram.types import ChatJoinRequest

from database.crud import get_or_create_chat_settings
from database.engine import session_factory

logger = logging.getLogger(__name__)
router = Router(name="join_requests")


async def _send_join_welcome(bot: Bot, user, cfg) -> None:
    """Отправляет приветствие в личку новому участнику, если включено."""
    if not cfg.join_welcome_enabled or not cfg.join_welcome_text:
        return
    from aiogram.utils.text_decorations import html_decoration

    safe_name = html_decoration.quote(user.full_name or "")
    text = cfg.join_welcome_text.replace("{name}", safe_name)
    try:
        await bot.send_message(user.id, text, parse_mode="HTML")
        logger.info("Приветствие отправлено пользователю %s.", user.id)
    except Exception as e:
        # Пользователь не открывал личку с ботом или заблокировал его.
        logger.info("Не удалось отправить приветствие %s: %s", user.id, e)


@router.chat_join_request()
async def on_join_request(event: ChatJoinRequest, bot: Bot) -> None:
    """Одобряет заявку (если включён автоприём) и шлёт приветствие в ЛС."""
    chat_id = event.chat.id
    user = event.from_user

    async with session_factory() as session:
        cfg = await get_or_create_chat_settings(session, chat_id)

    # Автоприём выключен — заявку решают админы вручную.
    # Но приветствие можно отправить сразу: заявка = явный интерес вступить,
    # и Telegram разрешает боту написать заявителю в личку.
    if not cfg.autoapprove_enabled:
        await _send_join_welcome(bot, user, cfg)
        return

    try:
        await bot.approve_chat_join_request(chat_id=chat_id, user_id=user.id)
        logger.info("Заявка одобрена: %s в %s.", user.id, chat_id)
    except Exception as e:
        logger.warning("Не удалось одобрить заявку в %s: %s", chat_id, e)
        return

    await _send_join_welcome(bot, user, cfg)
