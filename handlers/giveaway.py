"""Создание и проведение конкурсов (раздел 4.6 ТЗ). Только для админов/модераторов.

Диалог в личке: канал условия → текст → число победителей → время финала →
канал публикации. Бот постит пост с кнопкой «Участвовать». Нажатие кнопки
проверяет подписку на канал-условие и регистрирует участника.
"""

import logging

from aiogram import F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from database import crud
from database.engine import session_factory
from keyboards.chat_picker import build_chat_picker
from services import giveaway as gv
from utils.datetime_parse import parse_publish_time, to_local_str

logger = logging.getLogger(__name__)
router = Router(name="giveaway")


# Статусы, означающие «подписан на канал»
_SUBSCRIBED = (
    ChatMemberStatus.MEMBER,
    ChatMemberStatus.ADMINISTRATOR,
    ChatMemberStatus.CREATOR,
)


class NewGiveaway(StatesGroup):
    require_channel = State()
    title = State()
    winners = State()
    when = State()
    post_channel = State()


def _cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="gv:cancel_fsm")]]
    )


def _participate_kb(giveaway_id: int, count: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"🎉 Участвовать ({count})",
                    callback_data=f"gv:join:{giveaway_id}",
                )
            ]
        ]
    )


@router.message(Command("newgiveaway"))
async def cmd_newgiveaway(message: Message, state: FSMContext) -> None:
    if message.chat.type != "private":
        await message.answer("Конкурс создаётся в личке со мной: /newgiveaway в ЛС.")
        return
    await state.clear()
    await state.set_state(NewGiveaway.require_channel)

    kb, shown = await build_chat_picker(
        message.bot,
        message.from_user.id,
        callback_prefix="gvreq",
        only_type="channel",
        show_manual=True,
    )
    # Добавим кнопку «без условия» поверх пикера
    from aiogram.types import InlineKeyboardButton

    kb.inline_keyboard.insert(
        0, [InlineKeyboardButton(text="🚫 Без обязательной подписки", callback_data="gvreq:0")]
    )

    await message.answer(
        "🎯 <b>Создаём конкурс</b>\n\n"
        "Шаг 1. Канал, подписка на который обязательна для участия.\n"
        "Выберите канал или «без условия»:",
        reply_markup=kb,
    )


