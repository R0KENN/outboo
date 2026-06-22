"""Модуль автопостинга (раздел 4.3 ТЗ).

Диалог создания отложенного поста через FSM, мультивыбор каналов, очередь,
отмена, перенос, публикация «сейчас». Создание ведётся в личке с ботом.
Целевые каналы выбираются кнопками из реестра managed_chats.
"""

import json
import logging
import uuid
from datetime import UTC

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
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import settings as app_settings
from database import crud
from database.crud import get_managed_chat, list_managed_chats
from database.engine import session_factory
from services import scheduler as sched
from services.scheduler import _build_keyboard, _parse_media
from utils.datetime_parse import parse_publish_time, to_local_str
from utils.pagination import nav_row, paginate

logger = logging.getLogger(__name__)
router = Router(name="posting")


def _as_user_message(callback, message):
    """Возвращает message с from_user реального пользователя (не бота)."""
    msg = message.model_copy(update={"from_user": callback.from_user})
    if msg.from_user is None or msg.from_user.id != callback.from_user.id:
        try:
            object.__setattr__(msg, "from_user", callback.from_user)
        except Exception:
            pass
    return msg


class NewPost(StatesGroup):
    """Шаги диалога создания поста."""

    channel = State()
    content = State()
    buttons = State()
    when = State()
    delete_after = State()
    preview = State()  # ← добавить


def _cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="post:cancel_fsm")]]
    )


class RescheduleFSM(StatesGroup):
    """Ожидание новой даты при переносе из карточки очереди."""

    waiting_time = State()  # перенос одного поста
    waiting_time_batch = State()  # перенос всей группы


class EditPostFSM(StatesGroup):
    waiting_content = State()


