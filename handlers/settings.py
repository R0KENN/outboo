"""Панель настроек чата на inline-кнопках (раздел 4.4 ТЗ).

Команда /settings открывает меню. Нажатия обрабатываются по callback_data
вида set:<действие>:<поле>:<chat_id>. Доступ — только администраторам.
"""

import logging

from aiogram import F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from database.crud import get_managed_chat, get_or_create_chat_settings
from database.engine import session_factory
from keyboards.settings_kb import autoreact_kb, main_settings_kb, params_kb

logger = logging.getLogger(__name__)
router = Router(name="settings")


class JoinWelcomeFSM(StatesGroup):
    text = State()

# Границы значений, чтобы кнопками нельзя было выставить абсурд
LIMITS = {
    "warn_limit": (1, 10),
    "flood_messages": (2, 30),
    "flood_seconds": (1, 60),
    "captcha_timeout": (30, 600),
}
STEP = {
    "warn_limit": 1,
    "flood_messages": 1,
    "flood_seconds": 1,
    "captcha_timeout": 30,
}


async def _is_admin(message_or_cb, chat_id: int, user_id: int) -> bool:
    """Проверяет, что пользователь — администратор чата."""
    try:
        member = await message_or_cb.bot.get_chat_member(chat_id, user_id)
        return member.status in (
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.CREATOR,
        )
    except Exception:
        return False


@router.message(Command("settings"))
async def cmd_settings(message: Message) -> None:
    """Открывает панель настроек. Работает только в группах и только у админа."""
    if message.chat.type not in ("group", "supergroup"):
        await message.answer("Настройки доступны внутри группы.")
        return
    if not await _is_admin(message, message.chat.id, message.from_user.id):
        await message.answer("Панель настроек доступна только администраторам.")
        return

    async with session_factory() as session:
        cfg = await get_or_create_chat_settings(session, message.chat.id)
        kb = main_settings_kb(cfg, message.chat.type)
    await message.answer("⚙️ <b>Настройки чата</b>\nНажмите, чтобы переключить:", reply_markup=kb)


@router.callback_query(F.data.startswith("set:"))
async def on_settings_callback(callback: CallbackQuery, state: FSMContext):
    """Обрабатывает все нажатия в панели настроек."""
    parts = callback.data.split(":")
    action = parts[1]

    if action == "noop":
        await callback.answer()
        return

    if action == "joinwelcometext":
        await callback.message.answer(
            "Чтобы задать текст приветствия в ЛС, отправьте в этом канале команду:\n"
            "<code>/setjoinwelcome ваш текст</code>\n"
            "Доступен плейсхолдер {name}."
        )
        await callback.answer()
        return

    # Последний элемент callback_data — всегда chat_id
    chat_id = int(parts[-1])
    # Тип чата нужен для правильного набора кнопок настроек
    async with session_factory() as _s:
        _mc = await get_managed_chat(_s, chat_id)
    chat_type = _mc.chat_type if _mc else "group"

    # Защита: переключать настройки может только админ этого чата
    if not await _is_admin(callback, chat_id, callback.from_user.id):
        await callback.answer("Только для администраторов.", show_alert=True)
        return

    async with session_factory() as session:
        cfg = await get_or_create_chat_settings(session, chat_id)

        if action == "toggle":
            field = parts[2]
            setattr(cfg, field, not getattr(cfg, field))
            await session.commit()
            await session.refresh(cfg)
            await callback.message.edit_reply_markup(reply_markup=main_settings_kb(cfg, chat_type))
            await callback.answer("Сохранено.")

        elif action in ("inc", "dec"):
            field = parts[2]
            lo, hi = LIMITS[field]
            step = STEP[field] * (1 if action == "inc" else -1)
            new_val = max(lo, min(hi, getattr(cfg, field) + step))
            setattr(cfg, field, new_val)
            await session.commit()
            await session.refresh(cfg)
            await callback.message.edit_reply_markup(reply_markup=params_kb(cfg))
            await callback.answer(f"{field} = {new_val}")

        elif action == "warnaction":
            cfg.warn_action = "ban" if cfg.warn_action == "mute" else "mute"
            await session.commit()
            await session.refresh(cfg)
            await callback.message.edit_reply_markup(reply_markup=params_kb(cfg))
            await callback.answer(f"Действие: {cfg.warn_action}")

        elif action == "captchatype":
            cfg.captcha_type = "math" if cfg.captcha_type == "button" else "button"
            await session.commit()
            await session.refresh(cfg)
            await callback.message.edit_reply_markup(reply_markup=params_kb(cfg))
            await callback.answer(f"Тип капчи: {cfg.captcha_type}")

        elif action == "react":
            # Открыть подменю автореакций
            await callback.message.edit_reply_markup(reply_markup=autoreact_kb(cfg))
            await callback.answer()

        elif action == "reactmode":
            cfg.autoreact_random = not cfg.autoreact_random
            await session.commit()
            await session.refresh(cfg)
            await callback.message.edit_reply_markup(reply_markup=autoreact_kb(cfg))
            await callback.answer(
                "Случайная реакция" if cfg.autoreact_random else "Все реакции сразу"
            )

        elif action == "reactemoji":
            # Мультивыбор эмодзи: добавляем/убираем из набора
            emoji = parts[2]
            current = [e.strip() for e in (cfg.autoreact_emojis or "").split(",") if e.strip()]
            if emoji in current:
                current.remove(emoji)
            else:
                current.append(emoji)
            cfg.autoreact_emojis = ",".join(current)
            await session.commit()
            await session.refresh(cfg)
            await callback.message.edit_reply_markup(reply_markup=autoreact_kb(cfg))
            await callback.answer(f"Набор: {cfg.autoreact_emojis or 'пусто'}")

        elif action == "params":
            await callback.message.edit_reply_markup(reply_markup=params_kb(cfg))
            await callback.answer()

        elif action == "refresh":
            await callback.message.edit_text(
                "⚙️ <b>Настройки</b>\nНажмите, чтобы переключить:",
                reply_markup=main_settings_kb(cfg, chat_type),
            )
            await callback.answer()


