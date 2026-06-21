"""Разбор даты и времени публикации, введённых администратором (раздел 4.3)."""

from datetime import UTC, datetime, timedelta, timezone

# Часовой пояс по умолчанию для ввода админа (МСК). При желании вынести в .env.
DEFAULT_TZ = timezone(timedelta(hours=3))

_FORMATS = (
    "%d.%m.%Y %H:%M",
    "%d.%m.%y %H:%M",
    "%Y-%m-%d %H:%M",
    "%d.%m %H:%M",  # без года — подставим текущий
)


def parse_publish_time(text: str) -> datetime | None:
    """Парсит ввод вида '25.12.2025 18:30' в aware-datetime (UTC).

    Поддерживает несколько форматов. Возвращает None, если распознать не удалось
    или время уже в прошлом.
    """
    text = (text or "").strip()
    parsed: datetime | None = None

    for fmt in _FORMATS:
        try:
            dt = datetime.strptime(text, fmt)
        except ValueError:
            continue
        # Для формата без года подставляем текущий
        if "%Y" not in fmt and "%y" not in fmt:
            now_local = datetime.now(DEFAULT_TZ)
            dt = dt.replace(year=now_local.year)
            # Если дата без года оказалась в прошлом — значит, имелся в виду следующий год
            if dt.replace(tzinfo=DEFAULT_TZ) <= now_local:
                dt = dt.replace(year=now_local.year + 1)
        parsed = dt
        break

    if parsed is None:
        return None

    # Привязываем к часовому поясу админа и переводим в UTC для хранения
    aware = parsed.replace(tzinfo=DEFAULT_TZ)
    utc = aware.astimezone(UTC)

    if utc <= datetime.now(UTC):
        return None
    return utc


def to_local_str(dt: datetime) -> str:
    """Форматирует UTC-время в строку в локальном поясе для показа админу."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(DEFAULT_TZ).strftime("%d.%m.%Y %H:%M")