# ──────────────────────────────────────────────────────────────────────────
# Выбор каналов (мультивыбор из managed_chats)
# ──────────────────────────────────────────────────────────────────────────
async def _channel_choice_kb(bot, user_id: int, is_global_admin: bool, selected: set[int]):
    """Клавиатура мультивыбора каналов. Возвращает (markup, число_доступных)."""
    async with session_factory() as session:
        chats = await list_managed_chats(session, only_active=True)

    b = InlineKeyboardBuilder()
    shown = 0
    for ch in chats:
        if ch.chat_type != "channel" or not ch.is_admin:
            continue
        if not is_global_admin:
            try:
                m = await bot.get_chat_member(ch.chat_id, user_id)
                if m.status not in (
                    ChatMemberStatus.ADMINISTRATOR,
                    ChatMemberStatus.CREATOR,
                ):
                    continue
            except Exception:
                continue
        title = ch.title or str(ch.chat_id)
        mark = "✅ " if ch.chat_id in selected else "▫️ "
        b.row(
            InlineKeyboardButton(
                text=f"{mark}📢 {title}",
                callback_data=f"post:toggle:{ch.chat_id}",
            )
        )
        shown += 1

    b.row(
        InlineKeyboardButton(
            text=f"➡️ Готово ({len(selected)})",
            callback_data="post:done",
        )
    )
    b.row(
        InlineKeyboardButton(
            text="✍️ Ввести канал вручную",
            callback_data="post:manual",
        )
    )
    b.row(InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:home"))
    return b.as_markup(), shown


async def start_channel_choice(
    message: Message,
    state: FSMContext,
    user_id: int,
    edit: bool = False,
) -> None:
    """Стартер диалога: чистит состояние и показывает мультивыбор каналов."""
    is_global_admin = user_id in app_settings.admin_ids

    await state.clear()
    await state.set_state(NewPost.channel)
    await state.update_data(channel_ids=[], is_global_admin=is_global_admin)

    kb, shown = await _channel_choice_kb(message.bot, user_id, is_global_admin, set())

    if shown == 0:
        text = (
            "📢 <b>Создание поста</b>\n\n"
            "У меня пока нет каналов, куда можно постить.\n"
            "Добавьте бота в канал администратором — он появится в списке.\n\n"
            "Либо введите канал вручную."
        )
    else:
        text = "📢 <b>Куда публикуем?</b>\nОтметьте один или несколько каналов и нажмите «Готово»:"

    if edit:
        try:
            await message.edit_text(text, reply_markup=kb)
            return
        except Exception:
            pass
    await message.answer(text, reply_markup=kb)


# ──────────────────────────────────────────────────────────────────────────
# Создание поста (FSM)
# ──────────────────────────────────────────────────────────────────────────
@router.message(Command("newpost"))
async def cmd_newpost(message: Message, state: FSMContext) -> None:
    """Запускает диалог создания поста (в личке с ботом)."""
    if message.chat.type != "private":
        await message.answer("Создавать посты удобнее в личке со мной: напишите /newpost мне в ЛС.")
        return
    await start_channel_choice(message, state, user_id=message.from_user.id)


@router.callback_query(F.data == "post:cancel_fsm")
async def cb_cancel_fsm(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Создание поста отменено.")
    await callback.answer()


@router.callback_query(NewPost.channel, F.data.startswith("post:toggle:"))
async def cb_toggle_channel(callback: CallbackQuery, state: FSMContext) -> None:
    """Добавляет/убирает канал из выбора (галочка)."""
    chat_id = int(callback.data.split(":")[2])
    data = await state.get_data()
    selected = set(data.get("channel_ids", []))

    if chat_id in selected:
        selected.discard(chat_id)
    else:
        selected.add(chat_id)
    await state.update_data(channel_ids=list(selected))

    kb, _ = await _channel_choice_kb(
        callback.bot,
        callback.from_user.id,
        data.get("is_global_admin", False),
        selected,
    )
    try:
        await callback.message.edit_reply_markup(reply_markup=kb)
    except Exception:
        pass
    await callback.answer()


@router.callback_query(NewPost.channel, F.data == "post:done")
async def cb_done_channels(callback: CallbackQuery, state: FSMContext) -> None:
    """Подтверждает выбор каналов и переходит к содержимому."""
    data = await state.get_data()
    selected = list(data.get("channel_ids", []))
    if not selected:
        await callback.answer("Выберите хотя бы один канал.", show_alert=True)
        return

    valid_ids: list[int] = []
    titles: list[str] = []
    async with session_factory() as session:
        for chat_id in selected:
            try:
                member = await callback.bot.get_chat_member(chat_id, callback.bot.id)
                if member.status not in ("administrator", "creator"):
                    continue
            except Exception:
                continue
            ch = await get_managed_chat(session, chat_id)
            valid_ids.append(chat_id)
            titles.append((ch.title if ch else None) or str(chat_id))

    if not valid_ids:
        await callback.answer(
            "Ни в одном из выбранных каналов нет прав. Проверьте бота.",
            show_alert=True,
        )
        return

    await state.update_data(channel_ids=valid_ids, channel_titles=titles)
    await state.set_state(NewPost.content)
    await callback.message.edit_text(
        f"Каналов выбрано: <b>{len(valid_ids)}</b>\n{', '.join(titles)}\n\n"
        "Теперь пришлите содержимое поста: текст, фото, видео, документ "
        "или альбом. Форматирование сохраняется.",
        reply_markup=_cancel_kb(),
    )
    await callback.answer()


@router.callback_query(NewPost.channel, F.data == "post:manual")
async def cb_manual_channel(callback: CallbackQuery, state: FSMContext) -> None:
    """Переключение на ручной ввод @username/id канала."""
    await callback.message.edit_text(
        "✍️ Пришлите @username канала или его числовой id.\n\n"
        "Важно: бот должен быть администратором этого канала.",
        reply_markup=_cancel_kb(),
    )
    await callback.answer()


@router.message(NewPost.channel)
async def step_channel(message: Message, state: FSMContext) -> None:
    """Ручной ввод канала: проверяет права бота и сохраняет его."""
    raw = (message.text or "").strip()
    if not raw:
        await message.answer("Пришлите @username или id канала.")
        return

    if raw.startswith("@"):
        target = raw
    else:
        try:
            target = int(raw)
        except ValueError:
            target = "@" + raw.lstrip("@")

    try:
        chat = await message.bot.get_chat(target)
        member = await message.bot.get_chat_member(chat.id, message.bot.id)
        if member.status not in ("administrator", "creator"):
            await message.answer(
                "Я не админ этого канала. Добавьте меня администратором и пришлите канал ещё раз."
            )
            return
    except Exception as e:
        logger.warning("Проверка канала не удалась: %s", e)
        await message.answer("Не получилось найти канал. Проверьте @username/id и что бот в нём.")
        return

    await state.update_data(
        channel_ids=[chat.id],
        channel_titles=[chat.title or str(chat.id)],
    )
    await state.set_state(NewPost.content)
    await message.answer(
        f"Канал принят: <b>{chat.title or chat.id}</b>\n\n"
        "Теперь пришлите содержимое поста: текст, фото, видео, документ "
        "или альбом. Форматирование сохраняется.",
        reply_markup=_cancel_kb(),
    )


# ──────────────────────────────────────────────────────────────────────────
# Контент
# ──────────────────────────────────────────────────────────────────────────
def _extract_media(message: Message) -> list[dict]:
    """Извлекает медиа из сообщения в формат [{"type","file_id"}]."""
    if message.photo:
        return [{"type": "photo", "file_id": message.photo[-1].file_id}]
    if message.video:
        return [{"type": "video", "file_id": message.video.file_id}]
    if message.document:
        return [{"type": "document", "file_id": message.document.file_id}]
    if message.audio:
        return [{"type": "audio", "file_id": message.audio.file_id}]
    if message.animation:
        return [{"type": "video", "file_id": message.animation.file_id}]
    return []


_album_buffer: dict[str, list[dict]] = {}


@router.message(NewPost.content, F.media_group_id)
async def step_content_album(message: Message, state: FSMContext) -> None:
    """Собирает элементы альбома (приходят отдельными сообщениями)."""
    import asyncio

    mgid = message.media_group_id
    items = _extract_media(message)
    if items:
        _album_buffer.setdefault(mgid, []).extend(items)

    caption = message.html_text if (message.caption or message.text) else ""
    if caption:
        data = await state.get_data()
        if not data.get("text"):
            await state.update_data(text=caption)

    async def _finalize(group_id: str):
        await asyncio.sleep(1.0)
        media = _album_buffer.pop(group_id, [])
        if not media:
            return
        cur = await state.get_state()
        if cur != NewPost.content.state:
            return
        await state.update_data(media=json.dumps(media, ensure_ascii=False))
        await state.set_state(NewPost.buttons)
        await message.answer(
            f"Принято медиа в альбоме: {len(media)} шт.\n\n"
            "Добавить inline-кнопки? Формат:\n"
            "<code>Текст кнопки - https://ссылка</code>\n"
            "Каждая с новой строки. Или «-», чтобы пропустить.",
            reply_markup=_cancel_kb(),
        )

    asyncio.create_task(_finalize(mgid))


@router.message(NewPost.content)
async def step_content_single(message: Message, state: FSMContext) -> None:
    """Принимает одиночное сообщение (текст или одно медиа)."""
    media = _extract_media(message)
    if media:
        text = message.html_text if message.caption else ""
    else:
        text = message.html_text if message.text else ""

    if not media and not text:
        await message.answer("Пришлите текст или медиа для поста.")
        return

    await state.update_data(
        text=text or "",
        media=json.dumps(media, ensure_ascii=False) if media else "",
    )
    await state.set_state(NewPost.buttons)
    await message.answer(
        "Добавить inline-кнопки? Формат:\n"
        "<code>Текст кнопки - https://ссылка</code>\n"
        "Каждая с новой строки. Или «-», чтобы пропустить.",
        reply_markup=_cancel_kb(),
    )


def _parse_buttons(text: str) -> str:
    """Парсит ввод кнопок в JSON [[{"text","url"}], ...]."""
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line or "-" not in line:
            continue
        label, _, url = line.partition("-")
        label, url = label.strip(), url.strip()
        if label and url.startswith("http"):
            rows.append([{"text": label, "url": url}])
    return json.dumps(rows, ensure_ascii=False) if rows else ""


@router.message(NewPost.buttons)
async def step_buttons(message: Message, state: FSMContext) -> None:
    """Принимает кнопки или пропуск, затем спрашивает время."""
    raw = (message.text or "").strip()
    buttons = "" if raw == "-" else _parse_buttons(raw)
    await state.update_data(buttons=buttons)
    await state.set_state(NewPost.when)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Опубликовать сейчас", callback_data="post:now")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="post:cancel_fsm")],
        ]
    )
    await message.answer(
        "Когда опубликовать?\n"
        "Пришлите дату и время <code>ДД.ММ.ГГГГ ЧЧ:ММ</code> (МСК)\n"
        "например 25.12.2025 18:30,\n\n"
        "или нажмите «Опубликовать сейчас».",
        reply_markup=kb,
    )


