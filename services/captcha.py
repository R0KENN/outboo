"""Генерация капчи для новичков (раздел 4.2 ТЗ).

Поддерживается два типа: простая кнопка 'Я не бот' и математический вопрос.
Состояние ожидающих проверки хранится в памяти процесса.
"""

import random
import time
from dataclasses import dataclass, field

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


@dataclass
class PendingCaptcha:
    """Данные об ожидающем проверки новичке."""

    user_id: int
    chat_id: int
    correct: str  # правильный ответ (для math) или 'ok' (для button)
    join_message_id: int  # id служебного сообщения о входе (чтобы удалить)
    prompt_message_id: int = 0  # id сообщения с капчей
    created_at: float = field(default_factory=time.monotonic)


# Хранилище ожидающих: ключ (chat_id, user_id)
pending: dict[tuple[int, int], PendingCaptcha] = {}


def build_button_captcha(chat_id: int, user_id: int) -> tuple[str, InlineKeyboardMarkup, str]:
    """Капча-кнопка. Возвращает (текст, клавиатура, правильный_ответ)."""
    text = "Подтвердите, что вы не бот:"
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(
            text="✅ Я не бот",
            callback_data=f"captcha:ok:{chat_id}:{user_id}",
        )
    )
    return text, b.as_markup(), "ok"


def build_math_captcha(chat_id: int, user_id: int) -> tuple[str, InlineKeyboardMarkup, str]:
    """Математическая капча. Возвращает (текст, клавиатура, правильный_ответ)."""
    a, b_num = random.randint(1, 9), random.randint(1, 9)
    correct = str(a + b_num)
    # Готовим варианты ответов: правильный + три неверных
    options = {correct}
    while len(options) < 4:
        options.add(str(random.randint(2, 18)))
    options = list(options)
    random.shuffle(options)

    text = f"Решите пример, чтобы войти в чат:\n\n<b>{a} + {b_num} = ?</b>"
    kb = InlineKeyboardBuilder()
    for opt in options:
        kb.button(text=opt, callback_data=f"captcha:ans:{chat_id}:{user_id}:{opt}")
    kb.adjust(2)
    return text, kb.as_markup(), correct


def build_captcha(captcha_type: str, chat_id: int, user_id: int):
    """Выбирает тип капчи по настройке чата."""
    if captcha_type == "math":
        return build_math_captcha(chat_id, user_id)
    return build_button_captcha(chat_id, user_id)