@router.message(Command("setwelcome"))
async def cmd_set_welcome(message: Message) -> None:
    """Задаёт текст приветствия. Используйте {name} для подстановки имени."""
    if not await _is_admin(message, message.chat.id, message.from_user.id):
        return
    # Берём текст после команды с сохранением форматирования (HTML)
    text = (message.html_text or "").partition(" ")[2].strip()
    if not text:
        await message.answer(
            "Укажите текст после команды.\nПример: /setwelcome Привет, {name}! Читай правила."
        )
        return
    async with session_factory() as session:
        cfg = await get_or_create_chat_settings(session, message.chat.id)
        cfg.welcome_text = text
        await session.commit()
    await message.answer("Текст приветствия обновлён.")

@router.message(Command("setjoinwelcome"))
async def cmd_set_join_welcome(message: Message) -> None:
    """Задаёт текст приветствия в ЛС новым подписчикам (по заявке).

    Используйте {name} для подстановки имени. Отправьте команду в нужном
    канале/группе. Бот должен быть админом, а у чата включён режим заявок.
    """
    if not await _is_admin(message, message.chat.id, message.from_user.id):
        return
    # Берём текст после команды с сохранением форматирования (HTML)
    text = (message.html_text or "").partition(" ")[2].strip()
    if not text:
        await message.answer(
            "Укажите текст после команды.\n"
            "Пример: /setjoinwelcome Привет, {name}! Спасибо за подписку 🎉"
        )
        return
    async with session_factory() as session:
        cfg = await get_or_create_chat_settings(session, message.chat.id)
        cfg.join_welcome_text = text
        cfg.join_welcome_enabled = True
        await session.commit()
    await message.answer("Приветствие в ЛС обновлено и включено.")

@router.message(Command("setrules"))
async def cmd_set_rules(message: Message) -> None:
    """Задаёт текст правил, который добавляется к приветствию."""
    if not await _is_admin(message, message.chat.id, message.from_user.id):
        return
    text = (message.html_text or "").partition(" ")[2].strip()
    if not text:
        await message.answer("Укажите текст правил после команды.")
        return
    async with session_factory() as session:
        cfg = await get_or_create_chat_settings(session, message.chat.id)
        cfg.rules_text = text
        await session.commit()
    await message.answer("Правила обновлены.")