# ──────────────────────────────────────────────────────────────────────────
# Время и финал
# ──────────────────────────────────────────────────────────────────────────
@router.callback_query(NewPost.when, F.data == "post:now")
async def cb_publish_now(callback: CallbackQuery, state: FSMContext) -> None:
    """Публикует пост немедленно."""
    from datetime import datetime

    publish_at = datetime.now(UTC)
    await state.update_data(publish_at=publish_at.isoformat())
    await state.set_state(NewPost.delete_after)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Не удалять", callback_data="post:nodel")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="post:cancel_fsm")],
        ]
    )
    await callback.message.edit_text(
        "Публикуем сейчас. Удалить пост автоматически через время?\n"
        "Пришлите число минут или нажмите «Не удалять».",
        reply_markup=kb,
    )
    await callback.answer()


@router.message(NewPost.when)
async def step_when(message: Message, state: FSMContext) -> None:
    """Принимает время публикации."""
    publish_at = parse_publish_time(message.text or "")
    if publish_at is None:
        await message.answer(
            "Не разобрал время или оно в прошлом.\n"
            "Формат: <code>ДД.ММ.ГГГГ ЧЧ:ММ</code>, например 25.12.2025 18:30."
        )
        return
    await state.update_data(publish_at=publish_at.isoformat())
    await state.set_state(NewPost.delete_after)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Не удалять", callback_data="post:nodel")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="post:cancel_fsm")],
        ]
    )
    await message.answer(
        "Удалить пост автоматически через время?\nПришлите число минут или нажмите «Не удалять».",
        reply_markup=kb,
    )


async def _send_preview(bot, chat_id: int, data: dict) -> None:
    """Отправляет пользователю предпросмотр поста ровно так, как он уйдёт в канал."""
    text = data.get("text", "")
    media = _parse_media(data.get("media", ""))
    keyboard = _build_keyboard(data.get("buttons", ""))

    if not media:
        await bot.send_message(chat_id, text or "(пустой пост)", parse_mode="HTML", reply_markup=keyboard)
    elif len(media) == 1:
        item = media[0]
        mtype, file_id = item.get("type"), item.get("file_id")
        common = dict(caption=text or None, parse_mode="HTML", reply_markup=keyboard)
        if mtype == "photo":
            await bot.send_photo(chat_id, file_id, **common)
        elif mtype == "video":
            await bot.send_video(chat_id, file_id, **common)
        elif mtype == "document":
            await bot.send_document(chat_id, file_id, **common)
        elif mtype == "audio":
            await bot.send_audio(chat_id, file_id, **common)
        else:
            await bot.send_message(chat_id, text or "(пост)", parse_mode="HTML", reply_markup=keyboard)
    else:
        from services.scheduler import _INPUT_MEDIA

        group = []
        for i, item in enumerate(media):
            cls = _INPUT_MEDIA.get(item.get("type"))
            if cls is None:
                continue
            kwargs = {"media": item.get("file_id")}
            if i == 0 and text:
                kwargs["caption"] = text
                kwargs["parse_mode"] = "HTML"
            group.append(cls(**kwargs))
        await bot.send_media_group(chat_id, media=group)
        if keyboard:
            await bot.send_message(chat_id, "⬆️ кнопки поста", parse_mode="HTML", reply_markup=keyboard)


