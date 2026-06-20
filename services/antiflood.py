"""Антифлуд: лимит N сообщений за M секунд с автомутом (раздел 4.1).

Счётчики хранятся в памяти процесса (по договорённости — без Redis).
"""
import time
from collections import defaultdict, deque


class FloodTracker:
    """Отслеживает частоту сообщений каждого участника в каждом чате."""

    def __init__(self) -> None:
        # ключ (chat_id, user_id) -> очередь временных меток сообщений
        self._events: dict[tuple[int, int], deque] = defaultdict(deque)

    def register(
        self, chat_id: int, user_id: int, limit: int, window: int,
    ) -> bool:
        """Регистрирует сообщение. True, если лимит превышен (это флуд)."""
        now = time.monotonic()
        key = (chat_id, user_id)
        events = self._events[key]
        events.append(now)
        # Удаляем метки старше окна
        while events and now - events[0] > window:
            events.popleft()
        return len(events) > limit

    def reset(self, chat_id: int, user_id: int) -> None:
        """Сбрасывает счётчик (например, после мута)."""
        self._events.pop((chat_id, user_id), None)


# Единый экземпляр на весь процесс
flood_tracker = FloodTracker()
