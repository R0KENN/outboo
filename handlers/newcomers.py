"""Управление новыми участниками: капча, приветствие, карантin, очистка (раздел 4.2)."""

import asyncio
import logging

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    ChatPermissions,
    Message,
)

from database import crud
from database.crud import get_or_create_chat_settings, record_join
from database.engine import session_factory
from services import captcha as cap

logger = logging.getLogger(__name__)
router = Router(name="newcomers")


async def _restrict(bot, chat_id: int, user_id: int) -> None:
    """Полностью запрещает писать (на время прохождения капчи)."""
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
    )


async def _unrestrict(bot, chat_id: int, user_id: int) -> None:
    """Возвращает полный набор прав после успешной капчи."""
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
    )


async def _send_welcome(bot, chat_id: int, user, cfg) -> None:
    """Отправляет приветствие с подстановкой имени и автоудалением."""
    if not cfg.welcome_enabled:
        return
    from aiogram.utils.text_decorations import html_decoration

    text = cfg.welcome_text.replace("{name}", html_decoration.quote(user.full_name or ""))
    if cfg.rules_text:
        text += f"\n\n{cfg.rules_text}"
    msg = await bot.send_message(chat_id, text)
    if cfg.welcome_delete_after > 0:

        async def _delayed_delete():
            await asyncio.sleep(cfg.welcome_delete_after)
            try:
                await bot.delete_message(chat_id, msg.message_id)
            except Exception:
                pass

        asyncio.create_task(_delayed_delete())


async def _captcha_timeout(bot, chat_id: int, user_id: int, timeout: int) -> None:
    """Кикает новичка, если он не прошёл капчу за отведённое время."""
    await asyncio.sleep(timeout)
    key = (chat_id, user_id)
    item = cap.pending.get(key)
    if item is None:
        return  # уже прошёл капчу
    cap.pending.pop(key, None)
    try:
        # Кик: бан + разбан, чтобы мог зайти заново
        await bot.ban_chat_member(chat_id, user_id)
        await bot.unban_chat_member(chat_id, user_id, only_if_banned=True)
        if item.prompt_message_id:
            await bot.delete_message(chat_id, item.prompt_message_id)
    except Exception as e:
        logger.warning("Не удалось кикнуть по таймауту капчи: %s", e)


@router.chat_member()
async def on_member_update(event: ChatMemberUpdated) -> None:
    """Срабатывает на вход нового участника."""
    old_status = event.old_chat_member.status
    new_status = event.new_chat_member.status

    # Интересует переход в участники (вход в чат)
    joined = old_status in ("left", "kicked") and new_status == "member"
    if not joined:
        return

    user = event.new_chat_member.user
    if user.is_bot:
        return

    chat_id = event.chat.id
    # Фиксируем время входа для карантина новичков и считаем статистику
    async with session_factory() as session:
        await record_join(session, chat_id, user.id)
        await crud.bump_stat(session, chat_id, "new_members")
    async with session_factory() as session:
        cfg = await get_or_create_chat_settings(session, chat_id)

    bot = event.bot

    # Если капча выключена — сразу приветствуем
    if not cfg.captcha_enabled:
        await _send_welcome(bot, chat_id, user, cfg)
        return

    # Капча включена: ограничиваем и показываем проверку
    try:
        await _restrict(bot, chat_id, user.id)
    except Exception as e:
        logger.warning("Не удалось ограничить новичка: %s", e)
        return

    text, kb, correct = cap.build_captcha(cfg.captcha_type, chat_id, user.id)
    prompt = await bot.send_message(
        chat_id,
        f"{user.full_name}, {text}",
        reply_markup=kb,
    )

    cap.pending[(chat_id, user.id)] = cap.PendingCaptcha(
        user_id=user.id,
        chat_id=chat_id,
        correct=correct,
        join_message_id=0,
        prompt_message_id=prompt.message_id,
    )
    asyncio.create_task(_captcha_timeout(bot, chat_id, user.id, cfg.captcha_timeout))


@router.callback_query(F.data.startswith("captcha:"))
async def on_captcha_answer(callback: CallbackQuery) -> None:
    """Обрабатывает нажатие кнопки капчи."""
    parts = callback.data.split(":")
    _ = parts[1]  # тип (ok | ans) — пока не используется
    chat_id = int(parts[2])
    user_id = int(parts[3])
    answer = parts[4] if len(parts) > 4 else "ok"

    # Отвечать на капчу может только тот, кому она адресована
    if callback.from_user.id != user_id:
        await callback.answer("Это проверка не для вас.", show_alert=True)
        return

    key = (chat_id, user_id)
    item = cap.pending.get(key)
    if item is None:
        await callback.answer("Проверка уже неактуальна.")
        return

    if answer == item.correct:
        cap.pending.pop(key, None)
        await _unrestrict(callback.bot, chat_id, user_id)
        try:
            await callback.message.delete()
        except Exception:
            pass
        async with session_factory() as session:
            cfg = await get_or_create_chat_settings(session, chat_id)
        await _send_welcome(callback.bot, chat_id, callback.from_user, cfg)
        await callback.answer("Добро пожаловать!")
    else:
        await callback.answer("Неверно, попробуйте ещё раз.", show_alert=True)


@router.message(F.new_chat_members | F.left_chat_member)
async def clean_service_messages(message: Message) -> None:
    """Удаляет служебные сообщения о входе/выходе, если включено в настройках."""
    if message.chat.type not in ("group", "supergroup"):
        return
    async with session_factory() as session:
        cfg = await get_or_create_chat_settings(session, message.chat.id)
    if cfg.clean_service_msgs:
        try:
            await message.delete()
        except Exception:
            pass