async def _finalize_posts(message: Message, state: FSMContext, delete_after: int) -> None:
    """Создаёт записи постов во все выбранные каналы и ставит их в планировщик."""
    from datetime import datetime

    data = await state.get_data()
    publish_at = datetime.fromisoformat(data["publish_at"])
    channel_ids = data.get("channel_ids", [])
    channel_titles = data.get("channel_titles", [])

    if not channel_ids:
        await message.answer("Не выбран ни один канал. Начните заново.")
        await state.clear()
        return

    batch_id = uuid.uuid4().hex
    created_ids: list[int] = []
    async with session_factory() as session:
        for chat_id in channel_ids:
            post = await crud.create_scheduled_post(
                session,
                channel_id=chat_id,
                text=data.get("text", ""),
                media=data.get("media", ""),
                buttons=data.get("buttons", ""),
                parse_mode="HTML",
                publish_at=publish_at,
                delete_after=delete_after,
                created_by=message.from_user.id,
                batch_id=batch_id,
            )
            created_ids.append(post.id)

    for post_id in created_ids:
        await sched.schedule_post(post_id, publish_at)

    await state.clear()

    del_note = f"\nАвтоудаление через {delete_after // 60} мин." if delete_after else ""
    ids_str = ", ".join(f"#{i}" for i in created_ids)
    chans_str = ", ".join(channel_titles) if channel_titles else str(len(created_ids))
    is_now = (publish_at - datetime.now(UTC)).total_seconds() < 90
    when_str = "сейчас" if is_now else f"{to_local_str(publish_at)} (МСК)"

    await message.answer(
        f"✅ Постов: <b>{len(created_ids)}</b> ({ids_str})\n"
        f"Каналы: <b>{chans_str}</b>\n"
        f"Время: <b>{when_str}</b>{del_note}\n\n"
        f"Очередь: /queue"
    )


@router.callback_query(NewPost.delete_after, F.data == "post:nodel")
async def cb_no_delete(callback: CallbackQuery, state: FSMContext) -> None:
    """Без автоудаления — показываем предпросмотр."""
    await state.update_data(delete_after=0)
    await _show_preview_step(_as_user_message(callback, callback.message), state)
    await callback.answer()


@router.message(NewPost.delete_after)
async def step_delete_after(message: Message, state: FSMContext) -> None:
    """Принимает число минут автоудаления (или «-») и показывает предпросмотр."""
    raw = (message.text or "").strip()
    delete_after = 0
    if raw != "-":
        try:
            delete_after = max(0, int(raw)) * 60
        except ValueError:
            await message.answer("Пришлите число минут или «-».")
            return
    await state.update_data(delete_after=delete_after)
    await _show_preview_step(message, state)


async def _show_preview_step(message: Message, state: FSMContext) -> None:
    """Показывает предпросмотр и кнопки подтверждения."""
    data = await state.get_data()
    await state.set_state(NewPost.preview)

    await message.answer("👀 <b>Предпросмотр поста:</b>")
    try:
        await _send_preview(message.bot, message.chat.id, data)
    except Exception as e:
        await message.answer(f"Не удалось отрисовать превью: {e}")

    titles = data.get("channel_titles", [])
    from datetime import datetime

    publish_at = datetime.fromisoformat(data["publish_at"])
    is_now = (publish_at - datetime.now(UTC)).total_seconds() < 90
    when = "сейчас" if is_now else f"{to_local_str(publish_at)} (МСК)"
    da = data.get("delete_after", 0)
    da_note = f"\nАвтоудаление через {da // 60} мин." if da else ""

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Опубликовать", callback_data="post:confirm")],
            [InlineKeyboardButton(text="✏️ Изменить текст", callback_data="post:edittext")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="post:cancel_fsm")],
        ]
    )
    await message.answer(
        f"Каналы: <b>{', '.join(titles) if titles else '—'}</b>\n"
        f"Время: <b>{when}</b>{da_note}\n\n"
        "Всё верно?",
        reply_markup=kb,
    )


@router.callback_query(NewPost.preview, F.data == "post:confirm")
async def cb_confirm_post(callback: CallbackQuery, state: FSMContext) -> None:
    """Подтверждение — создаём посты."""
    data = await state.get_data()
    await _finalize_posts(
        _as_user_message(callback, callback.message),
        state,
        delete_after=data.get("delete_after", 0),
    )
    await callback.answer()


@router.callback_query(NewPost.preview, F.data == "post:edittext")
async def cb_edit_text(callback: CallbackQuery, state: FSMContext) -> None:
    """Возврат к вводу текста: оставляем медиа/кнопки, ждём новый текст."""
    await state.set_state(NewPost.content)
    await callback.message.answer(
        "Пришлите новый текст (или новое содержимое) поста:",
        reply_markup=_cancel_kb(),
    )
    await callback.answer()


