"""Универсальная пагинация для inline-списков."""

from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

PAGE_SIZE = 6  # элементов на страницу


def paginate(items: list, page: int, page_size: int = PAGE_SIZE):
    """Возвращает (срез_текущей_страницы, всего_страниц, нормализованный_номер)."""
    total = len(items)
    pages = max(1, (total + page_size - 1) // page_size)
    page = max(0, min(page, pages - 1))
    start = page * page_size
    return items[start : start + page_size], pages, page


def nav_row(b: InlineKeyboardBuilder, prefix: str, page: int, pages: int) -> None:
    """Добавляет ряд навигации «‹ / X из Y / ›», если страниц больше одной.

    prefix — префикс callback_data, например 'q:page'. Кнопки шлют
    '<prefix>:<номер_страницы>'.
    """
    if pages <= 1:
        return
    row = []
    if page > 0:
        row.append(InlineKeyboardButton(text="‹", callback_data=f"{prefix}:{page - 1}"))
    row.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="q:noop"))
    if page < pages - 1:
        row.append(InlineKeyboardButton(text="›", callback_data=f"{prefix}:{page + 1}"))
    b.row(*row)
