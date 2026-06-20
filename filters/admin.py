"""Фильтр доступа к командам модерации (раздел 6, безопасность)."""
from aiogram.filters import BaseFilter
from aiogram.types import Message


class IsAdminOrModerator(BaseFilter):
    """Пропускает только администраторов чата и назначенных модераторов.

    Флаги is_admin / is_moderator проставляет AdminCheckMiddleware.
    """
    async def __call__(
        self, message: Message,
        is_admin: bool = False, is_moderator: bool = False,
    ) -> bool:
        return is_admin or is_moderator
