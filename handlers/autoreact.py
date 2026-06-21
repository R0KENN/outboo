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
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    MessageReactionUpdated,
    ReactionTypeCustomEmoji,
    ReactionTypeEmoji,
)

from config import settings as app_settings
from database.crud import get_or_create_chat_settings
from database.engine import session_factory
from utils.rate_limit import safe_call

logger = logging.getLogger(__name__)
router = Router(name="autoreact")


def _parse_emojis(raw: str) -> list[str]:
    """Разбирает строку настроек '👍,🔥,❤️' в список эмодзи."""
    return [e.strip() for e in (raw or "").split(",") if e.strip()]


async def _apply_reaction(bot: Bot, chat_id: int, message_id: int, cfg) -> bool:
    emojis = _parse_emojis(cfg.autoreact_emojis)
    if not emojis:
        return False
    chosen = random.choice(emojis)
    try:
        await bot.set_message_reaction(
            chat_id=chat_id,
            message_id=message_id,
            reaction=[ReactionTypeEmoji(emoji=chosen)],
        )
        return True
    except Exception as e:
        logger.warning(
            "Автореакция %s в %s/%s не поставлена: %s", chosen, chat_id, message_id, e
        )
        # Фолбэк на гарантированно валидный 👍
        if chosen != "👍":
            try:
                await bot.set_message_reaction(
                    chat_id=chat_id,
                    message_id=message_id,
                    reaction=[ReactionTypeEmoji(emoji="👍")],
                )
                return True
            except Exception:
                pass
        return False


@router.channel_post()
async def react_to_post(message: Message, bot: Bot) -> None:
    """Базовая автореакция на новый пост канала (обычный эмодзи)."""
    async with session_factory() as session:
        cfg = await get_or_create_chat_settings(session, message.chat.id)

    if not cfg.autoreact_enabled:
        logger.info("Пост в %s: автореакции выключены — пропускаю.", message.chat.id)
        return

    if not _parse_emojis(cfg.autoreact_emojis):
        logger.info("Пост в %s: список эмодзи пуст — нечего ставить.", message.chat.id)
        return

    ok = await _apply_reaction(bot, message.chat.id, message.message_id, cfg)
    if ok:
        logger.info("Реакция поставлена на %s/%s.", message.chat.id, message.message_id)


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
        r.custom_emoji_id
        for r in (update.new_reaction or [])
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
        logger.warning(
            "Не удалось присоединиться к кастом-реакции в %s/%s: %s",
            update.chat.id,
            update.message_id,
            e,
        )


# ──────────────────────────────────────────────────────────────────────────
# Простановка реакций на старые посты (по диапазону message_id)
# ──────────────────────────────────────────────────────────────────────────
async def _is_channel_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    if user_id in app_settings.admin_ids:
        return True
    try:
        m = await bot.get_chat_member(chat_id, user_id)
        return m.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR)
    except Exception:
        return False

async def _react_range(
    bot: Bot,
    message: Message,
    chat_id: int,
    from_id: int,
    to_id: int,
    emojis: list[str],
) -> None:
    """Ставит реакции на диапазон постов канала с живым прогрессом."""
    total = to_id - from_id + 1
    progress = await message.answer(
        f"⏳ Ставлю реакции на посты #{from_id}–#{to_id} (всего {total})…"
    )

    ok = 0
    fail = 0
    done = 0
    for mid in range(from_id, to_id + 1):
        chosen = random.choice(emojis)
        result = await safe_call(
            lambda m=mid, e=chosen: bot.set_message_reaction(
                chat_id=chat_id,
                message_id=m,
                reaction=[ReactionTypeEmoji(emoji=e)],
            ),
            delay=0.2,
        )
        if result is not None:
            ok += 1
        else:
            fail += 1
        done += 1

        if done % 20 == 0:
            try:
                await progress.edit_text(
                    f"⏳ Обработано {done} из {total}…\n"
                    f"✅ Поставлено: {ok}  ⏭ Пропущено: {fail}"
                )
            except Exception:
                pass

    await progress.edit_text(
        f"✅ <b>Готово.</b>\n"
        f"Поставлено: <b>{ok}</b>\n"
        f"Пропущено (нет поста / нельзя реагировать): <b>{fail}</b>"
    )

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

    await _react_range(bot, message, chat.id, from_id, to_id, emojis=_parse_emojis(cfg.autoreact_emojis))


class ReactRangeFSM(StatesGroup):
    waiting_range = State()


@router.callback_query(F.data.startswith("react:oldposts:"))
async def cb_react_oldposts(callback: CallbackQuery, state: FSMContext) -> None:
    """Кнопка из карточки канала: просим прислать диапазон id."""
    chat_id = int(callback.data.split(":")[2])
    if not await _is_channel_admin(callback.bot, chat_id, callback.from_user.id):
        await callback.answer("Только для администраторов канала.", show_alert=True)
        return

    await state.set_state(ReactRangeFSM.waiting_range)
    await state.update_data(react_chat_id=chat_id)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Отмена", callback_data="react:cancel")]]
    )
    await callback.message.edit_text(
        "🔁 <b>Реакции на старые посты</b>\n\n"
        "Пришлите диапазон id постов через пробел: <code>from to</code>\n"
        "Например: <code>100 250</code>\n\n"
        "id поста виден в его ссылке: t.me/канал/<b>123</b> → id = 123.\n"
        "За раз — не больше 500 постов.",
        reply_markup=kb,
    )
    await callback.answer()


@router.callback_query(ReactRangeFSM.waiting_range, F.data == "react:cancel")
async def cb_react_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Отменено.")
    await callback.answer()


@router.message(ReactRangeFSM.waiting_range)
async def step_react_range(message: Message, bot: Bot, state: FSMContext) -> None:
    """Принимает диапазон и ставит реакции напрямую (без подмены текста)."""
    parts = (message.text or "").split()
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        await message.answer("Нужно два числа через пробел, например: 100 250")
        return

    from_id, to_id = int(parts[0]), int(parts[1])
    if from_id > to_id:
        from_id, to_id = to_id, from_id
    if to_id - from_id > 500:
        await message.answer("За раз не больше 500 постов. Сузьте диапазон.")
        return

    data = await state.get_data()
    chat_id = data.get("react_chat_id")
    await state.clear()

    if not chat_id:
        await message.answer("Потерял канал. Откройте настройки канала заново.")
        return

    async with session_factory() as session:
        cfg = await get_or_create_chat_settings(session, chat_id)

    emojis = _parse_emojis(cfg.autoreact_emojis)
    if not emojis:
        await message.answer(
            "В настройках канала не выбран ни один эмодзи реакции.\n"
            "Откройте настройки канала и задайте эмодзи."
        )
        return

    await _react_range(bot, message, chat_id, from_id, to_id, emojis=emojis)
