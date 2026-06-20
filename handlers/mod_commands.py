"""Ручные команды модерации (раздел 4.1). Доступны только админам/модерам."""
import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from database.engine import session_factory
from filters.admin import IsAdminOrModerator
from services import moderation_actions as ma
from utils.parse import get_target_id, parse_duration

logger = logging.getLogger(__name__)
router = Router(name="mod_commands")

# Все команды этого роутера — только для админов и модераторов
router.message.filter(IsAdminOrModerator())


def _need_reply(message: Message) -> bool:
    return not (message.reply_to_message and message.reply_to_message.from_user)


@router.message(Command("ban"))
async def cmd_ban(message: Message) -> None:
    if _need_reply(message):
        await message.answer("Команда применяется ответом на сообщение нарушителя.")
        return
    target_id, name = get_target_id(message)
    async with session_factory() as session:
        await ma.ban_user(message.bot, session, message.chat.id, target_id, message.from_user.id)
    await message.answer(f"{name} забанен.")


@router.message(Command("unban"))
async def cmd_unban(message: Message) -> None:
    if _need_reply(message):
        await message.answer("Ответьте на сообщение пользователя.")
        return
    target_id, name = get_target_id(message)
    async with session_factory() as session:
        await ma.unban_user(message.bot, session, message.chat.id, target_id, message.from_user.id)
    await message.answer(f"{name} разбанен.")


@router.message(Command("kick"))
async def cmd_kick(message: Message) -> None:
    if _need_reply(message):
        await message.answer("Ответьте на сообщение пользователя.")
        return
    target_id, name = get_target_id(message)
    async with session_factory() as session:
        await ma.kick_user(message.bot, session, message.chat.id, target_id, message.from_user.id)
    await message.answer(f"{name} удалён из чата.")


@router.message(Command("mute"))
async def cmd_mute(message: Message) -> None:
    if _need_reply(message):
        await message.answer("Ответьте на сообщение. Формат: /mute 30m")
        return
    target_id, name = get_target_id(message)
    seconds = parse_duration(message.text or "") or 3600  # по умолчанию 1 час
    async with session_factory() as session:
        await ma.mute_user(message.bot, session, message.chat.id, target_id, message.from_user.id, seconds)
    await message.answer(f"{name} замучен на {seconds // 60} мин.")


@router.message(Command("unmute"))
async def cmd_unmute(message: Message) -> None:
    if _need_reply(message):
        await message.answer("Ответьте на сообщение пользователя.")
        return
    target_id, name = get_target_id(message)
    async with session_factory() as session:
        await ma.unmute_user(message.bot, session, message.chat.id, target_id, message.from_user.id)
    await message.answer(f"{name} размучен.")


@router.message(Command("warn"))
async def cmd_warn(message: Message) -> None:
    if _need_reply(message):
        await message.answer("Ответьте на сообщение нарушителя.")
        return
    target_id, name = get_target_id(message)
    async with session_factory() as session:
        count, limit, triggered = await ma.add_warn(
            message.bot, session, message.chat.id, target_id, message.from_user.id,
        )
    if triggered:
        await message.answer(f"{name} достиг лимита {limit} — применено действие.")
    else:
        await message.answer(f"{name}: предупреждение {count}/{limit}.")


@router.message(Command("unwarn"))
async def cmd_unwarn(message: Message) -> None:
    if _need_reply(message):
        await message.answer("Ответьте на сообщение пользователя.")
        return
    target_id, name = get_target_id(message)
    async with session_factory() as session:
        count = await ma.remove_warn(session, message.chat.id, target_id)
    await message.answer(f"{name}: осталось предупреждений — {count}.")


@router.message(Command("warns"))
async def cmd_warns(message: Message) -> None:
    if _need_reply(message):
        await message.answer("Ответьте на сообщение пользователя.")
        return
    target_id, name = get_target_id(message)
    async with session_factory() as session:
        count = await ma.get_warns(session, message.chat.id, target_id)
    await message.answer(f"{name}: предупреждений — {count}.")
