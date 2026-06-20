"""Экспорт данных бота в Google Sheets (раздел 4.6 ТЗ).

Используется сервисный аккаунт Google (JSON-ключ). Библиотека gspread
синхронная, поэтому её вызовы выносятся в отдельный поток через
asyncio.to_thread, чтобы не блокировать event loop бота.

Каждый набор данных пишется на свой лист (worksheet): лист полностью
очищается и перезаписывается актуальными данными.
"""
import asyncio
import logging

from config import settings

logger = logging.getLogger(__name__)

# Области доступа: только работа с таблицами и Drive (для открытия по id)
_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def is_configured() -> bool:
    """True, если в .env заданы путь к ключу и id таблицы."""
    return bool(settings.google_creds_path and settings.google_sheet_id)


def _get_spreadsheet():
    """Синхронно открывает таблицу по id. Вызывается внутри to_thread."""
    import gspread
    gc = gspread.service_account(filename=settings.google_creds_path)
    return gc.open_by_key(settings.google_sheet_id)


def _write_worksheet_sync(title: str, header: list[str], rows: list[list]) -> int:
    """Перезаписывает лист title: очищает и заливает header + rows. Возвращает число строк."""
    ss = _get_spreadsheet()
    try:
        ws = ss.worksheet(title)
        ws.clear()
    except Exception:
        # Листа ещё нет — создаём
        ws = ss.add_worksheet(title=title, rows=max(len(rows) + 10, 100), cols=max(len(header), 10))
    data = [header] + rows
    if data:
        ws.update(range_name="A1", values=data)
    return len(rows)


async def write_worksheet(title: str, header: list[str], rows: list[list]) -> int:
    """Асинхронная обёртка над синхронной записью листа."""
    return await asyncio.to_thread(_write_worksheet_sync, title, header, rows)


async def check_connection() -> str:
    """Проверяет доступ к таблице. Возвращает её название или текст ошибки."""
    def _check():
        ss = _get_spreadsheet()
        return ss.title
    try:
        return await asyncio.to_thread(_check)
    except Exception as e:
        return f"ERROR: {e}"
