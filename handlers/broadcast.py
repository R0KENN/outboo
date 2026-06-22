"""Команды массовой рассылки (раздел 4.6 ТЗ). Только для владельцев бота.

Рассылка ведётся в личке с ботом. Сценарий:
  1. Владелец пишет /broadcast.
  2. Бот просит прислать сообщение для рассылки (любой контент).
  3. Бот показывает, скольким подписчикам уйдёт, и просит подтвердить.
  4. По подтверждению запускается рассылка, в конце — сводка.
"""

import asyncio
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import settings
from database import crud
from database.engine import session_factory
from services import broadcast as bc

logger = logging.getLogger(__name__)
router = Router(name="broadcast")


class Broadcast(StatesGroup):
    """Шаги диалога рассылки."""

    content = State()
    segment = State()
    confirm = State()


def _is_owner(user_id: int) -> bool:
    """Рассылку запускают только владельцы бота из BOT_ADMINS."""
    return user_id in settings.admin_ids


@router.message(Command("subs"))
async def cmd_subs(message: Message) -> None:
    """Показывает размер базы подписчиков."""
    if message.chat.type != "private" or not _is_owner(message.from_user.id):
        return
    async with session_factory() as session:
        total, active = await crud.count_subscribers(session)
    await message.answer(
        f"👥 Подписчиков всего: <b>{total}</b>\nАктивных (получат рассылку): <b>{active}</b>"
    )


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message, state: FSMContext) -> None:
    """Запускает диалог рассылки (только в личке, только для владельца)."""
    if message.chat.type != "private":
        await message.answer("Рассылка запускается в личке со мной.")
        return
    if not _is_owner(message.from_user.id):
        await message.answer("Команда доступна только владельцам бота.")
        return

    await state.clear()
    await state.set_state(Broadcast.content)
    await message.answer(
        "📨 Пришлите сообщение для рассылки.\n"
        "Это может быть текст, фото, видео, документ — с форматированием и кнопками.\n"
        "Сообщение будет разослано как есть.\n\n"
        "Для отмены: /cancel"
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    """Отменяет любой активный диалог рассылки."""
    if await state.get_state() is not None:
        await state.clear()
        await message.answer("Текущее действие отменено.")


@router.message(Broadcast.content)
async def step_content(message: Message, state: FSMContext) -> None:
    """Принимает сообщение для рассылки и просит подтверждение."""
    # Запоминаем, откуда копировать (чат и id сообщения)
    await state.update_data(
        from_chat_id=message.chat.id,
        message_id=message.message_id,
    )
    # Список каналов, через которые приходили подписчики — для сегментации
    async with session_factory() as session:
        from database.crud import list_managed_chats

        chats = await list_managed_chats(session, only_active=True)

    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="📢 Всем подписчикам", callback_data="bc:seg:all"))
    for ch in chats:
        if ch.chat_type != "channel":
            continue
        title = ch.title or str(ch.chat_id)
        b.row(InlineKeyboardButton(text=f"📢 {title}", callback_data=f"bc:seg:{ch.chat_id}"))
    b.row(InlineKeyboardButton(text="❌ Отмена", callback_data="bc:cancel"))

    await state.set_state(Broadcast.segment)
    await message.answer(
        "Кому разослать?\n«Всем» — всем активным подписчикам бота.\n"
        "Канал — только тем, кто пришёл к боту через этот канал.",
        reply_markup=b.as_markup(),
    )


@router.callback_query(F.data == "bc:cancel")
async def cb_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Рассылка отменена.")
    await callback.answer()

@router.callback_query(Broadcast.segment, F.data.startswith("bc:seg:"))
async def cb_segment(callback: CallbackQuery, state: FSMContext) -> None:
    """Выбор сегмента получателей: все или конкретный канал."""
    raw = callback.data.split(":")[2]
    source_chat_id = None if raw == "all" else int(raw)
    await state.update_data(source_chat_id=source_chat_id)

    async with session_factory() as session:
        if source_chat_id is None:
            _, active = await crud.count_subscribers(session)
        else:
            ids = await crud.get_active_subscriber_ids_by_source(session, source_chat_id)
            active = len(ids)

    await state.set_state(Broadcast.confirm)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Разослать", callback_data="bc:go"),
                InlineKeyboardButton(text="❌ Отмена", callback_data="bc:cancel"),
            ]
        ]
    )
    seg_name = "всем подписчикам" if source_chat_id is None else "сегменту канала"
    await callback.message.edit_text(
        f"Рассылка {seg_name}. Получателей: <b>{active}</b>.\nЗапустить?",
        reply_markup=kb,
    )
    await callback.answer()

@router.callback_query(F.data == "bc:go")
async def cb_go(callback: CallbackQuery, state: FSMContext) -> None:
    """Запускает рассылку в фоне, чтобы не блокировать бота."""
    data = await state.get_data()
    await state.clear()

    from_chat_id = data.get("from_chat_id")
    message_id = data.get("message_id")
    source_chat_id = data.get("source_chat_id")
    if not from_chat_id or not message_id:
        await callback.message.edit_text("Не нашёл сообщение для рассылки, начните заново.")
        await callback.answer()
        return

    await callback.message.edit_text("📤 Рассылка запущена…")
    await callback.answer()

    bot = callback.bot
    owner_id = callback.from_user.id

    async def _worker():
        try:
            summary = await bc.run_broadcast(
                bot, from_chat_id, message_id, source_chat_id=source_chat_id
            )
            await bot.send_message(
                owner_id,
                "✅ <b>Рассылка завершена</b>\n"
                f"Всего: {summary['total']}\n"
                f"Доставлено: {summary['sent']}\n"
                f"Заблокировали бота: {summary['blocked']}\n"
                f"Ошибок: {summary['failed']}",
            )
        except Exception as e:
            logger.exception("Ошибка рассылки: %s", e)
            await bot.send_message(owner_id, f"❌ Рассылка прервалась: {e}")

    asyncio.create_task(_worker())
