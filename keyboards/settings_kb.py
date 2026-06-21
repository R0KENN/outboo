"""Клавиатуры панели настроек (раздел 4.4 ТЗ)."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database.models import ChatSettings


def _mark(enabled: bool) -> str:
    """Галочка или крестик в зависимости от состояния флага."""
    return "✅" if enabled else "❌"


def main_settings_kb(cfg: ChatSettings, chat_type: str = "group") -> InlineKeyboardMarkup:
    """Меню настроек, разное для канала и для группы."""
    cid = cfg.chat_id
    b = InlineKeyboardBuilder()

    if chat_type == "channel":
        # ── Настройки КАНАЛА ──
        b.row(
            InlineKeyboardButton(
                text=f"{_mark(cfg.autoreact_enabled)} Автореакции на посты",
                callback_data=f"set:react:{cid}",
            )
        )
        b.row(
            InlineKeyboardButton(
                text=f"{_mark(cfg.autoapprove_enabled)} Автоприём заявок",
                callback_data=f"set:toggle:autoapprove_enabled:{cid}",
            )
        )
        b.row(
            InlineKeyboardButton(
                text=f"{_mark(cfg.join_welcome_enabled)} Приветствие в ЛС при заявке",
                callback_data=f"set:toggle:join_welcome_enabled:{cid}",
            )
        )
    else:
        # ── Настройки ГРУППЫ ──
        b.row(
            InlineKeyboardButton(
                text=f"{_mark(cfg.antispam_enabled)} Антиспам",
                callback_data=f"set:toggle:antispam_enabled:{cid}",
            )
        )
        b.row(
            InlineKeyboardButton(
                text=f"@-упоминания каналов: {'блок' if cfg.block_mentions else 'разрешены'}",
                callback_data=f"set:toggle:block_mentions:{cfg.chat_id}",
            )
        )
        b.row(
            InlineKeyboardButton(
                text=f"{_mark(cfg.antimat_enabled)} Антимат",
                callback_data=f"set:toggle:antimat_enabled:{cid}",
            )
        )
        b.row(
            InlineKeyboardButton(
                text=f"{_mark(cfg.antiflood_enabled)} Антифлуд",
                callback_data=f"set:toggle:antiflood_enabled:{cid}",
            )
        )
        b.row(
            InlineKeyboardButton(
                text=f"{_mark(cfg.captcha_enabled)} Капча для новичков",
                callback_data=f"set:toggle:captcha_enabled:{cid}",
            )
        )
        b.row(
            InlineKeyboardButton(
                text=f"{_mark(cfg.welcome_enabled)} Приветствие",
                callback_data=f"set:toggle:welcome_enabled:{cid}",
            )
        )
        b.row(
            InlineKeyboardButton(
                text=f"{_mark(cfg.clean_service_msgs)} Чистить служебные",
                callback_data=f"set:toggle:clean_service_msgs:{cid}",
            )
        )
        b.row(
            InlineKeyboardButton(
                text=f"{_mark(cfg.quarantine_enabled)} Карантин новичков",
                callback_data=f"set:toggle:quarantine_enabled:{cid}",
            )
        )
        b.row(
            InlineKeyboardButton(
                text=f"{_mark(cfg.autoapprove_enabled)} Автоприём заявок",
                callback_data=f"set:toggle:autoapprove_enabled:{cid}",
            )
        )
        b.row(
            InlineKeyboardButton(
                text=f"⚙️ Параметры (порог варнов: {cfg.warn_limit})",
                callback_data=f"set:params:{cid}",
            )
        )

    b.row(InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:home"))
    return b.as_markup()


def params_kb(cfg: ChatSettings) -> InlineKeyboardMarkup:
    """Меню числовых параметров с кнопками +/-."""
    cid = cfg.chat_id
    b = InlineKeyboardBuilder()

    # Порог варнов
    b.row(
        InlineKeyboardButton(text="➖", callback_data=f"set:dec:warn_limit:{cid}"),
        InlineKeyboardButton(text=f"Порог варнов: {cfg.warn_limit}", callback_data="set:noop"),
        InlineKeyboardButton(text="➕", callback_data=f"set:inc:warn_limit:{cid}"),
    )
    # Действие при достижении порога
    b.row(
        InlineKeyboardButton(
            text=f"При лимите варнов: {'бан' if cfg.warn_action == 'ban' else 'мут'}",
            callback_data=f"set:warnaction:{cid}",
        )
    )
    # Лимит антифлуда: сообщений
    b.row(
        InlineKeyboardButton(text="➖", callback_data=f"set:dec:flood_messages:{cid}"),
        InlineKeyboardButton(text=f"Флуд: {cfg.flood_messages} сообщ.", callback_data="set:noop"),
        InlineKeyboardButton(text="➕", callback_data=f"set:inc:flood_messages:{cid}"),
    )
    # Лимит антифлуда: окно в секундах
    b.row(
        InlineKeyboardButton(text="➖", callback_data=f"set:dec:flood_seconds:{cid}"),
        InlineKeyboardButton(text=f"за {cfg.flood_seconds} сек.", callback_data="set:noop"),
        InlineKeyboardButton(text="➕", callback_data=f"set:inc:flood_seconds:{cid}"),
    )
    # Таймаут капчи
    b.row(
        InlineKeyboardButton(text="➖", callback_data=f"set:dec:captcha_timeout:{cid}"),
        InlineKeyboardButton(text=f"Капча: {cfg.captcha_timeout} сек.", callback_data="set:noop"),
        InlineKeyboardButton(text="➕", callback_data=f"set:inc:captcha_timeout:{cid}"),
    )
    # Тип капчи
    b.row(
        InlineKeyboardButton(
            text=f"Тип капчи: {'пример' if cfg.captcha_type == 'math' else 'кнопка'}",
            callback_data=f"set:captchatype:{cid}",
        )
    )
    b.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"set:refresh:{cid}"))
    return b.as_markup()


# Разрешённый Telegram базовый набор эмодзи для реакций (часть популярных)
REACTION_CHOICES = ["👍", "❤️", "🔥", "🎉", "👏", "😁", "🤔", "🙏", "💯", "⚡"]


def autoreact_kb(cfg: ChatSettings) -> InlineKeyboardMarkup:
    """Подменю автореакций: вкл/выкл, режим, выбор эмодзи (мультивыбор)."""
    cid = cfg.chat_id
    selected = {e.strip() for e in (cfg.autoreact_emojis or "").split(",") if e.strip()}
    b = InlineKeyboardBuilder()

    b.row(
        InlineKeyboardButton(
            text=f"{_mark(cfg.autoreact_enabled)} Автореакции",
            callback_data=f"set:toggle:autoreact_enabled:{cid}",
        )
    )
    b.row(
        InlineKeyboardButton(
            text=("🎲 Режим: случайная" if cfg.autoreact_random else "📚 Режим: все сразу"),
            callback_data=f"set:reactmode:{cid}",
        )
    )
    b.row(
        InlineKeyboardButton(
            text=f"{_mark(cfg.autoreact_join_custom)} Подхватывать кастом-эмодзи",
            callback_data=f"set:toggle:autoreact_join_custom:{cid}",
        )
    )

    # Сетка эмодзи по 5 в ряд; выбранные помечаются точкой
    row_buttons = []
    for emoji in REACTION_CHOICES:
        mark = "•" if emoji in selected else ""
        row_buttons.append(
            InlineKeyboardButton(
                text=f"{mark}{emoji}",
                callback_data=f"set:reactemoji:{emoji}:{cid}",
            )
        )
    # Раскладываем по 5 кнопок в ряд
    for i in range(0, len(row_buttons), 5):
        b.row(*row_buttons[i : i + 5])

    b.row(
        InlineKeyboardButton(
            text="🔁 Реакции на старые посты",
            callback_data=f"react:oldposts:{cid}",
        )
    )

    b.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"set:refresh:{cid}"))
    return b.as_markup()