# ──────────────────────────────────────────────────────────────────────────
# Управление очередью
# ──────────────────────────────────────────────────────────────────────────
# ──────────────────────────────────────────────────────────────────────────
# Управление очередью (инлайн-вкладка)
# ──────────────────────────────────────────────────────────────────────────
def _queue_list_kb(groups, page: int = 0) -> InlineKeyboardMarkup:
    """Список запланированных постов кнопками, с пагинацией."""
    page_items, pages, page = paginate(groups, page)

    b = InlineKeyboardBuilder()
    for grp in page_items:
        first = grp[0]
        preview = (first.text or "").replace("\n", " ")[:25] or "(медиа)"
        when = to_local_str(first.publish_at)
        if len(grp) > 1:
            label = f"🗂 {when} · {len(grp)} кан. · {preview}"
            cb = f"q:batch:{first.batch_id}"
        else:
            label = f"📄 {when} · {preview}"
            cb = f"q:post:{first.id}"
        b.row(InlineKeyboardButton(text=label, callback_data=cb))

    nav_row(b, "q:page", page, pages)

    b.row(
        InlineKeyboardButton(text="➕ Новый пост", callback_data="q:new"),
        InlineKeyboardButton(text="🔄 Обновить", callback_data="q:refresh"),
    )
    b.row(InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:home"))
    return b.as_markup()


def _post_card_kb(post_id: int) -> InlineKeyboardMarkup:
    """Карточка одного поста."""
    b = InlineKeyboardBuilder()
    b.row(InlineKeyboardButton(text="✏️ Изменить", callback_data=f"q:edit:{post_id}"))
    b.row(InlineKeyboardButton(text="🕐 Перенести", callback_data=f"q:resched:{post_id}"))
    b.row(InlineKeyboardButton(text="🗑 Отменить", callback_data=f"q:cancel:{post_id}"))
    b.row(InlineKeyboardButton(text="⬅️ К списку", callback_data="q:refresh"))
    return b.as_markup()


def _batch_card_kb(batch_id: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(text="🕐 Перенести группу", callback_data=f"q:reschedbatch:{batch_id}")
    )
    b.row(
        InlineKeyboardButton(
            text="🗑 Отменить всю группу", callback_data=f"q:cancelbatch:{batch_id}"
        )
    )
    b.row(InlineKeyboardButton(text="⬅️ К списку", callback_data="q:refresh"))
    return b.as_markup()


async def _render_queue(target, edit: bool = False, page: int = 0) -> None:
    """Рисует список очереди (target — Message)."""
    async with session_factory() as session:
        groups = await crud.list_pending_grouped(session)
    if not groups:
        text = "📋 <b>Очередь пуста.</b>\n\nСоздайте первый отложенный пост:"
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="➕ Новый пост", callback_data="q:new")],
                [InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:home")],
            ]
        )
    else:
        text = "📋 <b>Запланированные посты</b>\nНажмите на пост для управления:"
        kb = _queue_list_kb(groups, page)
    if edit:
        try:
            await target.edit_text(text, reply_markup=kb)
            return
        except Exception:
            pass
    await target.answer(text, reply_markup=kb)


@router.message(Command("queue"))
async def cmd_queue(message: Message) -> None:
    """Открывает инлайн-вкладку очереди."""
    await _render_queue(message)

@router.message(Command("cancelpost"))
async def cmd_cancelpost(message: Message) -> None:
    """Отменяет отложенный пост по id: /cancelpost <id>."""
    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Формат: /cancelpost &lt;id&gt;")
        return
    post_id = int(parts[1])
    async with session_factory() as session:
        ok = await crud.cancel_post(session, post_id)
    try:
        sched.scheduler.remove_job(f"post:{post_id}")
    except Exception:
        pass
    await message.answer(
        f"Пост #{post_id} отменён." if ok else "Пост не найден или уже не в очереди."
    )


@router.message(Command("repost"))
async def cmd_repost(message: Message) -> None:
    """Переносит пост на новое время: /repost <id> ДД.ММ.ГГГГ ЧЧ:ММ."""
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Формат: /repost &lt;id&gt; ДД.ММ.ГГГГ ЧЧ:ММ")
        return
    sub = parts[1].split(maxsplit=1)
    if not sub[0].isdigit() or len(sub) < 2:
        await message.answer("Формат: /repost &lt;id&gt; ДД.ММ.ГГГГ ЧЧ:ММ")
        return
    post_id = int(sub[0])
    new_time = parse_publish_time(sub[1])
    if new_time is None:
        await message.answer("Не разобрал время или оно в прошлом.")
        return
    async with session_factory() as session:
        ok = await crud.reschedule_post(session, post_id, new_time)
    if not ok:
        await message.answer("Пост не найден или уже не в очереди.")
        return
    await sched.schedule_post(post_id, new_time)
    await message.answer(
        f"Пост #{post_id} перенесён на {to_local_str(new_time)} (МСК)."
    )

@router.callback_query(F.data == "q:refresh")
async def cb_queue_refresh(callback: CallbackQuery) -> None:
    await _render_queue(callback.message, edit=True)
    await callback.answer()


@router.callback_query(F.data.startswith("q:page:"))
async def cb_queue_page(callback: CallbackQuery) -> None:
    """Переключение страницы очереди."""
    page = int(callback.data.split(":")[2])
    await _render_queue(callback.message, edit=True, page=page)
    await callback.answer()


