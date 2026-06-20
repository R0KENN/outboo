"""Главное reply-меню в личке с ботом."""

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import ReplyKeyboardBuilder

from config import settings


def main_menu_kb(user_id: int) -> ReplyKeyboardMarkup:
    """Главное меню. Владельцам бота показываем дополнительный ряд."""
    b = ReplyKeyboardBuilder()

    # Доступно всем
    b.row(
        KeyboardButton(text="📅 Создать пост"),
        KeyboardButton(text="📋 Очередь постов"),
    )
    b.row(
        KeyboardButton(text="🎉 Создать конкурс"),
        KeyboardButton(text="🔗 Реферальная ссылка"),
    )

    # Дополнительно для владельцев бота
    if user_id in settings.admin_ids:
        b.row(
            KeyboardButton(text="📨 Рассылка"),
            KeyboardButton(text="👥 Подписчики"),
        )
        b.row(KeyboardButton(text="📊 Экспорт в Sheets"))

    b.row(KeyboardButton(text="❓ Помощь"))

    return b.as_markup(resize_keyboard=True)
