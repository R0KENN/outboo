"""Модуль автопостинга (раздел 4.3 ТЗ).

Диалог создания отложенного поста через FSM, очередь, отмена, перенос.
Все команды доступны только администраторам/модераторам (фильтр на роутере).
Создание поста ведётся в личке с ботом, чтобы не мусорить в чате;
целевой канал задаётся его @username или числовым id.
"""
import json
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)

from database.engine import session_factory
from database import crud
from filters.admin import IsAdminOrModerator
from services import scheduler as sched
from utils.datetime_parse import parse_publish_time, to_local_str

logger = logging.getLogger(__name__)
router = Router(name="posting")

# Команды постинга — только для админов/модераторов
router.message.filter(IsAdminOrModerator())


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
# Создание поста (FSM)
# ──────────────────────────────────────────────────────────────────────────
@router.message(Command("newpost"))
async def cmd_newpost(message: Message, state: FSMContext) -> None:
    """Запускает диалог создания отложенного поста (в личке с ботом)."""
    if message.chat.type != "private":
        await message.answer("Создавать посты удобнее в личке со мной: напишите /newpost мне в ЛС.")
        return
    await state.clear()
    await state.set_state(NewPost.channel)
    await message.answer(
        "📢 Куда публикуем?\n"
        "Пришлите @username канала или его числовой id.\n\n"
        "Важно: бот должен быть администратором этого канала.",
        reply_markup=_cancel_kb(),
    )


@router.callback_query(F.data == "post:cancel_fsm")
async def cb_cancel_fsm(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Создание поста отменено.")
    await callback.answer()


@router.message(NewPost.channel)
async def step_channel(message: Message, state: FSMContext) -> None:
    """Принимает канал, проверяет права бота в нём."""
    raw = (message.text or "").strip()
    if not raw:
        await message.answer("Пришлите @username или id канала.")
        return

    # Нормализуем: @name -> @name, число -> int
    target: str | int
    if raw.startswith("@"):
        target = raw
    else:
        try:
            target = int(raw)
        except ValueError:
            target = "@" + raw.lstrip("@")

    # Проверяем, что бот — админ канала и может постить
    try:
        chat = await message.bot.get_chat(target)
        member = await message.bot.get_chat_member(chat.id, message.bot.id)
        if member.status not in ("administrator", "creator"):
            await message.answer(
                "Я не админ этого канала. Добавьте меня администратором с правом "
                "публикации и пришлите канал ещё раз."
            )
            return
    except Exception as e:
        logger.warning("Проверка канала не удалась: %s", e)
        await message.answer(
            "Не получилось найти канал или проверить права. "
            "Проверьте @username/id и что бот добавлен в канал."
        )
        return

    await state.update_data(channel_id=chat.id, channel_title=chat.title or str(chat.id))
    await state.set_state(NewPost.content)
    await message.answer(
        f"Канал принят: <b>{chat.title or chat.id}</b>\n\n"
        "Теперь пришлите содержимое поста: текст, фото, видео, документ "
        "или альбом (несколько фото/видео в одном сообщении).\n"
        "Форматирование (жирный, курсив, ссылки) сохраняется.",
        reply_markup=_cancel_kb(),
    )


def _extract_media(message: Message) -> list[dict]:
    """Извлекает медиа из одиночного сообщения в формат [{"type","file_id"}]."""
    if message.photo:
        return [{"type": "photo", "file_id": message.photo[-1].file_id}]
    if message.video:
        return [{"type": "video", "file_id": message.video.file_id}]
    if message.document:
        return [{"type": "document", "file_id": message.document.file_id}]
    if message.audio:
        return [{"type": "audio", "file_id": message.audio.file_id}]
    if message.animation:
        # анимации шлём как video в публикации
        return [{"type": "video", "file_id": message.animation.file_id}]
    return []


# Буфер для сборки альбомов: media_group_id -> список элементов
_album_buffer: dict[str, list[dict]] = {}


@router.message(NewPost.content, F.media_group_id)
async def step_content_album(message: Message, state: FSMContext) -> None:
    """Собирает элементы альбома (приходят отдельными сообщениями)."""
    import asyncio
    mgid = message.media_group_id
    items = _extract_media(message)
    if items:
        _album_buffer.setdefault(mgid, []).extend(items)

    # Текст альбома лежит в подписи к первому элементу
    caption = message.html_text if (message.caption or message.text) else ""
    if caption:
        data = await state.get_data()
        if not data.get("text"):
            await state.update_data(text=caption)

    # Ждём, пока придут все части альбома, затем фиксируем один раз
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
            "Добавить inline-кнопки? Пришлите их в формате:\n"
            "<code>Текст кнопки - https://ссылка</code>\n"
            "Каждая кнопка с новой строки. Или отправьте «-», чтобы пропустить.",
            reply_markup=_cancel_kb(),
        )

    asyncio.create_task(_finalize(mgid))


