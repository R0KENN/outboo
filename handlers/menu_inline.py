"""Главное инлайн-меню бота и список управляемых чатов (личка).

Вся навигация — на inline-кнопках. /start открывает меню, убирает старую
reply-клавиатуру и регистрирует подписчика/реферала.
"""

import logging

from aiogram import F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardRemove,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import settings
from database.crud import (
    get_managed_chat,
    get_or_create_chat_settings,
    list_managed_chats,
)
from database.engine import session_factory
from keyboards.settings_kb import main_settings_kb

logger = logging.getLogger(__name__)
router = Router(name="menu_inline")

_bot_username: str | None = None


async def _get_bot_username(bot) -> str:
    """Username бота для deep-link, кэшируется после первого запроса."""
    global _bot_username
    if _bot_username is None:
        me = await bot.get_me()
        _bot_username = me.username or ""
    return _bot_username


# ─────────────────────────── вспомогательные ───────────────────────────
def _is_global_admin(user_id: int) -> bool:
    return user_id in settings.admin_ids


async def _is_chat_admin(bot, chat_id: int, user_id: int) -> bool:
    """Проверяет, админ ли пользователь в конкретном чате/канале."""
    if _is_global_admin(user_id):
        return True
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in (
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.CREATOR,
        )
    except Exception:
        return False


def _chat_icon(chat_type: str) -> str:
    return "📢" if chat_type == "channel" else "👥"


def _as_user_message(callback: CallbackQuery) -> Message:
    """Копия сообщения с подменённым отправителем — на реального пользователя.

    callback.message.from_user — это бот; командам (/ref, /broadcast и т.п.)
    нужен id того, кто нажал кнопку.
    """
    msg = callback.message.model_copy(update={"from_user": callback.from_user})
    # Подстраховка: если подмена не сработала (изменилось поведение pydantic),
    # явно проставляем from_user, чтобы не уйти с id бота.
    if msg.from_user is None or msg.from_user.id != callback.from_user.id:
        try:
            object.__setattr__(msg, "from_user", callback.from_user)
        except Exception:
            logger.warning("Не удалось подменить from_user в callback.message")
    return msg


# ─────────────────────────── клавиатуры ───────────────────────────
def home_kb(user_id: int) -> InlineKeyboardMarkup:
    """Главное инлайн-меню. Владельцам бота — дополнительные пункты."""
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="🗂 Мои чаты и каналы", callback_data="menu:chats"))
    b.row(
        InlineKeyboardButton(text="📢 Опубликовать в каналы", callback_data="menu:newpost"),
        InlineKeyboardButton(text="📋 Очередь", callback_data="menu:queue"),
    )
    b.row(InlineKeyboardButton(text="🎉 Конкурс", callback_data="menu:giveaway"))
    if _is_global_admin(user_id):
        b.row(
            InlineKeyboardButton(text="📨 Рассылка", callback_data="menu:broadcast"),
            InlineKeyboardButton(text="👥 Подписчики", callback_data="menu:subs"),
        )
        b.row(InlineKeyboardButton(text="📊 Экспорт", callback_data="menu:export"))
    b.row(InlineKeyboardButton(text="ℹ️ Помощь", callback_data="menu:help"))
    return b.as_markup()


def chats_list_kb(chats) -> InlineKeyboardMarkup:
    """Список чатов: каждая кнопка ведёт в карточку чата."""
    b = InlineKeyboardBuilder()
    for ch in chats:
        title = ch.title or str(ch.chat_id)
        admin_mark = "" if ch.is_admin else " ⚠️"
        b.row(
            InlineKeyboardButton(
                text=f"{_chat_icon(ch.chat_type)} {title}{admin_mark}",
                callback_data=f"menu:open:{ch.chat_id}",
            )
        )
    b.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:home"))
    return b.as_markup()


# ─────────────────────────── /start и /menu ───────────────────────────
@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    """Точка входа в личке — убирает reply-меню и показывает инлайн-меню."""
    if message.chat.type != "private":
        return

    # Регистрация подписчика/реферала (перенесено из старого start.py)
    if message.from_user:
        from database import crud

        user_id = message.from_user.id
        parts = (message.text or "").split(maxsplit=1)
        payload = parts[1].strip() if len(parts) > 1 else ""

        async with session_factory() as session:
            await crud.upsert_subscriber(
                session,
                user_id,
                message.from_user.username or "",
                message.from_user.full_name or "",
            )
            if payload.startswith("src_"):
                raw = payload[4:]
                if raw.lstrip("-").isdigit():
                    await crud.set_subscriber_source(session, user_id, int(raw))
            if payload.startswith("ref_"):
                raw = payload[4:]
                if raw.isdigit():
                    referrer_id = int(raw)
                    ok = await crud.register_referral(session, user_id, referrer_id)
                    if ok:
                        try:
                            await message.bot.send_message(
                                referrer_id,
                                f"🎉 По вашей ссылке пришёл новый пользователь: "
                                f"{message.from_user.full_name}",
                            )
                        except Exception:
                            pass

    # Убираем старую нижнюю reply-клавиатуру, если она осталась от прошлой версии
    cleanup = await message.answer("Загружаю меню…", reply_markup=ReplyKeyboardRemove())
    try:
        await cleanup.delete()
    except Exception:
        pass

    await message.answer(
        "👋 <b>Главное меню</b>\nВыберите раздел:",
        reply_markup=home_kb(message.from_user.id),
    )


