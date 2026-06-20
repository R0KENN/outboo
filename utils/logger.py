"""Настройка логирования с ротацией файла (раздел 2 ТЗ)."""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from config import settings


def setup_logging() -> None:
    Path(settings.log_file).parent.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    )

    file_handler = RotatingFileHandler(
        settings.log_file, maxBytes=5 * 1024 * 1024, backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(settings.log_level)
    root.addHandler(file_handler)
    root.addHandler(console_handler)
