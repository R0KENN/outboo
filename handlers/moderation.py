"""Автоматическая модерация входящих сообщений (раздел 4.1)."""

import logging
from datetime import UTC, datetime

from aiogram import Router
from aiogram.types import Message

from database import crud
from database.crud import get_join_time, get_or_create_chat_settings
from database.engine import session_factory
from services import antispam
from services.antiflood import flood_tracker
from services.moderation_actions import add_warn, mute_user

logger = logging.getLogger(__name__)
router = Router(name="moderation")


@router.message()
async def moderate_message(
    message: Message,
    is_admin: bool = False,
    is_moderator: bool = False,
) -> None:
    """Проверяет каждое сообщение группы через включённые фильтры."""
    # Работаем только в группах и не трогаем админов/модераторов
    if message.chat.type not in ("group", "supergroup"):
        return
    if is_admin or is_moderator or message.from_user is None:
        return

    chat_id = message.chat.id
    user_id = message.from_user.id

    async with session_factory() as session:
        cfg = await get_or_create_chat_settings(session, chat_id)

        # 0. Карантин новых аккаунтов: первые N часов нельзя ссылки/медиа/форварды
        if cfg.quarantine_enabled and cfg.newbie_quarantine_hours > 0:
            joined_at = await get_join_time(session, chat_id, user_id)
            if joined_at is not None:
                age_hours = (datetime.now(UTC) - joined_at).total_seconds() / 3600
                if age_hours < cfg.newbie_quarantine_hours:
                    has_media = bool(
                        message.photo
                        or message.video
                        or message.document
                        or message.audio
                        or message.animation
                    )
                    is_forward = antispam.is_forwarded(message)
                    has_link = antispam.contains_link(message)
                    if has_media or is_forward or has_link:
                        try:
                            await message.delete()
                        except Exception as e:
                            logger.warning("Карантин: не удалось удалить: %s", e)
                        return

        # 1. Антифлуд
        if cfg.antiflood_enabled:
            is_flood = flood_tracker.register(
                chat_id,
                user_id,
                cfg.flood_messages,
                cfg.flood_seconds,
            )
            if is_flood:
                flood_tracker.reset(chat_id, user_id)
                try:
                    await mute_user(
                        message.bot,
                        session,
                        chat_id,
                        user_id,
                        message.bot.id,
                        cfg.flood_mute_seconds,
                        "flood",
                    )
                    import asyncio
                    notice = await message.answer(
                        f"{message.from_user.full_name} замучен за флуд."
                    )

                    async def _del_notice(m=notice):
                        await asyncio.sleep(10)
                        try:
                            await m.delete()
                        except Exception:
                            pass

                    asyncio.create_task(_del_notice())
                except Exception as e:
                    logger.warning("Не удалось замутить за флуд: %s", e)
                return

        # 2. Антиспам
        if cfg.antispam_enabled and await antispam.check_spam(
            session,
            message,
            chat_id,
            block_mentions=cfg.block_mentions,
        ):
            try:
                await message.delete()
                await crud.bump_stat(session, chat_id, "deleted_spam")
            except Exception as e:
                logger.warning("Не удалось удалить спам: %s", e)
            return

        # 3. Антимат
        if cfg.antimat_enabled and await antispam.check_profanity(session, message, chat_id):
            try:
                await message.delete()
                await crud.bump_stat(session, chat_id, "deleted_profanity")
                count, limit, triggered = await add_warn(
                    message.bot,
                    session,
                    chat_id,
                    user_id,
                    message.bot.id,
                    "profanity",
                )
                note = (
                    f"{message.from_user.full_name}: предупреждение "
                    f"{count}/{limit} за нецензурную лексику."
                )
                if triggered:
                    note = (
                        f"{message.from_user.full_name} достиг лимита "
                        f"предупреждений — применено действие."
                    )
                await message.answer(note)
            except Exception as e:
                logger.warning("Ошибка антимата: %s", e)
            return

        # 4. Учёт активности (сообщение прошло все фильтры)
        await crud.bump_stat(session, chat_id, "messages")
