"""Антифлуд-middleware (раздел 4.1). In-memory, без внешних зависимостей."""

import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

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

        # В группах/супергруппах базовый троттлинг НЕ применяем: иначе он
        # глушит сообщения спамеров до того, как их увидит автомодерация.
        # Частоту в группах ограничивает настраиваемый per-chat антифлуд
        # (services.antiflood.flood_tracker внутри handlers/moderation.py).
        if event.chat.type != "private":
            return await handler(event, data)

        user_id = event.from_user.id
        now = time.monotonic()

        if now - self._last_time[user_id] < self.rate_limit:
            # Слишком частые сообщения/нажатия в личке — глушим.
            return None

        self._last_time[user_id] = now

        # Защита от неограниченного роста словаря в долгоживущем процессе.
        if len(self._last_time) > 10_000:
            cutoff = now - max(self.rate_limit * 10, 60)
            stale = [u for u, t in self._last_time.items() if t < cutoff]
            for u in stale:
                del self._last_time[u]

        return await handler(event, data)
