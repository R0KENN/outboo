"""Автореакции бота на новые посты канала (раздел расширения).

Telegram присылает channel_post при публикации в канале, где бот — админ.
Если у канала включён autoreact_enabled, бот ставит реакцию (одну случайную
из набора или сразу все). Бот должен быть админом канала; ставить реакции
от своего имени он может без спец-права.
"""
import logging
import random

from aiogram import Bot, Router
from aiogram.types import Message, ReactionTypeEmoji

from database.crud import get_or_create_chat_settings
from database.engine import session_factory

logger = logging.getLogger(__name__)
router = Router(name="autoreact")


def _parse_emojis(raw: str) -> list[str]:
    """Разбирает строку настроек '👍,🔥,❤️' в список эмодзи."""
    return [e.strip() for e in (raw or "").split(",") if e.strip()]


@router.channel_post()
async def react_to_post(message: Message, bot: Bot) -> None:
    """Ставит автореакцию на новый пост канала."""
    chat_id = message.chat.id

    async with session_factory() as session:
        cfg = await get_or_create_chat_settings(session, chat_id)

    if not cfg.autoreact_enabled:
        return

    emojis = _parse_emojis(cfg.autoreact_emojis)
    if not emojis:
        return

    # Один случайный эмодзи или весь набор сразу
    if cfg.autoreact_random:
        chosen = [random.choice(emojis)]
    else:
        # Telegram ограничивает число реакций от одного аккаунта;
        # берём максимум первые 3, чтобы не упереться в лимит.
        chosen = emojis[:3]

    reactions = [ReactionTypeEmoji(emoji=e) for e in chosen]

    try:
        await bot.set_message_reaction(
            chat_id=chat_id,
            message_id=message.message_id,
            reaction=reactions,
        )
    except Exception as e:
        # Частая причина — эмодзи не из числа разрешённых каналом,
        # либо у бота нет прав. Логируем и не падаем.
        logger.warning("Автореакция в %s не поставлена: %s", chat_id, e)
