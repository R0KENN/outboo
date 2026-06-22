"""Клавиатуры панели настроек (раздел 4.4 ТЗ)."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database.models import ChatSettings


def _toggle(enabled: bool) -> str:
    """Индикатор состояния тумблера: зелёный/серый кружок."""
    return "🟢" if enabled else "⚪️"


def main_settings_kb(cfg: ChatSettings, chat_type: str = "group") -> InlineKeyboardMarkup:
    """Меню настроек, разное для канала и для группы."""
    cid = cfg.chat_id
    b = InlineKeyboardBuilder()

    if chat_type == "channel":
        # ── Настройки КАНАЛА ──
        b.row(
            InlineKeyboardButton(
                text=f"{_toggle(cfg.autoreact_enabled)} Автореакции на посты",
                callback_data=f"set:react:{cid}",
            )
        )
        b.row(
            InlineKeyboardButton(
                text=f"{_toggle(cfg.autoapprove_enabled)} Автоприём заявок",
                callback_data=f"set:toggle:autoapprove_enabled:{cid}",
            )
        )
        b.row(
            InlineKeyboardButton(
                text=f"{_toggle(cfg.join_welcome_enabled)} Приветствие в ЛС при заявке",
                callback_data=f"set:toggle:join_welcome_enabled:{cid}",
            )
        )
        b.row(
            InlineKeyboardButton(
                text="✏️ Изменить текст приветствия",
                callback_data=f"set:joinwelcometext:{cid}",
            )
        )
    else:
        # ── Настройки ГРУППЫ: разделы ──
        b.row(
            InlineKeyboardButton(
                text="🛡 Модерация",
                callback_data=f"set:section:moderation:{cid}",
            )
        )
        b.row(
            InlineKeyboardButton(
                text="👋 Новички и приветствие",
                callback_data=f"set:section:newcomers:{cid}",
            )
        )
        b.row(
            InlineKeyboardButton(
                text="🧹 Чистка и заявки",
                callback_data=f"set:section:cleanup:{cid}",
            )
        )
        b.row(
            InlineKeyboardButton(
                text=f"⚙️ Параметры · порог варнов {cfg.warn_limit}",
                callback_data=f"set:params:{cid}",
            )
        )

    b.row(InlineKeyboardButton(text="🏠 В главное меню", callback_data="menu:home"))
    return b.as_markup()


def _back_root_row(b: InlineKeyboardBuilder, cid: int) -> None:
    """Общая строка «Назад» к корню настроек группы."""
    b.row(InlineKeyboardButton(text="‹ Назад к разделам", callback_data=f"set:refresh:{cid}"))


def section_moderation_kb(cfg: ChatSettings) -> InlineKeyboardMarkup:
    """Раздел модерации: антиспам, упоминания, мат, флуд."""
    cid = cfg.chat_id
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(
            text=f"{_toggle(cfg.antispam_enabled)} Антиспам · ссылки и пересылы",
            callback_data=f"set:toggle:antispam_enabled:{cid}",
        )
    )
    b.row(
        InlineKeyboardButton(
            text=f"{_toggle(cfg.block_mentions)} Блокировать @-упоминания",
            callback_data=f"set:toggle:block_mentions:{cid}",
        )
    )
    b.row(
        InlineKeyboardButton(
            text=f"{_toggle(cfg.antimat_enabled)} Антимат · удаление + варн",
            callback_data=f"set:toggle:antimat_enabled:{cid}",
        )
    )
    b.row(
        InlineKeyboardButton(
            text=f"{_toggle(cfg.antiflood_enabled)} Антифлуд · мут за частые сообщения",
            callback_data=f"set:toggle:antiflood_enabled:{cid}",
        )
    )
    b.row(
        InlineKeyboardButton(
            text="🚫 Стоп-слова",
            callback_data=f"set:words:0:{cid}",
        ),
        InlineKeyboardButton(
            text="🔗 Разрешённые ссылки",
            callback_data=f"set:domains:0:{cid}",
        ),
    )
    _back_root_row(b, cid)
    return b.as_markup()


def section_newcomers_kb(cfg: ChatSettings) -> InlineKeyboardMarkup:
    """Раздел новичков: капча, приветствие, правила, карантин."""
    cid = cfg.chat_id
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(
            text=f"{_toggle(cfg.captcha_enabled)} Капча для новичков",
            callback_data=f"set:toggle:captcha_enabled:{cid}",
        )
    )
    b.row(
        InlineKeyboardButton(
            text=f"{_toggle(cfg.welcome_enabled)} Приветствие",
            callback_data=f"set:toggle:welcome_enabled:{cid}",
        )
    )
    b.row(
        InlineKeyboardButton(
            text=f"{_toggle(cfg.quarantine_enabled)} Карантин новичков",
            callback_data=f"set:toggle:quarantine_enabled:{cid}",
        )
    )
    b.row(
        InlineKeyboardButton(
            text="✏️ Текст приветствия",
            callback_data=f"set:welcometext:{cid}",
        ),
        InlineKeyboardButton(
            text="📜 Правила чата",
            callback_data=f"set:rulestext:{cid}",
        ),
    )
    _back_root_row(b, cid)
    return b.as_markup()


def section_cleanup_kb(cfg: ChatSettings) -> InlineKeyboardMarkup:
    """Раздел чистки и заявок."""
    cid = cfg.chat_id
    b = InlineKeyboardBuilder()
    b.row(
        InlineKeyboardButton(
            text=f"{_toggle(cfg.clean_service_msgs)} Чистить служебные сообщения",
            callback_data=f"set:toggle:clean_service_msgs:{cid}",
        )
    )
    b.row(
        InlineKeyboardButton(
            text=f"{_toggle(cfg.autoapprove_enabled)} Автоприём заявок",
            callback_data=f"set:toggle:autoapprove_enabled:{cid}",
        )
    )
    _back_root_row(b, cid)
    return b.as_markup()


def params_kb(cfg: ChatSettings) -> InlineKeyboardMarkup:
    """Меню числовых параметров с кнопками +/-."""
    cid = cfg.chat_id
    b = InlineKeyboardBuilder()

    # Порог варнов
    b.row(
        InlineKeyboardButton(text="➖", callback_data=f"set:dec:warn_limit:{cid}"),
        InlineKeyboardButton(text=f"⚠️ Порог варнов · {cfg.warn_limit}", callback_data="set:noop"),
        InlineKeyboardButton(text="➕", callback_data=f"set:inc:warn_limit:{cid}"),
    )
    # Действие при достижении порога
    b.row(
        InlineKeyboardButton(
            text=f"🎯 При лимите: {'🔨 бан' if cfg.warn_action == 'ban' else '🔇 мут'}",
            callback_data=f"set:warnaction:{cid}",
        )
    )
    # Лимит антифлуда: сообщений
    b.row(
        InlineKeyboardButton(text="➖", callback_data=f"set:dec:flood_messages:{cid}"),
        InlineKeyboardButton(text=f"💬 Флуд · {cfg.flood_messages} сообщ.", callback_data="set:noop"),
        InlineKeyboardButton(text="➕", callback_data=f"set:inc:flood_messages:{cid}"),
    )
    # Лимит антифлуда: окно в секундах
    b.row(
        InlineKeyboardButton(text="➖", callback_data=f"set:dec:flood_seconds:{cid}"),
        InlineKeyboardButton(text=f"⏱ За {cfg.flood_seconds} сек.", callback_data="set:noop"),
        InlineKeyboardButton(text="➕", callback_data=f"set:inc:flood_seconds:{cid}"),
    )
    # Срок мута за флуд / по лимиту варнов (в минутах на кнопке)
    b.row(
        InlineKeyboardButton(text="➖", callback_data=f"set:dec:flood_mute_seconds:{cid}"),
        InlineKeyboardButton(
            text=f"🔇 Мут · {cfg.flood_mute_seconds // 60} мин.",
            callback_data="set:noop",
        ),
        InlineKeyboardButton(text="➕", callback_data=f"set:inc:flood_mute_seconds:{cid}"),
    )
    # Таймаут капчи
    b.row(
        InlineKeyboardButton(text="➖", callback_data=f"set:dec:captcha_timeout:{cid}"),
        InlineKeyboardButton(text=f"🤖 Капча · {cfg.captcha_timeout} сек.", callback_data="set:noop"),
        InlineKeyboardButton(text="➕", callback_data=f"set:inc:captcha_timeout:{cid}"),
    )
    # Тип капчи
    b.row(
        InlineKeyboardButton(
            text=f"🧩 Тип капчи: {'пример' if cfg.captcha_type == 'math' else 'кнопка'}",
            callback_data=f"set:captchatype:{cid}",
        )
    )
    b.row(InlineKeyboardButton(text="‹ Назад", callback_data=f"set:refresh:{cid}"))
    return b.as_markup()


# Только эмодзи из официального whitelist реакций Telegram
REACTION_CHOICES = ["👍", "👎", "❤️", "🔥", "🥰", "👏", "😁", "🎉", "🤔", "🙏"]


def autoreact_kb(cfg: ChatSettings) -> InlineKeyboardMarkup:
    """Подменю автореакций: вкл/выкл, режим, выбор эмодзи (мультивыбор)."""
    cid = cfg.chat_id
    selected = {e.strip() for e in (cfg.autoreact_emojis or "").split(",") if e.strip()}
    b = InlineKeyboardBuilder()

    b.row(
        InlineKeyboardButton(
            text=f"{_toggle(cfg.autoreact_enabled)} Автореакции",
            callback_data=f"set:toggle:autoreact_enabled:{cid}",
        )
    )
    b.row(
        InlineKeyboardButton(
            text=("🎲 Режим: случайная" if cfg.autoreact_random else "📌 Режим: первая из набора"),
            callback_data=f"set:reactmode:{cid}",
        )
    )
    b.row(
        InlineKeyboardButton(
            text=f"{_toggle(cfg.autoreact_join_custom)} Подхватывать кастом-эмодзи",
            callback_data=f"set:toggle:autoreact_join_custom:{cid}",
        )
    )
    b.row(
        InlineKeyboardButton(
            text=f"⏱ Задержка реакции · {getattr(cfg, 'autoreact_delay', 0)} сек",
            callback_data=f"set:reactdelay:{cid}",
        )
    )

    # Сетка эмодзи по 5 в ряд; выбранные обрамляются скобками-индикатором
    row_buttons = []
    for emoji in REACTION_CHOICES:
        text = f"· {emoji} ·" if emoji in selected else emoji
        row_buttons.append(
            InlineKeyboardButton(
                text=text,
                callback_data=f"set:reactemoji:{emoji}:{cid}",
            )
        )
    for i in range(0, len(row_buttons), 5):
        b.row(*row_buttons[i : i + 5])

    b.row(
        InlineKeyboardButton(
            text="🔁 Реакции на старые посты",
            callback_data=f"react:oldposts:{cid}",
        )
    )
    b.row(
        InlineKeyboardButton(
            text="🧹 Удалить реакции бота",
            callback_data=f"react:delposts:{cid}",
        )
    )

    b.row(InlineKeyboardButton(text="‹ Назад", callback_data=f"set:refresh:{cid}"))
    return b.as_markup()


WORDS_PER_PAGE = 8


def _list_kb(
    items: list[str],
    page: int,
    cid: int,
    kind: str,
    add_text: str,
    empty_hint: str,
) -> InlineKeyboardMarkup:
    """Универсальный список (стоп-слова / домены) с удалением и пагинацией.

    kind — 'word' или 'domain' (используется в callback_data).
    """
    b = InlineKeyboardBuilder()
    total = len(items)
    pages = max(1, (total + WORDS_PER_PAGE - 1) // WORDS_PER_PAGE)
    page = max(0, min(page, pages - 1))
    start = page * WORDS_PER_PAGE
    chunk = items[start : start + WORDS_PER_PAGE]

    for i, item in enumerate(chunk):
        idx = start + i
        b.row(
            InlineKeyboardButton(
                text=f"🗑 {item}",
                callback_data=f"set:del{kind}:{idx}:{page}:{cid}",
            )
        )

    if pages > 1:
        nav = []
        if page > 0:
            nav.append(
                InlineKeyboardButton(text="‹", callback_data=f"set:{kind}s:{page - 1}:{cid}")
            )
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="set:noop"))
        if page < pages - 1:
            nav.append(
                InlineKeyboardButton(text="›", callback_data=f"set:{kind}s:{page + 1}:{cid}")
            )
        b.row(*nav)

    b.row(
        InlineKeyboardButton(
            text=add_text,
            callback_data=f"set:add{kind}:{cid}",
        )
    )
    b.row(
        InlineKeyboardButton(
            text="‹ Назад к модерации",
            callback_data=f"set:section:moderation:{cid}",
        )
    )
    return b.as_markup()


def words_kb(items: list[str], page: int, cid: int) -> InlineKeyboardMarkup:
    """Список стоп-слов."""
    return _list_kb(
        items, page, cid, kind="word",
        add_text="➕ Добавить слово",
        empty_hint="Список стоп-слов пуст.",
    )


def domains_kb(items: list[str], page: int, cid: int) -> InlineKeyboardMarkup:
    """Список разрешённых доменов."""
    return _list_kb(
        items, page, cid, kind="domain",
        add_text="➕ Добавить ссылку/домен",
        empty_hint="Список разрешённых ссылок пуст.",
    )
