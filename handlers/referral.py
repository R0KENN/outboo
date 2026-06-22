"""Реферальная система (раздел 4.6 ТЗ).

Каждый пользователь получает личную ссылку t.me/<bot>?start=ref_<id>.
Переход по ней и первый /start засчитывают приглашение пригласившему.
Команда /ref показывает ссылку и число приглашённых, /reftop — рейтинг.
Сама регистрация перехода происходит в handlers/menu_inline.py (разбор start=ref_).
"""

import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from database import crud
from database.engine import session_factory

logger = logging.getLogger(__name__)
router = Router(name="referral")


@router.message(Command("ref"))
async def cmd_ref(message: Message) -> None:
    """Личная реферальная ссылка пользователя и его статистика."""
    if message.chat.type != "private":
        await message.answer("Команда работает в личке со мной.")
        return

    me = await message.bot.get_me()
    user_id = message.from_user.id
    link = f"https://t.me/{me.username}?start=ref_{user_id}"

    async with session_factory() as session:
        invited = await crud.count_referrals(session, user_id)

    await message.answer(
        "🔗 <b>Ваша реферальная ссылка:</b>\n"
        f"<code>{link}</code>\n\n"
        f"Приглашено друзей: <b>{invited}</b>\n\n"
        "Поделитесь ссылкой — каждый, кто перейдёт по ней и запустит бота, "
        "будет засчитан вам."
    )


@router.message(Command("reftop"))
async def cmd_reftop(message: Message) -> None:
    """Рейтинг лучших пригласителей."""
    if message.chat.type != "private":
        return
    async with session_factory() as session:
        top = await crud.top_referrers(session, limit=10)
    if not top:
        await message.answer("Пока никто никого не пригласил.")
        return

    lines = ["🏆 <b>Топ пригласителей:</b>\n"]
    for i, (referrer_id, cnt) in enumerate(top, start=1):
        lines.append(f"{i}. <code>{referrer_id}</code> — {cnt} приглашённых")
    await message.answer("\n".join(lines))
