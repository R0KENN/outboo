"""Модуль статистики (раздел 4.5 ТЗ). Команда /stats, доступна админам/модерам."""

import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from database import crud
from database.engine import session_factory
from filters.admin import IsAdminOrModerator

logger = logging.getLogger(__name__)
router = Router(name="stats")
router.message.filter(IsAdminOrModerator())

# Человекочитаемые подписи метрик
_LABELS = {
    "new_members": "Новых участников",
    "messages": "Сообщений (активность)",
    "deleted_spam": "Удалено как спам",
    "deleted_profanity": "Удалено за мат",
    "bans": "Банов",
    "mutes": "Мутов",
    "warns": "Предупреждений",
}

# Поддерживаемые периоды: ключ команды -> (дней, подпись)
_PERIODS = {
    "day": (1, "за день"),
    "week": (7, "за неделю"),
    "month": (30, "за месяц"),
}


def _format_report(period_label: str, data: dict[str, int]) -> str:
    """Собирает текст отчёта в фиксированном порядке метрик."""
    lines = [f"📊 <b>Статистика {period_label}</b>\n"]
    for key, label in _LABELS.items():
        lines.append(f"{label}: <b>{data.get(key, 0)}</b>")
    return "\n".join(lines)


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    """Показывает статистику. Формат: /stats [day|week|month] (по умолчанию week)."""
    if message.chat.type not in ("group", "supergroup"):
        await message.answer("Статистика доступна внутри группы.")
        return

    parts = (message.text or "").split()
    period_key = parts[1].lower() if len(parts) > 1 else "week"
    if period_key not in _PERIODS:
        await message.answer(
            "Период не распознан. Используйте: /stats day, /stats week или /stats month."
        )
        return

    days, label = _PERIODS[period_key]
    async with session_factory() as session:
        data = await crud.get_stats_period(session, message.chat.id, days)

    await message.answer(_format_report(label, data))
