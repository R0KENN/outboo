"""Сервис массовых рассылок по базе подписчиков (раздел 4.6 ТЗ).

Соблюдает лимиты Telegram (~30 сообщений/сек на разные чаты): между отправками
выдерживается небольшая пауза. Заблокировавшие бота помечаются неактивными,
чтобы не слать им повторно. При флуд-контроле (RetryAfter) делается пауза
и повторная попытка. Рассылка копирует исходное сообщение администратора,
поэтому поддерживает любой контент: текст, фото, видео, кнопки и т.д.
"""

import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramRetryAfter,
)

from database import crud
from database.engine import session_factory

logger = logging.getLogger(__name__)

# Пауза между отправками: ~20 сообщений/сек — с запасом под лимит Telegram (30/с)
SEND_DELAY = 0.05


async def run_broadcast(
    bot: Bot,
    from_chat_id: int,
    message_id: int,
    source_chat_id: int | None = None,
) -> dict[str, int]:
    """Копирует сообщение message_id из чата from_chat_id подписчикам.

    Если source_chat_id задан — рассылка только тем, кто пришёл к боту через
    этот канал (deep-link ?start=src_<id>); иначе — всем активным подписчикам.
    Возвращает сводку: {"total", "sent", "blocked", "failed"}.
    copy_message публикует контент без пометки «переслано».
    """
    async with session_factory() as session:
        if source_chat_id is None:
            user_ids = await crud.get_active_subscriber_ids(session)
        else:
            user_ids = await crud.get_active_subscriber_ids_by_source(
                session, source_chat_id
            )

    total = len(user_ids)
    sent = blocked = failed = 0

    for user_id in user_ids:
        try:
            await bot.copy_message(
                chat_id=user_id,
                from_chat_id=from_chat_id,
                message_id=message_id,
            )
            sent += 1

        except TelegramRetryAfter as e:
            # Флуд-контроль: ждём указанное время и пробуем этого же получателя ещё раз
            logger.warning("Флуд-контроль, пауза %s сек.", e.retry_after)
            await asyncio.sleep(e.retry_after)
            try:
                await bot.copy_message(
                    chat_id=user_id,
                    from_chat_id=from_chat_id,
                    message_id=message_id,
                )
                sent += 1
            except Exception as e2:
                failed += 1
                logger.warning("Повторная отправка %s не удалась: %s", user_id, e2)

        except TelegramForbiddenError:
            # Пользователь заблокировал бота — убираем из активной базы
            blocked += 1
            async with session_factory() as session:
                await crud.deactivate_subscriber(session, user_id)

        except TelegramBadRequest as e:
            # Например, чат не найден / сообщение удалено
            failed += 1
            logger.warning("Не удалось отправить %s: %s", user_id, e)

        except Exception as e:
            failed += 1
            logger.warning("Неизвестная ошибка при отправке %s: %s", user_id, e)

        await asyncio.sleep(SEND_DELAY)

    summary = {"total": total, "sent": sent, "blocked": blocked, "failed": failed}
    logger.info("Рассылка завершена: %s", summary)
    return summary
