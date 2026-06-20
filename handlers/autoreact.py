"""Автореакции бота на посты канала.

Ограничения Telegram Bot API, которые определяют логику:
- бот не премиум, поэтому ставит МАКСИМУМ одну реакцию на сообщение;
- кастом-эмодзи реакцию бот может поставить ТОЛЬКО если она уже присутствует
  на сообщении (или явно разрешена админами). Поэтому для кастома мы
  «присоединяемся» к уже стоящей на посте кастомной реакции;
- историю канала Bot API не отдаёт — простановка на старые посты делается
  командой /reactrange по диапазону message_id (см. ниже в этом файле).
"""
import logging
import random

from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.filters import Command
from aiogram.types import (
    Message, MessageReactionUpdated,
    ReactionTypeEmoji, ReactionTypeCustomEmoji,
)

from config import settings as app_settings
from database.crud import get_or_create_chat_settings, get_managed_chat
from database.engine import session_factory

logger = logging.getLogger(__name__)
router = Router(name="autoreact")


def _parse_emojis(raw: str) -> list[str]:
    """Разбирает строку настроек '👍,🔥,❤️' в список эмодзи."""
    return [e.strip() for e in (raw or "").split(",") if e.strip()]


async def _apply_reaction(bot: Bot, chat_id: int, message_id: int, cfg) -> bool:
    """Ставит одну реакцию на сообщение по настройкам канала.

    Возвращает True, если реакция поставлена. Кастом-эмодзи здесь не ставим
    (его нельзя инициировать) — для кастома есть join-логика в on_reaction.
    """
    emojis = _parse_emojis(cfg.autoreact_emojis)
    if not emojis:
        return False

    # Бот ставит ровно одну реакцию: берём случайную из набора.
    chosen = random.choice(emojis)
    try:
        await bot.set_message_reaction(
            chat_id=chat_id,
            message_id=message_id,
            reaction=[ReactionTypeEmoji(emoji=chosen)],
        )
        return True
    except Exception as e:
        logger.warning("Автореакция в %s/%s не поставлена: %s",
                       chat_id, message_id, e)
        return False


@router.channel_post()
async def react_to_post(message: Message, bot: Bot) -> None:
    """Базовая автореакция на новый пост канала (обычный эмодзи)."""
    async with session_factory() as session:
        cfg = await get_or_create_chat_settings(session, message.chat.id)
    if not cfg.autoreact_enabled:
        return
    await _apply_reaction(bot, message.chat.id, message.message_id, cfg)


@router.message_reaction()
async def join_custom_reaction(update: MessageReactionUpdated, bot: Bot) -> None:
    """Присоединяется к кастом-эмодзи реакции, которую поставил кто-то другой.

    Это единственный легальный способ для бота поставить КАСТОМНУЮ реакцию:
    она уже присутствует на сообщении. Срабатывает, когда на пост ставят
    премиум-реакцию. Требует, чтобы у канала был включён autoreact_join_custom.
    """
    async with session_factory() as session:
        cfg = await get_or_create_chat_settings(session, update.chat.id)

    if not getattr(cfg, "autoreact_join_custom", False):
        return

    # Ищем среди новых реакций кастомную (премиум-эмодзи).
    custom_ids = [
        r.custom_emoji_id for r in (update.new_reaction or [])
        if isinstance(r, ReactionTypeCustomEmoji)
    ]
    if not custom_ids:
        return

    try:
        await bot.set_message_reaction(
            chat_id=update.chat.id,
            message_id=update.message_id,
            reaction=[ReactionTypeCustomEmoji(custom_emoji_id=custom_ids[0])],
        )
    except Exception as e:
        logger.warning("Не удалось присоединиться к кастом-реакции в %s/%s: %s",
                       update.chat.id, update.message_id, e)


# ──────────────────────────────────────────────────────────────────────────
# Простановка реакций на старые посты (по диапазону message_id)
# ──────────────────────────────────────────────────────────────────────────
async def _is_channel_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    if user_id in app_settings.admin_ids:
        return True
    try:
        m = await bot.get_chat_member(chat_id, user_id)
        return m.status in (ChatMemberStatus.ADMINISTRATOR,
                            ChatMemberStatus.CREATOR)
    except Exception:
        return False


@router.message(Command("reactrange"))
async def cmd_reactrange(message: Message, bot: Bot) -> None:
    """Ставит автореакцию на диапазон старых постов канала.

    Формат: /reactrange <channel_id или @username> <from_id> <to_id>
    Пример: /reactrange @mychannel 100 250
    id поста виден в ссылке: t.me/mychannel/123 → id = 123.

    Историю канала Bot API не отдаёт, поэтому диапазон задаётся вручную.
    """
    parts = (message.text or "").split()
    if len(parts) < 4:
        await message.answer(
            "Формат: <code>/reactrange &lt;канал&gt; &lt;from_id&gt; &lt;to_id&gt;</code>\n"
            "Пример: <code>/reactrange @mychannel 100 250</code>\n\n"
            "id поста виден в его ссылке: t.me/канал/<b>123</b> → id = 123."
        )
        return

    target, from_raw, to_raw = parts[1], parts[2], parts[3]
    if not (from_raw.isdigit() and to_raw.isdigit()):
        await message.answer("from_id и to_id должны быть числами.")
        return
    from_id, to_id = int(from_raw), int(to_raw)
    if from_id > to_id:
        from_id, to_id = to_id, from_id
    if to_id - from_id > 500:
        await message.answer("За раз не больше 500 постов. Сузьте диапазон.")
        return

    # Разрешаем @username или числовой id
    try:
        chat = await bot.get_chat(target if target.startswith("@") else int(target))
    except Exception:
        await message.answer("Не нашёл канал. Проверьте @username или id.")
        return

    if not await _is_channel_admin(bot, chat.id, message.from_user.id):
        await message.answer("Команда доступна только администраторам канала.")
        return

    async with session_factory() as session:
        cfg = await get_or_create_chat_settings(session, chat.id)
    if not _parse_emojis(cfg.autoreact_emojis):
        await message.answer("В настройках канала не выбран ни один эмодзи реакции.")
        return

    await message.answer(
        f"Ставлю реакции на посты #{from_id}–#{to_id}…\n"
        "Это может занять время (Telegram ограничивает частоту)."
    )

    import asyncio
    ok = 0
    fail = 0
    for mid in range(from_id, to_id + 1):
        done = await _apply_reaction(bot, chat.id, mid, cfg)
        if done:
            ok += 1
        else:
            fail += 1
        await asyncio.sleep(0.3)  # бережём лимиты Telegram

    await message.answer(
        f"Готово. Поставлено: <b>{ok}</b>. "
        f"Пропущено (нет поста/нельзя): <b>{fail}</b>."
    )