@router.callback_query(F.data.startswith("q:edit:"))
async def cb_queue_edit(callback: CallbackQuery, state: FSMContext) -> None:
    """Кнопка «Изменить»: ждём новое содержимое поста."""
    post_id = int(callback.data.split(":")[2])
    async with session_factory() as session:
        post = await crud.get_post(session, post_id)
    if post is None or post.status != "pending":
        await callback.answer("Пост уже не в очереди.", show_alert=True)
        await _render_queue(callback.message, edit=True)
        return

    await state.set_state(EditPostFSM.waiting_content)
    await state.update_data(edit_post_id=post_id)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Отмена", callback_data="q:edit_cancel")]]
    )
    await callback.message.edit_text(
        f"✏️ <b>Изменение поста #{post_id}</b>\n\n"
        "Пришлите новое содержимое: текст и/или одно медиа. "
        "Оно полностью заменит прежнее содержимое поста.\n"
        "(Время публикации и каналы остаются прежними.)",
        reply_markup=kb,
    )
    await callback.answer()


@router.callback_query(EditPostFSM.waiting_content, F.data == "q:edit_cancel")
async def cb_edit_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _render_queue(callback.message, edit=True)
    await callback.answer("Изменение отменено.")


@router.message(EditPostFSM.waiting_content)
async def step_edit_content(message: Message, state: FSMContext) -> None:
    """Принимает новое содержимое и сохраняет его в пост."""
    data = await state.get_data()
    post_id = data.get("edit_post_id")
    await state.clear()
    if not post_id:
        await message.answer("Не нашёл пост. Откройте /queue заново.")
        return

    media = _extract_media(message)
    if media:
        text = message.html_text if message.caption else ""
    else:
        text = message.html_text if message.text else ""

    if not media and not text:
        await message.answer("Пришлите текст или медиа.")
        return

    async with session_factory() as session:
        ok = await crud.update_post_content(
            session,
            post_id,
            text=text or "",
            media=json.dumps(media, ensure_ascii=False) if media else "",
        )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="📋 К очереди", callback_data="q:refresh")]]
    )
    await message.answer(
        f"✅ Содержимое поста #{post_id} обновлено."
        if ok
        else f"Пост #{post_id} не найден или уже не в очереди.",
        reply_markup=kb,
    )


@router.callback_query(F.data == "q:noop")
async def cb_queue_noop(callback: CallbackQuery) -> None:
    """Кнопка-счётчик страниц — ничего не делает."""
    await callback.answer()


@router.callback_query(F.data == "q:new")
async def cb_queue_new(callback: CallbackQuery, state: FSMContext) -> None:
    """Запускает создание поста прямо из вкладки очереди."""
    if callback.message.chat.type != "private":
        await callback.answer(
            "Создавать посты можно только в личке с ботом.",
            show_alert=True,
        )
        return
    await start_channel_choice(
        callback.message,
        state,
        user_id=callback.from_user.id,
        edit=True,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("q:post:"))
async def cb_queue_post(callback: CallbackQuery) -> None:
    """Карточка одного поста."""
    post_id = int(callback.data.split(":")[2])
    async with session_factory() as session:
        post = await crud.get_post(session, post_id)
    if post is None or post.status != "pending":
        await callback.answer("Пост уже не в очереди.", show_alert=True)
        await _render_queue(callback.message, edit=True)
        return
    preview = (post.text or "").replace("\n", " ")[:200] or "(медиа без текста)"
    text = (
        f"📄 <b>Пост #{post.id}</b>\n"
        f"Канал: <code>{post.channel_id}</code>\n"
        f"Время: <b>{to_local_str(post.publish_at)}</b> (МСК)\n"
        + (f"Автоудаление: {post.delete_after // 60} мин\n" if post.delete_after else "")
        + f"\n{preview}\n\n"
        f"Перенести: <code>/repost {post.id} ДД.ММ.ГГГГ ЧЧ:ММ</code>"
    )
    await callback.message.edit_text(text, reply_markup=_post_card_kb(post.id))
    await callback.answer()


@router.callback_query(F.data.startswith("q:batch:"))
async def cb_queue_batch(callback: CallbackQuery) -> None:
    """Карточка мультиканальной группы."""
    batch_id = callback.data.split(":", 2)[2]
    async with session_factory() as session:
        groups = await crud.list_pending_grouped(session)
    grp = next((g for g in groups if g[0].batch_id == batch_id), None)
    if not grp:
        await callback.answer("Группа уже не в очереди.", show_alert=True)
        await _render_queue(callback.message, edit=True)
        return
    first = grp[0]
    preview = (first.text or "").replace("\n", " ")[:200] or "(медиа без текста)"
    ids_str = ", ".join(f"#{p.id}" for p in grp)
    text = (
        f"🗂 <b>Группа из {len(grp)} постов</b> ({ids_str})\n"
        f"Время: <b>{to_local_str(first.publish_at)}</b> (МСК)\n\n"
        f"{preview}"
    )
    await callback.message.edit_text(text, reply_markup=_batch_card_kb(batch_id))
    await callback.answer()


