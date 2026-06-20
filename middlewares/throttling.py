"""Антифлуд-middleware (раздел 4.1). In-memory, без внешних зависимостей."""
import time
from collections import defaultdict
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message

from config import settings


class ThrottlingMiddleware(BaseMiddleware):
    """Ограничивает частоту обработки сообщений от одного пользователя.

    Хранит время последнего пропущенного сообщения в памяти процесса.
    Для одного экземпляра бота этого достаточно; при масштабировании
    на несколько процессов сюда подключается Redis.
    """

    def __init__(self, rate_limit: float | None = None) -> None:
        self.rate_limit = rate_limit or settings.throttle_rate
        self._last_time: dict[int, float] = defaultdict(float)

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        if event.from_user is None:
            return await handler(event, data)

        user_id = event.from_user.id
        now = time.monotonic()

        if now - self._last_time[user_id] < self.rate_limit:
            # Слишком часто — глушим базовый троттлинг (не путать с
            # настраиваемым per-chat антифлудом, который добавим в Этапе 1)
            return None

        self._last_time[user_id] = now
        return await handler(event, data)
