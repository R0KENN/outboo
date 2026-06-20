"""Разбор даты и времени публикации, введённых администратором (раздел 4.3)."""
from datetime import datetime, timezone, timedelta

# Часовой пояс по умолчанию для ввода админа (МСК). При желании вынести в .env.
DEFAULT_TZ = timezone(timedelta(hours=3))

_FORMATS = (
    "%d.%m.%Y %H:%M",
    "%d.%m.%y %H:%M",
    "%Y-%m-%d %H:%M",
    "%d.%m %H:%M",      # без года — подставим текущий
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
            dt = dt.replace(year=datetime.now(DEFAULT_TZ).year)
        parsed = dt
        break

    if parsed is None:
        return None

    # Привязываем к часовому поясу админа и переводим в UTC для хранения
    aware = parsed.replace(tzinfo=DEFAULT_TZ)
    utc = aware.astimezone(timezone.utc)

    if utc <= datetime.now(timezone.utc):
        return None
    return utc


def to_local_str(dt: datetime) -> str:
    """Форматирует UTC-время в строку в локальном поясе для показа админу."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(DEFAULT_TZ).strftime("%d.%m.%Y %H:%M")