@router.callback_query(F.data.startswith("q:cancel:"))
async def cb_queue_cancel(callback: CallbackQuery) -> None:
    """Отмена одного поста из карточки."""
    post_id = int(callback.data.split(":")[2])
    async with session_factory() as session:
        ok = await crud.cancel_post(session, post_id)
    try:
        sched.scheduler.remove_job(f"post:{post_id}")
    except Exception:
        pass
    await callback.answer(f"Пост #{post_id} отменён." if ok else "Уже отменён.")
    await _render_queue(callback.message, edit=True)


@router.callback_query(F.data.startswith("q:cancelbatch:"))
async def cb_queue_cancelbatch(callback: CallbackQuery) -> None:
    """Отмена всей группы из карточки."""
    batch_id = callback.data.split(":", 2)[2]
    async with session_factory() as session:
        cancelled = await crud.cancel_batch(session, batch_id)
    for pid in cancelled:
        try:
            sched.scheduler.remove_job(f"post:{pid}")
        except Exception:
            pass
    await callback.answer(f"Отменено постов: {len(cancelled)}.")
    await _render_queue(callback.message, edit=True)


@router.callback_query(F.data.startswith("q:resched:"))
async def cb_queue_resched(callback: CallbackQuery, state: FSMContext) -> None:
    """Кнопка «Перенести»: запоминаем пост и ждём новую дату сообщением."""
    post_id = int(callback.data.split(":")[2])

    async with session_factory() as session:
        post = await crud.get_post(session, post_id)
    if post is None or post.status != "pending":
        await callback.answer("Пост уже не в очереди.", show_alert=True)
        await _render_queue(callback.message, edit=True)
        return

    await state.set_state(RescheduleFSM.waiting_time)
    await state.update_data(resched_post_id=post_id)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Отмена", callback_data="q:resched_cancel")]]
    )
    await callback.message.edit_text(
        f"🕐 <b>Перенос поста #{post_id}</b>\n\n"
        f"Текущее время: <b>{to_local_str(post.publish_at)}</b> (МСК)\n\n"
        "Пришлите новую дату и время в формате\n"
        "<code>ДД.ММ.ГГГГ ЧЧ:ММ</code>, например 25.12.2025 18:30.",
        reply_markup=kb,
    )
    await callback.answer()


@router.callback_query(RescheduleFSM.waiting_time, F.data == "q:resched_cancel")
async def cb_resched_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    """Отмена переноса — возвращаемся к списку очереди."""
    await state.clear()
    await _render_queue(callback.message, edit=True)
    await callback.answer("Перенос отменён.")


@router.message(RescheduleFSM.waiting_time)
async def step_resched_time(message: Message, state: FSMContext) -> None:
    """Принимает новую дату и переносит пост."""
    new_time = parse_publish_time(message.text or "")
    if new_time is None:
        await message.answer(
            "Не разобрал время или оно в прошлом.\n"
            "Формат: <code>ДД.ММ.ГГГГ ЧЧ:ММ</code>, например 25.12.2025 18:30.\n"
            "Или нажмите «Отмена» в сообщении выше."
        )
        return

    data = await state.get_data()
    post_id = data.get("resched_post_id")
    await state.clear()

    if not post_id:
        await message.answer("Не нашёл пост для переноса. Откройте /queue заново.")
        return

    async with session_factory() as session:
        ok = await crud.reschedule_post(session, post_id, new_time)

    if not ok:
        await message.answer(f"Пост #{post_id} не найден или уже не в очереди.")
        return

    await sched.schedule_post(post_id, new_time)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="📋 К очереди", callback_data="q:refresh")]]
    )
    await message.answer(
        f"✅ Пост #{post_id} перенесён на <b>{to_local_str(new_time)}</b> (МСК).",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("q:reschedbatch:"))
async def cb_queue_reschedbatch(callback: CallbackQuery, state: FSMContext) -> None:
    """Кнопка «Перенести группу»: запоминаем batch_id и ждём новую дату."""
    batch_id = callback.data.split(":", 2)[2]

    async with session_factory() as session:
        groups = await crud.list_pending_grouped(session)
    grp = next((g for g in groups if g[0].batch_id == batch_id), None)
    if not grp:
        await callback.answer("Группа уже не в очереди.", show_alert=True)
        await _render_queue(callback.message, edit=True)
        return

    await state.set_state(RescheduleFSM.waiting_time_batch)
    await state.update_data(resched_batch_id=batch_id)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Отмена", callback_data="q:resched_cancel")]]
    )
    first = grp[0]
    await callback.message.edit_text(
        f"🕐 <b>Перенос группы из {len(grp)} постов</b>\n\n"
        f"Текущее время: <b>{to_local_str(first.publish_at)}</b> (МСК)\n\n"
        "Пришлите новую дату и время в формате\n"
        "<code>ДД.ММ.ГГГГ ЧЧ:ММ</code>, например 25.12.2025 18:30.\n"
        "Все посты группы будут перенесены на это время.",
        reply_markup=kb,
    )
    await callback.answer()


@router.callback_query(RescheduleFSM.waiting_time_batch, F.data == "q:resched_cancel")
async def cb_reschedbatch_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    """Отмена переноса группы — возврат к списку."""
    await state.clear()
    await _render_queue(callback.message, edit=True)
    await callback.answer("Перенос отменён.")

