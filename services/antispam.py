"""Фильтрация контента: антиспам (ссылки/форварды) и антимат (раздел 4.1)."""

import re

from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import AllowedDomain, StopWord

# Регулярка для поиска ссылок и упоминаний
URL_PATTERN = re.compile(
    r"(https?://\S+|www\.\S+|t\.me/\S+|@[a-zA-Z][a-zA-Z0-9_]{4,})",
    re.IGNORECASE,
)
DOMAIN_PATTERN = re.compile(r"(?:https?://)?(?:www\.)?([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})")

# Таблица для нормализации обхода антимата (leet и латиница→кириллица)
LEET_MAP = str.maketrans(
    {
        "0": "о",
        "o": "о",
        "@": "а",
        "a": "а",
        "4": "ч",
        "3": "е",
        "e": "е",
        "1": "и",
        "!": "и",
        "c": "с",
        "p": "р",
        "x": "х",
        "y": "у",
        "k": "к",
        "h": "н",
        "b": "в",
        "t": "т",
        "m": "м",
        "$": "с",
        "6": "б",
        "9": "д",
    }
)


async def get_allowed_domains(session: AsyncSession, chat_id: int) -> set[str]:
    """Белый список доменов чата."""
    stmt = select(AllowedDomain.domain).where(AllowedDomain.chat_id == chat_id)
    rows = (await session.execute(stmt)).scalars().all()
    return {d.lower() for d in rows}


async def get_stopwords(session: AsyncSession, chat_id: int) -> list[str]:
    """Словарь стоп-слов чата."""
    stmt = select(StopWord.word).where(StopWord.chat_id == chat_id)
    return [w.lower() for w in (await session.execute(stmt)).scalars().all()]


def normalize_text(text: str) -> str:
    """Снимает простые приёмы обхода: разделители между буквами и leet-замены.

    Например 'с п а м' и 'сп@м' приводятся к виду, сравнимому со стоп-словом.
    """
    lowered = text.lower()
    # Убираем разделители между одиночными буквами: "с п а м" -> "спам"
    lowered = re.sub(r"(?<=\w)[\s._\-*]+(?=\w)", "", lowered)
    # Применяем карту leet-замен
    lowered = lowered.translate(LEET_MAP)
    # Схлопываем повторяющиеся буквы: "спааам" -> "спам"
    lowered = re.sub(r"(.)\1{2,}", r"\1", lowered)
    return lowered


def contains_link(message: Message) -> bool:
    """Проверяет, есть ли в сообщении ссылка/упоминание канала."""
    text = message.text or message.caption or ""
    if URL_PATTERN.search(text):
        return True
    # Ссылки могут быть и в entities (например, скрытые в тексте)
    entities = (message.entities or []) + (message.caption_entities or [])
    for ent in entities:
        if ent.type in ("url", "text_link", "mention"):
            return True
    return False


def has_url(message: Message) -> bool:
    """Есть ли в сообщении настоящая ссылка (http/t.me/www или url-entity)."""
    text = message.text or message.caption or ""
    if re.search(r"(https?://\S+|www\.\S+|t\.me/\S+)", text, re.IGNORECASE):
        return True
    entities = (message.entities or []) + (message.caption_entities or [])
    return any(ent.type in ("url", "text_link") for ent in entities)


def has_channel_mention(message: Message) -> bool:
    """Есть ли @-упоминание (потенциально канала/бота)."""
    text = message.text or message.caption or ""
    if re.search(r"@[a-zA-Z][a-zA-Z0-9_]{4,}", text):
        return True
    entities = (message.entities or []) + (message.caption_entities or [])
    return any(ent.type == "mention" for ent in entities)


def is_forwarded(message: Message) -> bool:
    """Проверяет, переслано ли сообщение из другого чата/канала."""
    return bool(
        message.forward_origin
        or getattr(message, "forward_from", None)
        or getattr(message, "forward_from_chat", None)
    )


def extract_domains(text: str) -> set[str]:
    """Извлекает домены из текста для сверки с белым списком."""
    return {m.group(1).lower() for m in DOMAIN_PATTERN.finditer(text)}


async def check_spam(
    session: AsyncSession,
    message: Message,
    chat_id: int,
    block_mentions: bool = False,
) -> bool:
    """True, если сообщение нужно удалить как спам.

    Ссылка разрешена, только если все её домены в белом списке.
    @-упоминания блокируются лишь при block_mentions=True.
    """
    if is_forwarded(message):
        return True

    if has_url(message):
        text = message.text or message.caption or ""
        domains = extract_domains(text)
        allowed = await get_allowed_domains(session, chat_id)
        # Если домен не извлёкся или хоть один вне белого списка — спам.
        if not domains or not domains.issubset(allowed):
            return True

    if block_mentions and has_channel_mention(message):
        return True

    return False


async def check_profanity(
    session: AsyncSession,
    message: Message,
    chat_id: int,
) -> bool:
    """True, если в сообщении есть стоп-слово (с учётом обхода).

    Сверка идёт по двум формам текста:
      1) исходный текст в нижнем регистре — проверка по границам слова,
         чтобы безобидное слово с «плохим» корнем внутри не удалялось;
      2) нормализованный текст без разделителей — ловит обходы вида
         'с п а м', 'сп@м', 'с-п-а-м'.
    """
    text = message.text or message.caption or ""
    if not text:
        return False

    stopwords = await get_stopwords(session, chat_id)
    if not stopwords:
        return False

    normalized = normalize_text(text)
    lowered = text.lower()

    for word in stopwords:
        if not word:
            continue
        pattern = r"(?<!\w)" + re.escape(word) + r"(?!\w)"
        # Короткие слова (≤4 символов) проверяем только по исходному тексту:
        # агрессивная нормализация (leet, склейка) на них даёт много ложных
        # срабатываний. Длинные слова проверяем и по нормализованной форме.
        if re.search(pattern, lowered):
            return True
        if len(word) > 4 and re.search(pattern, normalized):
            return True
    return False