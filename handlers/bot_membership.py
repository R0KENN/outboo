"""Отслеживание добавления/удаления бота в группы и каналы.

Telegram присылает апдейт my_chat_member, когда меняется статус самого бота
в чате (добавили, повысили до админа, удалили). На его основе ведём реестр
managed_chats — он питает список чатов в личном меню и индивидуальные настройки.
"""
import logging

from aiogram import Bot, Router
from aiogram.types import ChatMemberUpdated

from database.crud import (
    upsert_managed_chat,
    deactivate_managed_chat,
    get_or_create_chat_settings,
)
from database.engine import session_factory

logger = logging.getLogger(__name__)
router = Router(name="bot_membership")

# Статусы, означающие, что бот в чате присутствует
_PRESENT = {"member", "administrator", "creator", "restricted"}
# Статусы, означающие, что бота нет
_ABSENT = {"left", "kicked"}


@router.my_chat_member()
async def on_my_chat_member(event: ChatMemberUpdated, bot: Bot) -> None:
    """Реагирует на изменение статуса самого бота в чате/канале."""
    new_status = event.new_chat_member.status
    chat = event.chat
    actor = event.from_user  # кто изменил статус (добавил/удалил бота)

    # В личке этот апдетй тоже может прийти (пользователь блокирует/разблокирует),
    # но реестр чатов нас интересует только для групп и каналов.
    if chat.type == "private":
        return

    if new_status in _ABSENT:
        async with session_factory() as session:
            await deactivate_managed_chat(session, chat.id)
        logger.info("Бот удалён из %s (%s).", chat.title, chat.id)
        return

    if new_status in _PRESENT:
        is_admin = new_status in ("administrator", "creator")
        async with session_factory() as session:
            await upsert_managed_chat(
                session,
                chat_id=chat.id,
                chat_type=chat.type,
                title=chat.title or "",
                username=chat.username or "",
                is_admin=is_admin,
                added_by=actor.id if actor else 0,
            )
            # Сразу создаём строку настроек, чтобы чат был готов к конфигурации
            await get_or_create_chat_settings(session, chat.id)

        logger.info(
            "Бот добавлен/обновлён в %s (%s), админ=%s.",
            chat.title, chat.id, is_admin,
        )

        # Подсказка тому, кто добавил бота (если у нас есть с ним личка-диалог)
        if actor and is_admin:
            try:
                await bot.send_message(
                    actor.id,
                    f"✅ Бот подключён к «{chat.title}».\n"
                    f"Откройте меню командой /start → «Мои чаты», "
                    f"чтобы настроить его индивидуально.",
                )
            except Exception:
                # Пользователь не начинал диалог с ботом — это нормально
                pass
        elif actor and not is_admin:
            try:
                await bot.send_message(
                    actor.id,
                    f"⚠️ Бот добавлен в «{chat.title}», но без прав администратора. "
                    f"Большинство функций (модерация, реакции, приём заявок) "
                    f"требуют админ-прав. Назначьте бота администратором.",
                )
            except Exception:
                pass