@router.message(RescheduleFSM.waiting_time_batch)
async def step_reschedbatch_time(message: Message, state: FSMContext) -> None:
    """Принимает новую дату и переносит все посты группы на это время."""
    new_time = parse_publish_time(message.text or "")
    if new_time is None:
        await message.answer(
            "Не разобрал время или оно в прошлом.\n"
            "Формат: <code>ДД.ММ.ГГГГ ЧЧ:ММ</code>, например 25.12.2025 18:30."
        )
        return

    data = await state.get_data()
    batch_id = data.get("resched_batch_id")
    await state.clear()
    if not batch_id:
        await message.answer("Не нашёл группу для переноса. Откройте /queue заново.")
        return

    async with session_factory() as session:
        groups = await crud.list_pending_grouped(session)
    grp = next((g for g in groups if g[0].batch_id == batch_id), None)
    if not grp:
        await message.answer("Группа уже не в очереди.")
        return

    moved = 0
    async with session_factory() as session:
        for p in grp:
            if await crud.reschedule_post(session, p.id, new_time):
                moved += 1
    for p in grp:
        await sched.schedule_post(p.id, new_time)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="📋 К очереди", callback_data="q:refresh")]]
    )
    await message.answer(
        f"✅ Перенесено постов: <b>{moved}</b> на <b>{to_local_str(new_time)}</b> (МСК).",
        reply_markup=kb,
    )

@router.message(RescheduleFSM.waiting_time_batch)
async def step_reschedbatch_time(message: Message, state: FSMContext) -> None:
    """Принимает новую дату и переносит все посты группы."""
    new_time = parse_publish_time(message.text or "")
    if new_time is None:
        await message.answer(
            "Не разобрал время или оно в прошлом.\n"
            "Формат: <code>ДД.ММ.ГГГГ ЧЧ:ММ</code>, например 25.12.2025 18:30.\n"
            "Или нажмите «Отмена» в сообщении выше."
        )
        return

    data = await state.get_data()
    batch_id = data.get("resched_batch_id")
    await state.clear()

    if not batch_id:
        await message.answer("Не нашёл группу для переноса. Откройте /queue заново.")
        return

    # Собираем актуальные id постов группы прямо перед переносом
    async with session_factory() as session:
        groups = await crud.list_pending_grouped(session)
        grp = next((g for g in groups if g[0].batch_id == batch_id), None)
        if not grp:
            await message.answer("Группа уже не в очереди.")
            return
        post_ids = [p.id for p in grp]
        moved = 0
        for pid in post_ids:
            if await crud.reschedule_post(session, pid, new_time):
                moved += 1

    # Переназначаем задачи в планировщике
    for pid in post_ids:
        await sched.schedule_post(pid, new_time)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="📋 К очереди", callback_data="q:refresh")]]
    )
    await message.answer(
        f"✅ Перенесено постов: <b>{moved}</b> из {len(post_ids)}\n"
        f"Новое время: <b>{to_local_str(new_time)}</b> (МСК).",
        reply_markup=kb,
    )


@router.message(Command("cancelpost"))
async def cmd_cancelpost(message: Message) -> None:
    """Отменяет запланированный пост по id."""
    parts = (message.text or "").split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Формат: /cancelpost <id>")
        return
    post_id = int(parts[1])
    async with session_factory() as session:
        ok = await crud.cancel_post(session, post_id)
    try:
        sched.scheduler.remove_job(f"post:{post_id}")
    except Exception:
        pass
    await message.answer(
        f"Пост #{post_id} отменён." if ok else f"Пост #{post_id} не найден или уже не в очереди."
    )


@router.message(Command("cancelbatch"))
async def cmd_cancelbatch(message: Message) -> None:
    """Отменяет всю группу мультиканальной публикации: /cancelbatch <код>."""
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer("Формат: /cancelbatch <код> (см. /queue)")
        return
    prefix = parts[1].strip()

    async with session_factory() as session:
        groups = await crud.list_pending_grouped(session)
        target_batch = None
        for grp in groups:
            bid = grp[0].batch_id
            if bid and bid.startswith(prefix):
                target_batch = bid
                break
        if target_batch is None:
            await message.answer("Группа не найдена. Проверьте код в /queue.")
            return
        cancelled = await crud.cancel_batch(session, target_batch)

    for post_id in cancelled:
        try:
            sched.scheduler.remove_job(f"post:{post_id}")
        except Exception:
            pass

    ids_str = ", ".join(f"#{i}" for i in cancelled)
    await message.answer(
        f"Отменена группа: {len(cancelled)} постов ({ids_str})."
        if cancelled
        else "В группе не осталось активных постов."
    )


@router.message(Command("repost"))
async def cmd_repost(message: Message) -> None:
    """Переносит время публикации поста: /repost <id> ДД.ММ.ГГГГ ЧЧ:ММ."""
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3 or not parts[1].isdigit():
        await message.answer("Формат: /repost <id> ДД.ММ.ГГГГ ЧЧ:ММ")
        return
    post_id = int(parts[1])
    new_time = parse_publish_time(parts[2])
    if new_time is None:
        await message.answer("Не разобрал время или оно в прошлом.")
        return
    async with session_factory() as session:
        ok = await crud.reschedule_post(session, post_id, new_time)
    if ok:
        await sched.schedule_post(post_id, new_time)
        await message.answer(f"Пост #{post_id} перенесён на {to_local_str(new_time)} (МСК).")
    else:
        await message.answer(f"Пост #{post_id} не найден или уже не в очереди.")
