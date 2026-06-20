"""Планировщик отложенных постов на APScheduler (раздел 4.3 ТЗ).

Подход надёжный к перезапускам: вместо регистрации одного job на каждый пост
крутится периодический сканер (раз в минуту), который забирает из БД все
наступившие pending-посты и публикует их. Дополнительно для постов с близким
временем ставится точечный one-shot job, чтобы публикация была минута-в-минуту,
а не «до следующего тика сканера».

Так задачи не теряются при рестарте бота: при старте просто заново читаем БД.
"""
import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta

from aiogram import Bot
from aiogram.types import (
    InlineKeyboardButton, InlineKeyboardMarkup,
    InputMediaPhoto, InputMediaVideo, InputMediaDocument, InputMediaAudio,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger

from database.engine import session_factory
from database import crud

logger = logging.getLogger(__name__)

# Единый экземпляр на процесс. Bot прокидывается при старте.
scheduler = AsyncIOScheduler(timezone="UTC")
_bot: Bot | None = None

# Карта типов медиа -> класс InputMedia для альбомов
_INPUT_MEDIA = {
    "photo": InputMediaPhoto,
    "video": InputMediaVideo,
    "document": InputMediaDocument,
    "audio": InputMediaAudio,
}


def _build_keyboard(buttons_json: str) -> InlineKeyboardMarkup | None:
    """Восстанавливает inline-клавиатуру из JSON, сохранённого в БД.

    Формат: [[{"text": "...", "url": "..."}], [...]] — список рядов.
    """
    if not buttons_json:
        return None
    try:
        rows = json.loads(buttons_json)
    except (ValueError, TypeError):
        return None
    if not rows:
        return None
    kb_rows = []
    for row in rows:
        kb_row = [
            InlineKeyboardButton(text=btn["text"], url=btn["url"])
            for btn in row if btn.get("text") and btn.get("url")
        ]
        if kb_row:
            kb_rows.append(kb_row)
    return InlineKeyboardMarkup(inline_keyboard=kb_rows) if kb_rows else None


def _parse_media(media_json: str) -> list[dict]:
    """Разбирает JSON медиа: [{"type": "photo", "file_id": "..."}, ...]."""
    if not media_json:
        return []
    try:
        items = json.loads(media_json)
    except (ValueError, TypeError):
        return []
    return items if isinstance(items, list) else []


async def _publish_post(post_id: int) -> None:
    """Публикует один пост по id и обновляет его статус. Идемпотентна.

    Берёт пост заново из БД и проверяет статус, чтобы один и тот же пост
    не ушёл дважды (если совпали сканер и точечный job).
    """
    if _bot is None:
        logger.error("Планировщик не инициализирован (нет Bot).")
        return

    async with session_factory() as session:
        post = await crud.get_post(session, post_id)
        if post is None or post.status != "pending":
            return  # уже отправлен/отменён или удалён

        channel_id = post.channel_id
        text = post.text or ""
        parse_mode = post.parse_mode or "HTML"
        media = _parse_media(post.media)
        keyboard = _build_keyboard(post.buttons)
        delete_after = post.delete_after
        repeat_rule = post.repeat_rule
        publish_at = post.publish_at

    sent_message_ids: list[int] = []
    try:
        if not media:
            # Текстовый пост
            msg = await _bot.send_message(
                channel_id, text, parse_mode=parse_mode,
                reply_markup=keyboard,
                disable_web_page_preview=False,
            )
            sent_message_ids.append(msg.message_id)

        elif len(media) == 1:
            # Одиночное медиа с подписью и кнопками
            item = media[0]
            mtype = item.get("type")
            file_id = item.get("file_id")
            common = dict(caption=text or None, parse_mode=parse_mode,
                          reply_markup=keyboard)
            if mtype == "photo":
                msg = await _bot.send_photo(channel_id, file_id, **common)
            elif mtype == "video":
                msg = await _bot.send_video(channel_id, file_id, **common)
            elif mtype == "document":
                msg = await _bot.send_document(channel_id, file_id, **common)
            elif mtype == "audio":
                msg = await _bot.send_audio(channel_id, file_id, **common)
            else:
                msg = await _bot.send_message(channel_id, text,
                                              parse_mode=parse_mode,
                                              reply_markup=keyboard)
            sent_message_ids.append(msg.message_id)

        else:
            # Альбом: подпись вешается на первый элемент, кнопки в альбоме нельзя
            group = []
            for i, item in enumerate(media):
                cls = _INPUT_MEDIA.get(item.get("type"))
                if cls is None:
                    continue
                kwargs = {"media": item.get("file_id")}
                if i == 0 and text:
                    kwargs["caption"] = text
                    kwargs["parse_mode"] = parse_mode
                group.append(cls(**kwargs))
            messages = await _bot.send_media_group(channel_id, media=group)
            sent_message_ids.extend(m.message_id for m in messages)
            # Кнопки отдельным сообщением, если они есть (в альбом их не вставить)
            if keyboard:
                kb_msg = await _bot.send_message(
                    channel_id, "⬆️", reply_markup=keyboard,
                )
                sent_message_ids.append(kb_msg.message_id)

        # Успех: отмечаем статус
        async with session_factory() as session:
            await crud.set_post_status(session, post_id, "sent")
        logger.info("Пост #%s опубликован в %s.", post_id, channel_id)

    except Exception as e:
        logger.error("Не удалось опубликовать пост #%s: %s", post_id, e)
        async with session_factory() as session:
            await crud.set_post_status(session, post_id, "failed")
        return

    # Автоудаление опубликованного поста через заданный интервал
    if delete_after and delete_after > 0 and sent_message_ids:
        run_at = datetime.now(timezone.utc) + timedelta(seconds=delete_after)
        scheduler.add_job(
            _delete_messages, trigger=DateTrigger(run_date=run_at),
            args=[channel_id, list(sent_message_ids)],
            id=f"del:{post_id}", replace_existing=True,
        )

    # Повторяющиеся посты (премиум): создаём следующую копию
    if repeat_rule in ("daily", "weekly"):
        delta = timedelta(days=1) if repeat_rule == "daily" else timedelta(weeks=1)
        next_time = (publish_at if publish_at.tzinfo
                     else publish_at.replace(tzinfo=timezone.utc)) + delta
        async with session_factory() as session:
            new_post = await crud.create_scheduled_post(
                session,
                channel_id=channel_id,
                text=text,
                media=json.dumps(media, ensure_ascii=False),
                buttons=post_buttons_dump(keyboard),
                parse_mode=parse_mode,
                publish_at=next_time,
                delete_after=delete_after,
                created_by=0,
                repeat_rule=repeat_rule,
            )
        _schedule_one(new_post.id, next_time)


def post_buttons_dump(keyboard: InlineKeyboardMarkup | None) -> str:
    """Сериализует клавиатуру обратно в JSON для повторяющихся постов."""
    if keyboard is None:
        return ""
    rows = [
        [{"text": btn.text, "url": btn.url} for btn in row if btn.url]
        for row in keyboard.inline_keyboard
    ]
    return json.dumps(rows, ensure_ascii=False)


async def _delete_messages(channel_id: int, message_ids: list[int]) -> None:
    """Удаляет ранее опубликованные сообщения (таймер автоудаления)."""
    if _bot is None:
        return
    for mid in message_ids:
        try:
            await _bot.delete_message(channel_id, mid)
        except Exception as e:
            logger.debug("Автоудаление: не удалось удалить %s: %s", mid, e)


def _schedule_one(post_id: int, publish_at: datetime) -> None:
    """Ставит точечный one-shot job на публикацию конкретного поста.

    Используется для постов с близким временем (в пределах окна сканера),
    чтобы публикация была точной по минуте.
    """
    if publish_at.tzinfo is None:
        publish_at = publish_at.replace(tzinfo=timezone.utc)
    scheduler.add_job(
        _publish_post, trigger=DateTrigger(run_date=publish_at),
        args=[post_id], id=f"post:{post_id}", replace_existing=True,
    )


async def _scan_due_posts() -> None:
    """Периодический сканер: публикует все наступившие pending-посты.

    Подстраховка на случай, если точечный job не сработал (рестарт и т.п.).
    """
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        due = await crud.list_due_posts(session, now)
        ids = [p.id for p in due]
    for post_id in ids:
        await _publish_post(post_id)


async def schedule_post(post_id: int, publish_at: datetime) -> None:
    """Внешняя точка: вызывается хендлером после создания поста в БД."""
    _schedule_one(post_id, publish_at)


def setup_scheduler(bot: Bot) -> None:
    """Инициализирует планировщик: запоминает Bot, ставит сканер, стартует."""
    global _bot
    _bot = bot
    # Периодический сканер раз в минуту
    scheduler.add_job(
        _scan_due_posts, "interval", minutes=1,
        id="scan_due_posts", replace_existing=True,
    )
    scheduler.start()
    logger.info("Планировщик запущен.")


async def restore_jobs() -> None:
    """При старте бота заново ставит точечные джобы на будущие pending-посты."""
    async with session_factory() as session:
        posts = await crud.list_pending_posts(session)
    count = 0
    for post in posts:
        publish_at = post.publish_at
        if publish_at.tzinfo is None:
            publish_at = publish_at.replace(tzinfo=timezone.utc)
        if publish_at > datetime.now(timezone.utc):
            _schedule_one(post.id, publish_at)
            count += 1
    logger.info("Восстановлено отложенных постов: %s", count)
