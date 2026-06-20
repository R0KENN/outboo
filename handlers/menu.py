"""Обработка нажатий главного reply-меню в личке.

Кнопки присылают текст — ловим его и вызываем уже существующие хендлеры,
чтобы не дублировать логику.
"""
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from config import settings

# Импортируем готовые хендлеры команд, чтобы переиспользовать их логику
from handlers.posting import cmd_newpost, cmd_queue
from handlers.giveaway import cmd_newgiveaway
from handlers.referral import cmd_ref
from handlers.broadcast import cmd_broadcast, cmd_subs
from handlers.sheets import cmd_export
from handlers.start import cmd_help

logger = logging.getLogger(__name__)
router = Router(name="menu")


@router.message(F.chat.type == "private", F.text == "📅 Создать пост")
async def menu_newpost(message: Message, state: FSMContext) -> None:
    await cmd_newpost(message, state)


@router.message(F.chat.type == "private", F.text == "📋 Очередь постов")
async def menu_queue(message: Message) -> None:
    await cmd_queue(message)


@router.message(F.chat.type == "private", F.text == "🎉 Создать конкурс")
async def menu_giveaway(message: Message, state: FSMContext) -> None:
    await cmd_newgiveaway(message, state)


@router.message(F.chat.type == "private", F.text == "🔗 Реферальная ссылка")
async def menu_ref(message: Message) -> None:
    await cmd_ref(message)


@router.message(F.chat.type == "private", F.text == "📨 Рассылка")
async def menu_broadcast(message: Message, state: FSMContext) -> None:
    await cmd_broadcast(message, state)


@router.message(F.chat.type == "private", F.text == "👥 Подписчики")
async def menu_subs(message: Message) -> None:
    await cmd_subs(message)


@router.message(F.chat.type == "private", F.text == "📊 Экспорт в Sheets")
async def menu_export(message: Message) -> None:
    await cmd_export(message)


@router.message(F.chat.type == "private", F.text == "❓ Помощь")
async def menu_help(message: Message) -> None:
    await cmd_help(message)
