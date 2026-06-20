"""Команды выгрузки данных в Google Sheets (раздел 4.6 ТЗ).

Только для владельцев бота, в личке. Выгружает подписчиков, участников
конкурсов и журнал модерации на отдельные листы таблицы из .env.
"""

import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy import select

from config import settings
from database.engine import session_factory
from database.models import GiveawayParticipant, ModerationLog, Subscriber
from services import sheets
from utils.datetime_parse import to_local_str

logger = logging.getLogger(__name__)
router = Router(name="sheets")


def _is_owner(user_id: int) -> bool:
    return user_id in settings.admin_ids


@router.message(Command("sheettest"))
async def cmd_sheettest(message: Message) -> None:
    """Проверка подключения к Google-таблице."""
    if message.chat.type != "private" or not _is_owner(message.from_user.id):
        return
    if not sheets.is_configured():
        await message.answer(
            "Google Sheets не настроен. Заполните GOOGLE_CREDS_PATH и "
            "GOOGLE_SHEET_ID в .env и расшарьте таблицу на сервисный аккаунт."
        )
        return
    await message.answer("Проверяю доступ к таблице…")
    result = await sheets.check_connection()
    if result.startswith("ERROR:"):
        await message.answer(f"❌ Не удалось подключиться.\n<code>{result}</code>")
    else:
        await message.answer(f"✅ Подключение успешно. Таблица: <b>{result}</b>")


@router.message(Command("export"))
async def cmd_export(message: Message) -> None:
    """Выгружает все наборы данных в Google Sheets."""
    if message.chat.type != "private" or not _is_owner(message.from_user.id):
        return
    if not sheets.is_configured():
        await message.answer("Google Sheets не настроен (см. .env).")
        return

    await message.answer("📤 Выгружаю данные в Google Sheets…")

    try:
        # 1. Подписчики
        async with session_factory() as session:
            subs = (await session.execute(select(Subscriber))).scalars().all()
        sub_rows = [
            [
                s.user_id,
                s.username,
                s.full_name,
                "да" if s.is_active else "нет",
                to_local_str(s.joined_at) if s.joined_at else "",
            ]
            for s in subs
        ]
        n_subs = await sheets.write_worksheet(
            "Подписчики",
            ["ID", "Username", "Имя", "Активен", "Дата"],
            sub_rows,
        )

        # 2. Участники конкурсов
        async with session_factory() as session:
            parts = (await session.execute(select(GiveawayParticipant))).scalars().all()
        part_rows = [
            [
                p.giveaway_id,
                p.user_id,
                p.username,
                p.full_name,
                to_local_str(p.joined_at) if p.joined_at else "",
            ]
            for p in parts
        ]
        n_parts = await sheets.write_worksheet(
            "Участники конкурсов",
            ["Конкурс ID", "User ID", "Username", "Имя", "Дата"],
            part_rows,
        )

        # 3. Журнал модерации
        async with session_factory() as session:
            logs = (
                (
                    await session.execute(
                        select(ModerationLog).order_by(ModerationLog.created_at.desc())
                    )
                )
                .scalars()
                .all()
            )
        log_rows = [
            [log.chat_id, log.action, log.actor_id, log.target_id, log.reason,
             to_local_str(log.created_at) if log.created_at else ""]
            for log in logs
        ]
        n_logs = await sheets.write_worksheet(
            "Журнал модерации",
            ["Чат", "Действие", "Кто", "Над кем", "Причина", "Когда"],
            log_rows,
        )

        await message.answer(
            "✅ <b>Выгрузка завершена</b>\n"
            f"Подписчики: {n_subs}\n"
            f"Участники конкурсов: {n_parts}\n"
            f"Записи журнала: {n_logs}"
        )
    except Exception as e:
        logger.exception("Ошибка выгрузки в Sheets: %s", e)
        await message.answer(f"❌ Ошибка выгрузки:\n<code>{e}</code>")
