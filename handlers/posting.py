"""Модуль автопостинга (раздел 4.3 ТЗ).

Диалог создания отложенного поста через FSM, мультивыбор каналов, очередь,
отмена, перенос, публикация «сейчас». Создание ведётся в личке с ботом.
Целевые каналы выбираются кнопками из реестра managed_chats.
"""
import json
import logging
import uuid

from aiogram import F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import settings as app_settings
from database.engine import session_factory
from database import crud
from database.crud import list_managed_chats, get_managed_chat
from services import scheduler as sched
from utils.datetime_parse import parse_publish_time, to_local_str

logger = logging.getLogger(__name__)
router = Router(name="posting")


class NewPost(StatesGroup):
    """Шаги диалога создания поста."""
    channel = State()
    content = State()
    buttons = State()
    when = State()
    delete_after = State()


def _cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="❌ Отмена", callback_data="post:cancel_fsm")
    ]])


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
                    ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR,
                ):
                    continue
            except Exception:
                continue
        title = ch.title or str(ch.chat_id)
        mark = "✅ " if ch.chat_id in selected else "▫️ "
        b.row(InlineKeyboardButton(
            text=f"{mark}📢 {title}",
            callback_data=f"post:toggle:{ch.chat_id}",
        ))
        shown += 1

    b.row(InlineKeyboardButton(
        text=f"➡️ Готово ({len(selected)})", callback_data="post:done",
    ))
    b.row(InlineKeyboardButton(
        text="✍️ Ввести канал вручную", callback_data="post:manual",
    ))
    b.row(InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:home"))
    return b.as_markup(), shown


async def start_channel_choice(
    message: Message, state: FSMContext, user_id: int, edit: bool = False,
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
        text = (
            "📢 <b>Куда публикуем?</b>\n"
            "Отметьте один или несколько каналов и нажмите «Готово»:"
        )

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
        await message.answer(
            "Создавать посты удобнее в личке со мной: напишите /newpost мне в ЛС."
        )
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
        callback.bot, callback.from_user.id,
        data.get("is_global_admin", False), selected,
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
                "Я не админ этого канала. Добавьте меня администратором и "
                "пришлите канал ещё раз."
            )
            return
    except Exception as e:
        logger.warning("Проверка канала не удалась: %s", e)
        await message.answer(
            "Не получилось найти канал. Проверьте @username/id и что бот в нём."
        )
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

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀 Опубликовать сейчас", callback_data="post:now")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="post:cancel_fsm")],
    ])
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
    from datetime import datetime, timezone
    publish_at = datetime.now(timezone.utc)
    await state.update_data(publish_at=publish_at.isoformat())
    await state.set_state(NewPost.delete_after)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Не удалять", callback_data="post:nodel")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="post:cancel_fsm")],
    ])
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
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Не удалять", callback_data="post:nodel")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="post:cancel_fsm")],
    ])
    await message.answer(
        "Удалить пост автоматически через время?\n"
        "Пришлите число минут или нажмите «Не удалять».",
        reply_markup=kb,
    )


async def _finalize_posts(message: Message, state: FSMContext, delete_after: int) -> None:
    """Создаёт записи постов во все выбранные каналы и ставит их в планировщик."""
    from datetime import datetime, timezone

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

    del_note = (f"\nАвтоудаление через {delete_after // 60} мин."
                if delete_after else "")
    ids_str = ", ".join(f"#{i}" for i in created_ids)
    chans_str = ", ".join(channel_titles) if channel_titles else str(len(created_ids))
    is_now = (publish_at - datetime.now(timezone.utc)).total_seconds() < 90
    when_str = "сейчас" if is_now else f"{to_local_str(publish_at)} (МСК)"

    await message.answer(
        f"✅ Постов: <b>{len(created_ids)}</b> ({ids_str})\n"
        f"Каналы: <b>{chans_str}</b>\n"
        f"Время: <b>{when_str}</b>{del_note}\n\n"
        f"Очередь: /queue"
    )


@router.callback_query(NewPost.delete_after, F.data == "post:nodel")
async def cb_no_delete(callback: CallbackQuery, state: FSMContext) -> None:
    """Без автоудаления — финализируем."""
    await _finalize_posts(
        callback.message.model_copy(update={"from_user": callback.from_user}),
        state, delete_after=0,
    )
    await callback.answer()


@router.message(NewPost.delete_after)
async def step_delete_after(message: Message, state: FSMContext) -> None:
    """Принимает число минут автоудаления (или «-») и финализирует пост."""
    raw = (message.text or "").strip()
    delete_after = 0
    if raw != "-":
        try:
            delete_after = max(0, int(raw)) * 60
        except ValueError:
            await message.answer("Пришлите число минут или «-».")
            return
    await _finalize_posts(message, state, delete_after)


# ──────────────────────────────────────────────────────────────────────────
# Управление очередью
# ──────────────────────────────────────────────────────────────────────────
@router.message(Command("queue"))
async def cmd_queue(message: Message) -> None:
    """Показывает запланированные посты, мультиканальные — одной группой."""
    async with session_factory() as session:
        groups = await crud.list_pending_grouped(session)

    if not groups:
        await message.answer("Очередь пуста.")
        return

    lines = ["📋 <b>Запланированные посты:</b>\n"]
    for grp in groups:
        first = grp[0]
        preview = (first.text or "").replace("\n", " ")[:40] or "(медиа без текста)"
        when = to_local_str(first.publish_at)

        if len(grp) == 1 and not first.batch_id:
            lines.append(
                f"#{first.id} — {when} → <code>{first.channel_id}</code>\n   {preview}"
            )
        elif len(grp) == 1:
            lines.append(
                f"#{first.id} — {when} → 1 канал\n   {preview}\n"
                f"   отмена группы: /cancelbatch {first.batch_id[:8]}"
            )
        else:
            ids_str = ", ".join(f"#{p.id}" for p in grp)
            lines.append(
                f"🗂 {when} → <b>{len(grp)} каналов</b> ({ids_str})\n   {preview}\n"
                f"   отмена группы: /cancelbatch {first.batch_id[:8]}"
            )

    lines.append(
        "\nОтмена одного: /cancelpost &lt;id&gt;"
        "\nОтмена группы: /cancelbatch &lt;код&gt;"
        "\nПеренос: /repost &lt;id&gt; ДД.ММ.ГГГГ ЧЧ:ММ"
    )
    await message.answer("\n".join(lines))


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
        f"Пост #{post_id} отменён." if ok
        else f"Пост #{post_id} не найден или уже не в очереди."
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
        if cancelled else "В группе не осталось активных постов."
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
        await message.answer(
            f"Пост #{post_id} перенесён на {to_local_str(new_time)} (МСК)."
        )
    else:
        await message.answer(f"Пост #{post_id} не найден или уже не в очереди.")