@router.message(Command("menu"))
async def cmd_menu(message: Message) -> None:
    """Альтернативный вызов главного меню."""
    if message.chat.type != "private":
        return
    await message.answer(
        "👋 <b>Главное меню</b>\nВыберите раздел:",
        reply_markup=home_kb(message.from_user.id),
    )


# ─────────────────────────── навигация ───────────────────────────
@router.callback_query(F.data == "menu:home")
async def on_home(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        "👋 <b>Главное меню</b>\nВыберите раздел:",
        reply_markup=home_kb(callback.from_user.id),
    )
    await callback.answer()


@router.callback_query(F.data == "menu:chats")
async def on_chats(callback: CallbackQuery) -> None:
    """Список чатов/каналов, где есть бот."""
    user_id = callback.from_user.id

    async with session_factory() as session:
        all_chats = await list_managed_chats(session, only_active=True)

    visible = [ch for ch in all_chats if ch.added_by == user_id]

    if not visible:
        await callback.message.edit_text(
            "У вас пока нет подключённых чатов или каналов.\n\n"
            "Добавьте бота в группу/канал и назначьте администратором — "
            "он появится здесь автоматически.",
            reply_markup=chats_list_kb([]),
        )
        await callback.answer()
        return

    await callback.message.edit_text(
        "🗂 <b>Ваши чаты и каналы</b>\nНажмите, чтобы настроить выбранный:",
        reply_markup=chats_list_kb(visible),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("menu:open:"))
async def on_open_chat(callback: CallbackQuery) -> None:
    """Карточка конкретного чата с его индивидуальными настройками."""
    chat_id = int(callback.data.split(":")[2])

    user_id = callback.from_user.id
    async with session_factory() as session:
        _mc = await get_managed_chat(session, chat_id)
    owns = _mc is not None and _mc.added_by == user_id
    if not _is_global_admin(user_id) and not owns:
        await callback.answer("Нет доступа к этому чату.", show_alert=True)
        return

    async with session_factory() as session:
        ch = await get_managed_chat(session, chat_id)
        cfg = await get_or_create_chat_settings(session, chat_id)

    title = ch.title if ch else str(chat_id)
    chat_type = ch.chat_type if ch else "group"
    icon = _chat_icon(chat_type)
    kind = "канала" if chat_type == "channel" else "чата"

    kb = main_settings_kb(cfg, chat_type)
    # Для каналов добавляем кнопку с реферальной ссылкой набора подписчиков
    if chat_type == "channel":
        b = InlineKeyboardBuilder.from_markup(kb)
        b.row(
            InlineKeyboardButton(
                text="🔗 Ссылка для набора подписчиков",
                callback_data=f"menu:reflink:{chat_id}",
            )
        )
        kb = b.as_markup()

    await callback.message.edit_text(
        f"{icon} <b>{title}</b>\nИндивидуальные настройки {kind}:",
        reply_markup=kb,
    )
    await callback.answer()

@router.callback_query(F.data.startswith("menu:reflink:"))
async def on_reflink(callback: CallbackQuery) -> None:
    """Показывает deep-link для набора подписчиков через конкретный канал."""
    chat_id = int(callback.data.split(":")[2])

    # Доступ — владелец бота или тот, кто добавил этот чат
    user_id = callback.from_user.id
    async with session_factory() as session:
        ch = await get_managed_chat(session, chat_id)
    owns = ch is not None and ch.added_by == user_id
    if not _is_global_admin(user_id) and not owns:
        await callback.answer("Нет доступа к этому чату.", show_alert=True)
        return

    username = await _get_bot_username(callback.bot)
    if not username:
        await callback.answer("Не удалось получить имя бота.", show_alert=True)
        return

    link = f"https://t.me/{username}?start=src_{chat_id}"
    title = ch.title if ch else str(chat_id)

    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"menu:open:{chat_id}"))

    await callback.message.edit_text(
        f"🔗 <b>Ссылка для набора подписчиков</b>\n"
        f"Канал: <b>{title}</b>\n\n"
        f"Раздавайте эту ссылку. Каждый, кто перейдёт по ней и запустит бота, "
        f"будет помечен как пришедший через этот канал — и попадёт в его сегмент "
        f"при рассылке.\n\n"
        f"<code>{link}</code>\n\n"
        f"<i>Нажмите на ссылку, чтобы скопировать.</i>",
        reply_markup=b.as_markup(),
    )
    await callback.answer()


