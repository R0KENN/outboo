"""Разбор аргументов команд модерации (цель и длительность)."""

import re

from aiogram.types import Message

# Суффиксы длительности: 10m, 2h, 1d
DURATION_PATTERN = re.compile(r"(\d+)\s*([smhd])", re.IGNORECASE)
UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(text: str) -> int | None:
    """'30m' -> 1800 секунд. None, если длительность не найдена."""
    match = DURATION_PATTERN.search(text or "")
    if not match:
        return None
    value, unit = int(match.group(1)), match.group(2).lower()
    return value * UNIT_SECONDS[unit]


def get_target_id(message: Message) -> tuple[int | None, str | None]:
    """Определяет, к кому применяется команда.

    Возвращает (user_id, имя_для_вывода). Цель берётся из реплая на
    сообщение нарушителя. Возврат (None, None), если реплая нет.
    """
    if message.reply_to_message and message.reply_to_message.from_user:
        user = message.reply_to_message.from_user
        return user.id, (user.full_name or user.username or str(user.id))
    return None, None
