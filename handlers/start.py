"""Базовые команды для проверки работоспособности каркаса."""
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

router = Router(name="start")


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(
        "Бот-движок запущен. Каркас готов.\n"
        "Модули модерации, постинга и статистики подключаются поэтапно."
    )


@router.message(Command("ping"))
async def cmd_ping(message: Message, is_admin: bool) -> None:
    role = "администратор" if is_admin else "участник"
    await message.answer(f"pong — вы определены как: {role}")
