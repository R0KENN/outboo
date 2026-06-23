"""Smoke-тесты: ловят грубые поломки до того, как их увидит клиент.

Запуск:  pytest -q
Эти тесты не ходят в Telegram и не трогают боевую БД —
они проверяют, что код импортируется и базовая логика цела.
"""

import os

# Тестовый токен и SQLite в памяти — до импорта config.
os.environ.setdefault("BOT_TOKEN", "123456789:TEST-token-for-pytest")
os.environ.setdefault("DB_DRIVER", "sqlite")
os.environ.setdefault("SQLITE_PATH", ":memory:")


def test_config_loads():
    """Конфиг читается и DSN формируется без ошибок."""
    from config import settings

    assert settings.database_url.startswith("sqlite+aiosqlite")


def test_modules_parsing():
    """ENABLED_MODULES корректно разбирается в множество."""
    from config import Settings

    s = Settings(BOT_TOKEN="1:x", ENABLED_MODULES="moderation, posting ,giveaway")
    assert s.modules == {"moderation", "posting", "giveaway"}
    # Пустая строка = пустое множество (в bot.py трактуется как «все модули»).
    empty = Settings(BOT_TOKEN="1:x", ENABLED_MODULES="")
    assert empty.modules == set()


def test_all_handlers_import():
    """Все хендлеры импортируются — ловит синтаксис и битые импорты."""
    import importlib

    for mod in [
        "handlers.start",
        "handlers.moderation",
        "handlers.mod_commands",
        "handlers.newcomers",
        "handlers.posting",
        "handlers.settings",
        "handlers.admin",
        "handlers.stats",
        "handlers.giveaway",
        "handlers.broadcast",
        "handlers.referral",
        "handlers.sheets",
        "handlers.autoreact",
        "handlers.join_requests",
        "handlers.bot_membership",
        "handlers.menu_inline",
        "handlers.errors",
    ]:
        m = importlib.import_module(mod)
        assert hasattr(m, "router"), f"{mod} не содержит router"


def test_normalize_text_catches_obfuscation():
    """Антимат-нормализация снимает простые приёмы обхода."""
    from services.antispam import normalize_text

    # 'с п а м' и повторы должны схлопываться
    assert normalize_text("с п а м") == "спам"
    assert normalize_text("спааааам") == "спам"


def test_captcha_math_answer_is_correct():
    """Маткапча: правильный ответ присутствует среди вариантов."""
    from services.captcha import build_math_captcha

    text, kb, correct = build_math_captcha(chat_id=1, user_id=2)
    options = [b.text for row in kb.inline_keyboard for b in row]
    assert correct in options
    assert len(options) == 4
