"""Переиспользуемый компонент выбора чата/канала из реестра managed_chats.

Используется в постинге, конкурсах, экспорте — везде, где нужно выбрать
целевой чат кнопками, а не вводить @username вручную.
"""

from aiogram.enums import ChatMemberStatus
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import settings
from database.crud import list_managed_chats
from database.engine import session_factory


def chat_icon(chat_type: str) -> str:
    return "📢" if chat_type == "channel" else "👥"


async def build_chat_picker(
    bot,
    user_id: int,
    callback_prefix: str,
    only_type: str | None = None,
    multi: bool = False,
    selected: set[int] | None = None,
    show_manual: bool = False,
):
    """Строит клавиатуру выбора чата.

    callback_prefix — префикс callback_data, к нему добавляется ":<chat_id>".
    only_type — 'channel' | 'supergroup'/'group' | None (все типы).
    multi — режим мультивыбора (галочки + кнопка «Готово»).
    selected — выбранные id (для multi).
    show_manual — добавить кнопку ручного ввода.
    Возвращает (markup, число_доступных).
    """
    selected = selected or set()
    is_global_admin = user_id in settings.admin_ids

    async with session_factory() as session:
        chats = await list_managed_chats(session, only_active=True)

    b = InlineKeyboardBuilder()
    shown = 0
    for ch in chats:
        # Фильтр по типу
        if only_type == "channel" and ch.chat_type != "channel":
            continue
        if only_type in ("group", "supergroup") and ch.chat_type == "channel":
            continue
        if not ch.is_admin:
            continue
        # Обычный пользователь видит только свои чаты (которые он добавил)
        if ch.added_by != user_id:
            continue

        title = ch.title or str(ch.chat_id)
        if multi:
            mark = "✅ " if ch.chat_id in selected else "▫️ "
        else:
            mark = ""
        b.row(
            InlineKeyboardButton(
                text=f"{mark}{chat_icon(ch.chat_type)} {title}",
                callback_data=f"{callback_prefix}:{ch.chat_id}",
            )
        )
        shown += 1

    if multi:
        b.row(
            InlineKeyboardButton(
                text=f"➡️ Готово ({len(selected)})",
                callback_data=f"{callback_prefix}_done",
            )
        )
    if show_manual:
        b.row(
            InlineKeyboardButton(
                text="✍️ Ввести вручную",
                callback_data=f"{callback_prefix}_manual",
            )
        )
    b.row(InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:home"))
    return b.as_markup(), shown
