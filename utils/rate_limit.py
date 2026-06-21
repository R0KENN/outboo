"""Помощник для безопасного выполнения массовых операций с Telegram API.

Соблюдает лимиты: держит паузу между вызовами и корректно реагирует на
TelegramRetryAfter (ждёт ровно указанное время и повторяет вызов).
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

from aiogram.exceptions import TelegramRetryAfter

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Безопасная пауза между вызовами (~20 операций/сек, лимит Telegram ~30/с)
DEFAULT_DELAY = 0.05


async def safe_call(
    func: Callable[[], Awaitable[T]],
    *,
    delay: float = DEFAULT_DELAY,
    max_retries: int = 3,
) -> T | None:
    """Выполняет асинхронный вызов с обработкой флуд-контроля.

    func — функция без аргументов, возвращающая awaitable (используйте lambda
    или functools.partial). При TelegramRetryAfter ждёт retry_after и повторяет.
    Возвращает результат вызова или None при исчерпании попыток.
    """
    for attempt in range(max_retries):
        try:
            result = await func()
            await asyncio.sleep(delay)
            return result
        except TelegramRetryAfter as e:
            wait = e.retry_after + 1
            logger.warning("Флуд-контроль: пауза %s сек (попытка %s).", wait, attempt + 1)
            await asyncio.sleep(wait)
        except Exception as e:
            logger.warning("Массовая операция: вызов не удался: %s", e)
            await asyncio.sleep(delay)
            return None
    logger.warning("Массовая операция: исчерпаны повторы после флуд-контроля.")
    return None