@router.callback_query(F.data == "gv:cancel_fsm")
async def cb_cancel_fsm(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Создание конкурса отменено.")
    await callback.answer()


@router.callback_query(NewGiveaway.require_channel, F.data.startswith("gvreq:"))
async def cb_require_channel(callback: CallbackQuery, state: FSMContext) -> None:
    """Канал-условие выбран кнопкой (или 0 = без условия)."""
    chat_id = int(callback.data.split(":")[1])
    if chat_id == 0:
        await state.update_data(require_channel_id=0, require_channel_title="")
    else:
        async with session_factory() as session:
            ch = await crud.get_managed_chat(session, chat_id)
        await state.update_data(
            require_channel_id=chat_id,
            require_channel_title=(ch.title if ch else str(chat_id)),
        )
    await state.set_state(NewGiveaway.title)
    await callback.message.edit_text(
        "Шаг 2. Пришлите текст конкурса (что разыгрываем, условия).",
        reply_markup=_cancel_kb(),
    )
    await callback.answer()


@router.callback_query(NewGiveaway.require_channel, F.data == "gvreq_manual")
async def cb_require_manual(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.message.edit_text(
        "Пришлите @username канала-условия (бот должен быть его админом).",
        reply_markup=_cancel_kb(),
    )
    await callback.answer()


@router.message(NewGiveaway.require_channel)
async def step_require_channel(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if raw == "-":
        await state.update_data(require_channel_id=0, require_channel_title="")
    else:
        target = raw if raw.startswith("@") else "@" + raw.lstrip("@")
        try:
            chat = await message.bot.get_chat(target)
            member = await message.bot.get_chat_member(chat.id, message.bot.id)
            if member.status not in ("administrator", "creator"):
                await message.answer("Я не админ этого канала. Добавьте меня и пришлите снова.")
                return
        except Exception as e:
            logger.warning("Канал условия не найден: %s", e)
            await message.answer("Не нашёл канал. Проверьте @username и что бот в нём админ.")
            return
        await state.update_data(
            require_channel_id=chat.id,
            require_channel_title=chat.title or target,
        )
    await state.set_state(NewGiveaway.title)
    await message.answer(
        "Шаг 2. Пришлите текст конкурса (что разыгрываем, условия и т.п.).",
        reply_markup=_cancel_kb(),
    )


@router.message(NewGiveaway.title)
async def step_title(message: Message, state: FSMContext) -> None:
    text = message.html_text if message.text else ""
    if not text:
        await message.answer("Пришлите текстом описание конкурса.")
        return
    await state.update_data(title=text)
    await state.set_state(NewGiveaway.winners)
    await message.answer(
        "Шаг 3. Сколько будет победителей? Пришлите число (например 3).",
        reply_markup=_cancel_kb(),
    )


@router.message(NewGiveaway.winners)
async def step_winners(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit() or int(raw) < 1:
        await message.answer("Пришлите целое число не меньше 1.")
        return
    await state.update_data(winners=int(raw))
    await state.set_state(NewGiveaway.when)
    await message.answer(
        "Шаг 4. Когда подвести итоги?\n"
        "Формат: <code>ДД.ММ.ГГГГ ЧЧ:ММ</code> (время МСК), например 31.12.2025 20:00.",
        reply_markup=_cancel_kb(),
    )


@router.message(NewGiveaway.when)
async def step_when(message: Message, state: FSMContext) -> None:
    finish_at = parse_publish_time(message.text or "")
    if finish_at is None:
        await message.answer("Не разобрал время или оно в прошлом. Формат: ДД.ММ.ГГГГ ЧЧ:ММ.")
        return
    await state.update_data(finish_at=finish_at.isoformat())
    await state.set_state(NewGiveaway.post_channel)

    kb, shown = await build_chat_picker(
        message.bot,
        message.from_user.id,
        callback_prefix="gvpost",
        only_type="channel",
        show_manual=True,
    )
    await message.answer(
        "Шаг 5. Куда опубликовать конкурс? Выберите канал:",
        reply_markup=kb,
    )

async def _finalize_giveaway(message: Message, state: FSMContext, chat, bot, created_by: int) -> None:
    """Создаёт конкурс в БД и публикует пост. message — для ответа пользователю."""
    from datetime import datetime

    data = await state.get_data()
    finish_at = datetime.fromisoformat(data["finish_at"])

    async with session_factory() as session:
        g = await crud.create_giveaway(
            session,
            title=data["title"],
            winners_count=data["winners"],
            require_channel_id=data.get("require_channel_id", 0),
            require_channel_title=data.get("require_channel_title", ""),
            finish_at=finish_at,
            created_by=created_by
        )

    cond = ""
    if data.get("require_channel_id"):
        cond = f"\n\n📌 Условие: подписка на {data['require_channel_title']}"
    post_text = (
        f"{data['title']}{cond}\n\n"
        f"🏆 Победителей: {data['winners']}\n"
        f"⏰ Итоги: {to_local_str(finish_at)} (МСК)"
    )
    sent = await bot.send_message(chat.id, post_text, reply_markup=_participate_kb(g.id, 0))
    async with session_factory() as session:
        await crud.set_giveaway_post(session, g.id, chat.id, sent.message_id)
    gv.schedule_giveaway_finish(bot, g.id, finish_at)

    await state.clear()
    await message.answer(
        f"✅ Конкурс #{g.id} опубликован в <b>{chat.title or chat.id}</b>.\n"
        f"Итоги в {to_local_str(finish_at)} (МСК).\n\n"
        f"Досрочно: /endgiveaway {g.id}"
    )


@router.callback_query(NewGiveaway.post_channel, F.data.startswith("gvpost:"))
async def cb_post_channel(callback: CallbackQuery, state: FSMContext) -> None:
    chat_id = int(callback.data.split(":")[1])
    try:
        chat = await callback.bot.get_chat(chat_id)
    except Exception:
        await callback.answer("Не удалось открыть канал.", show_alert=True)
        return
    await callback.message.edit_text("Публикую конкурс…")
    await _finalize_giveaway(
        callback.message,
        state,
        chat,
        callback.bot,
        created_by=callback.from_user.id,
    )
    await callback.answer()


@router.callback_query(NewGiveaway.post_channel, F.data == "gvpost_manual")
async def cb_post_manual(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.message.edit_text(
        "Пришлите @username канала публикации.",
        reply_markup=_cancel_kb(),
    )
    await callback.answer()


@router.message(NewGiveaway.post_channel)
async def step_post_channel(message: Message, state: FSMContext) -> None:
    """Ручной ввод канала публикации."""
    raw = (message.text or "").strip()
    if not raw:
        await message.answer("Пришлите @username или id канала.")
        return
    target = raw if raw.startswith("@") else (int(raw) if raw.lstrip("-").isdigit() else "@" + raw)
    try:
        chat = await message.bot.get_chat(target)
        member = await message.bot.get_chat_member(chat.id, message.bot.id)
        if member.status not in ("administrator", "creator"):
            await message.answer("Я не админ этого канала. Добавьте меня и пришлите снова.")
            return
    except Exception as e:
        logger.warning("Канал публикации не найден: %s", e)
        await message.answer("Не нашёл канал. Проверьте данные и права бота.")
        return
    await _finalize_giveaway(message, state, chat, message.bot, created_by=message.from_user.id)


@router.message(Command("endgiveaway"))
async def cmd_endgiveaway(message: Message) -> None:
    """Досрочное завершение конкурса по id."""
    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Формат: /endgiveaway <id>")
        return
    giveaway_id = int(parts[1])
    # Снимаем таймер, чтобы не сработал повторно
    try:
        gv.scheduler.remove_job(f"giveaway:{giveaway_id}")
    except Exception:
        pass
    await gv.finish_giveaway(message.bot, giveaway_id)
    await message.answer(f"Конкурс #{giveaway_id} завершён.")


@router.callback_query(F.data.startswith("gv:join:"))
async def cb_join(callback: CallbackQuery) -> None:
    """Нажатие «Участвовать»: проверка подписки и регистрация."""
    giveaway_id = int(callback.data.split(":")[2])
    user = callback.from_user

    async with session_factory() as session:
        g = await crud.get_giveaway(session, giveaway_id)

    if g is None or g.status != "active":
        await callback.answer("Конкурс уже завершён.", show_alert=True)
        return

    # Проверка обязательной подписки на канал
    if g.require_channel_id:
        try:
            member = await callback.bot.get_chat_member(g.require_channel_id, user.id)
            if member.status not in _SUBSCRIBED:
                await callback.answer(
                    f"Сначала подпишитесь на {g.require_channel_title}, "
                    f"затем нажмите «Участвовать» снова.",
                    show_alert=True,
                )
                return
        except Exception as e:
            logger.warning("Проверка подписки не удалась: %s", e)
            await callback.answer(
                "Не удалось проверить подписку. Попробуйте позже.",
                show_alert=True,
            )
            return

    # Регистрируем участника
    async with session_factory() as session:
        added = await crud.add_participant(
            session,
            giveaway_id,
            user.id,
            user.full_name or "",
            user.username or "",
        )
        count = await crud.count_participants(session, giveaway_id)

    if added:
        await callback.answer("Вы участвуете! Удачи 🍀")
        # Обновляем счётчик на кнопке
        try:
            await callback.message.edit_reply_markup(
                reply_markup=_participate_kb(giveaway_id, count)
            )
        except Exception:
            pass
    else:
        await callback.answer("Вы уже участвуете в этом конкурсе.", show_alert=True)