@router.message(NewPost.content)
async def step_content_single(message: Message, state: FSMContext) -> None:
    """Принимает одиночное сообщение (текст или одно медиа)."""
    media = _extract_media(message)
    # html_text сохраняет форматирование (жирный/курсив/ссылки) в HTML-разметке
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
        "Добавить inline-кнопки? Пришлите их в формате:\n"
        "<code>Текст кнопки - https://ссылка</code>\n"
        "Каждая кнопка с новой строки. Или отправьте «-», чтобы пропустить.",
        reply_markup=_cancel_kb(),
    )


def _parse_buttons(text: str) -> str:
    """Парсит ввод кнопок в JSON [[{"text","url"}], ...] (по одной в ряд)."""
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
    """Принимает кнопки или пропуск."""
    raw = (message.text or "").strip()
    buttons = "" if raw == "-" else _parse_buttons(raw)
    await state.update_data(buttons=buttons)
    await state.set_state(NewPost.when)
    await message.answer(
        "Когда опубликовать?\n"
        "Пришлите дату и время в формате <code>ДД.ММ.ГГГГ ЧЧ:ММ</code>\n"
        "Например: <code>25.12.2025 18:30</code> (время МСК).",
        reply_markup=_cancel_kb(),
    )


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
    await message.answer(
        "Удалить пост автоматически через какое-то время?\n"
        "Пришлите число минут (например 60) или «-», чтобы не удалять.",
        reply_markup=_cancel_kb(),
    )


@router.message(NewPost.delete_after)
async def step_delete_after(message: Message, state: FSMContext) -> None:
    """Принимает интервал автоудаления и сохраняет пост в очередь."""
    from datetime import datetime
    raw = (message.text or "").strip()
    delete_after = 0
    if raw != "-":
        try:
            delete_after = max(0, int(raw)) * 60  # минуты -> секунды
        except ValueError:
            await message.answer("Пришлите число минут или «-».")
            return

    data = await state.get_data()
    publish_at = datetime.fromisoformat(data["publish_at"])

    async with session_factory() as session:
        post = await crud.create_scheduled_post(
            session,
            channel_id=data["channel_id"],
            text=data.get("text", ""),
            media=data.get("media", ""),
            buttons=data.get("buttons", ""),
            parse_mode="HTML",
            publish_at=publish_at,
            delete_after=delete_after,
            created_by=message.from_user.id,
        )

    # Ставим задачу в планировщик
    await sched.schedule_post(post.id, publish_at)
    await state.clear()

    del_note = (f"\nАвтоудаление через {delete_after // 60} мин."
                if delete_after else "")
    await message.answer(
        f"✅ Пост #{post.id} запланирован.\n"
        f"Канал: <b>{data.get('channel_title')}</b>\n"
        f"Время: <b>{to_local_str(publish_at)}</b> (МСК){del_note}\n\n"
        f"Очередь: /queue"
    )


# ──────────────────────────────────────────────────────────────────────────
# Управление очередью
# ──────────────────────────────────────────────────────────────────────────
@router.message(Command("queue"))
async def cmd_queue(message: Message) -> None:
    """Показывает список запланированных постов."""
    async with session_factory() as session:
        posts = await crud.list_pending_posts(session)

    if not posts:
        await message.answer("Очередь пуста.")
        return

    lines = ["📋 <b>Запланированные посты:</b>\n"]
    for p in posts:
        preview = (p.text or "").replace("\n", " ")[:40] or "(медиа без текста)"
        lines.append(
            f"#{p.id} — {to_local_str(p.publish_at)} → "
            f"<code>{p.channel_id}</code>\n   {preview}"
        )
    lines.append("\nОтмена: /cancelpost &lt;id&gt;\nПеренос: /repost &lt;id&gt; ДД.ММ.ГГГГ ЧЧ:ММ")
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
    # Снимаем точечный job, если он был
    try:
        sched.scheduler.remove_job(f"post:{post_id}")
    except Exception:
        pass
    await message.answer(
        f"Пост #{post_id} отменён." if ok
        else f"Пост #{post_id} не найден или уже не в очереди."
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
