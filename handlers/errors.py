"""Глобальный обработчик ошибок aiogram (раздел: надёжность).

Ловит исключения, всплывшие из любого хендлера, и реагирует осмысленно:
- TelegramRetryAfter — флуд-контроль, просто логируем (повтор делают сервисы);
- TelegramForbiddenError — пользователь заблокировал бота;
- TelegramBadRequest «message is not modified» — безвредно, глушим;
- остальное — логируем со стеком, чтобы процесс не падал.
"""
import logging

from aiogram import Router
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.types import ErrorEvent

logger = logging.getLogger(__name__)
router = Router(name="errors")


@router.errors()
async def on_error(event: ErrorEvent) -> bool:
    """Единая точка обработки ошибок. Возврат True = ошибка обработана."""
    exc = event.exception

    if isinstance(exc, TelegramRetryAfter):
        logger.warning("Флуд-контроль Telegram: подождать %s сек.", exc.retry_after)
        return True

    if isinstance(exc, TelegramForbiddenError):
        logger.info("Бот заблокирован пользователем или нет доступа: %s", exc)
        return True

    if isinstance(exc, TelegramBadRequest):
        msg = str(exc).lower()
        # Частая безвредная ошибка при edit_text без изменений
        if "message is not modified" in msg or "message to edit not found" in msg:
            return True
        logger.warning("Bad request: %s", exc)
        return True

    # Всё остальное — логируем со стеком, но не роняем бота
    logger.exception("Необработанная ошибка в апдейте: %s", exc)
    return True