# ─────────────────────────── разделы (переиспользование команд) ───────────────────────────
@router.callback_query(F.data == "menu:newpost")
async def on_newpost_start(callback: CallbackQuery, state: FSMContext) -> None:
    """Создание поста: выбор каналов кнопками."""
    from handlers.posting import start_channel_choice

    await start_channel_choice(
        callback.message,
        state,
        user_id=callback.from_user.id,
        edit=True,
    )
    await callback.answer()


@router.callback_query(F.data == "menu:queue")
async def on_queue(callback: CallbackQuery) -> None:
    """Очередь запланированных постов."""
    from handlers.posting import cmd_queue

    await cmd_queue(_as_user_message(callback))
    await callback.answer()


@router.callback_query(F.data == "menu:giveaway")
async def on_giveaway(callback: CallbackQuery, state: FSMContext) -> None:
    """Создание конкурса (FSM-диалог)."""
    from handlers.giveaway import cmd_newgiveaway

    await cmd_newgiveaway(_as_user_message(callback), state)
    await callback.answer()


@router.callback_query(F.data == "menu:broadcast")
async def on_broadcast(callback: CallbackQuery, state: FSMContext) -> None:
    """Рассылка по подписчикам бота (только владельцы)."""
    if not _is_global_admin(callback.from_user.id):
        await callback.answer("Только для владельцев бота.", show_alert=True)
        return
    from handlers.broadcast import cmd_broadcast

    await cmd_broadcast(_as_user_message(callback), state)
    await callback.answer()


@router.callback_query(F.data == "menu:subs")
async def on_subs(callback: CallbackQuery) -> None:
    """Размер базы подписчиков (только владельцы)."""
    if not _is_global_admin(callback.from_user.id):
        await callback.answer("Только для владельцев бота.", show_alert=True)
        return
    from handlers.broadcast import cmd_subs

    await cmd_subs(_as_user_message(callback))
    await callback.answer()


@router.callback_query(F.data == "menu:export")
async def on_export(callback: CallbackQuery) -> None:
    """Меню экспорта в Google Sheets (только владельцы)."""
    if not _is_global_admin(callback.from_user.id):
        await callback.answer("Только для владельцев бота.", show_alert=True)
        return
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="📤 Выгрузить всё", callback_data="menu:export_run"))
    b.row(InlineKeyboardButton(text="🔌 Проверить подключение", callback_data="menu:export_test"))
    b.row(InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:home"))
    await callback.message.edit_text(
        "📊 <b>Экспорт в Google Sheets</b>\nВыгружаются подписчики, участники "
        "конкурсов и журнал модерации.",
        reply_markup=b.as_markup(),
    )
    await callback.answer()


@router.callback_query(F.data == "menu:export_run")
async def on_export_run(callback: CallbackQuery) -> None:
    if not _is_global_admin(callback.from_user.id):
        await callback.answer("Только для владельцев бота.", show_alert=True)
        return
    from handlers.sheets import cmd_export

    await cmd_export(_as_user_message(callback))
    await callback.answer()


@router.callback_query(F.data == "menu:export_test")
async def on_export_test(callback: CallbackQuery) -> None:
    if not _is_global_admin(callback.from_user.id):
        await callback.answer("Только для владельцев бота.", show_alert=True)
        return
    from handlers.sheets import cmd_sheettest

    await cmd_sheettest(_as_user_message(callback))
    await callback.answer()


@router.callback_query(F.data == "menu:help")
async def on_help(callback: CallbackQuery) -> None:
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:home"))
    await callback.message.edit_text(
        "ℹ️ <b>Как пользоваться</b>\n\n"
        "1. Добавьте бота в группу или канал и сделайте администратором.\n"
        "2. Откройте «🗂 Мои чаты и каналы» — выберите нужный.\n"
        "3. Настройте фильтры, автореакции и приём заявок индивидуально.\n\n"
        "«Опубликовать в каналы» — пост сразу в несколько каналов "
        "(сейчас или по расписанию).\n"
        "«Очередь» — запланированные посты.",
        reply_markup=b.as_markup(),
    )
    await callback.answer()
