"""Логика проведения розыгрыша (раздел 4.6 ТЗ).

Завершение конкурса: выбираем случайных победителей среди участников,
публикуем результат (редактируем исходный пост и шлём сообщение в чат поста).
Таймер завершения вешается на тот же APScheduler, что и отложенные посты,
поэтому переживает перезапуск бота (восстанавливается через restore_giveaways).
"""

import logging
import random
from datetime import UTC, datetime

from aiogram import Bot
from apscheduler.triggers.date import DateTrigger

from database import crud
from database.engine import session_factory
from services.scheduler import scheduler

logger = logging.getLogger(__name__)


def _mention(user_id: int, name: str) -> str:
    """HTML-упоминание победителя по id (работает, даже если нет @username)."""
    safe = (name or str(user_id)).replace("<", "&lt;").replace(">", "&gt;")
    return f'<a href="tg://user?id={user_id}">{safe}</a>'


async def finish_giveaway(bot: Bot, giveaway_id: int) -> None:
    """Завершает конкурс: выбирает победителей и публикует результат. Идемпотентна."""
    async with session_factory() as session:
        g = await crud.get_giveaway(session, giveaway_id)
        if g is None or g.status != "active":
            return  # уже завершён/отменён
        participants = await crud.list_participants(session, giveaway_id)
        await crud.set_giveaway_status(session, giveaway_id, "finished")
        post_chat_id = g.post_chat_id
        post_message_id = g.post_message_id
        title = g.title
        winners_count = g.winners_count

    # Выбираем победителей
    if not participants:
        result_text = (
            "🎁 <b>Розыгрыш завершён</b>\n"
            "━━━━━━━━━━━━━━\n"
            f"{title}\n\n"
            "😔 Участников не было — победителей нет."
        )
        winners = []
    else:
        k = min(winners_count, len(participants))
        winners = random.sample(participants, k)
        lines = [f"🎉 <b>Розыгрыш завершён!</b>\n\n{title}\n\n🏆 Победители:"]
        for w in winners:
            lines.append(_mention(w.user_id, w.full_name))
        lines = [
            "🎉 <b>Розыгрыш завершён!</b>",
            "━━━━━━━━━━━━━━",
            f"{title}",
            "",
            "🏆 <b>Победители:</b>",
        ]
        for i, w in enumerate(winners, 1):
            lines.append(f"{i}. {_mention(w.user_id, w.full_name)}")
        result_text = "\n".join(lines)

    # Публикуем результат в канал/чат, где висел пост
    if post_chat_id:
        try:
            await bot.send_message(post_chat_id, result_text)
        except Exception as e:
            logger.warning("Не удалось отправить итоги конкурса #%s: %s", giveaway_id, e)
        # Помечаем сам пост как завершённый
        try:
            await bot.edit_message_reply_markup(
                chat_id=post_chat_id, message_id=post_message_id, reply_markup=None
            )
        except Exception:
            pass

    # Личное уведомление победителям
    for w in winners:
        try:
            await bot.send_message(
                w.user_id,
                f"🎉 <b>Поздравляем!</b>\nВы выиграли в розыгрыше:\n<b>{title}</b>"
            )
        except Exception:
            pass  # победитель мог не запускать бота в личке

    logger.info("Конкурс #%s завершён, победителей: %s", giveaway_id, len(winners))


def schedule_giveaway_finish(bot: Bot, giveaway_id: int, finish_at: datetime) -> None:
    """Ставит таймер завершения конкурса на общий планировщик."""
    if finish_at.tzinfo is None:
        finish_at = finish_at.replace(tzinfo=UTC)
    scheduler.add_job(
        finish_giveaway,
        trigger=DateTrigger(run_date=finish_at),
        args=[bot, giveaway_id],
        id=f"giveaway:{giveaway_id}",
        replace_existing=True,
    )


async def restore_giveaways(bot: Bot) -> None:
    """После рестарта заново ставит таймеры активных конкурсов.

    Если время уже прошло (бот лежал) — завершает их сразу.
    """
    async with session_factory() as session:
        active = await crud.list_active_giveaways(session)
    now = datetime.now(UTC)
    for g in active:
        finish_at = g.finish_at
        if finish_at.tzinfo is None:
            finish_at = finish_at.replace(tzinfo=UTC)
        if finish_at <= now:
            await finish_giveaway(bot, g.id)
        else:
            schedule_giveaway_finish(bot, g.id, finish_at)
    logger.info("Восстановлено активных конкурсов: %s", len(active))
