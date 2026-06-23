"""Точка входа бота-движка."""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import settings
from database.engine import engine, init_models
from handlers import admin as admin_handler
from handlers import autoreact as autoreact_handler
from handlers import bot_membership as bot_membership_handler
from handlers import broadcast as broadcast_handler
from handlers import errors as errors_handler
from handlers import giveaway as giveaway_handler
from handlers import join_requests as join_requests_handler
from handlers import referral as referral_handler

# ── новые модули ──
from handlers import menu_inline as menu_inline_handler

# ── обработчики ──
from handlers import mod_commands, moderation, newcomers, posting, start, stats
from handlers import settings as settings_handler
from handlers import sheets as sheets_handler
from middlewares.admin_check import AdminCheckMiddleware
from middlewares.throttling import ThrottlingMiddleware
from services.giveaway import restore_giveaways
from sqlalchemy.exc import OperationalError

# ── сервисы ──
from services.scheduler import restore_jobs, setup_scheduler
from utils.commands import set_bot_commands
from utils.logger import setup_logging

logger = logging.getLogger(__name__)


async def main() -> None:
    setup_logging()
    logger.info("Запуск бота…")

    # Инициализация БД. В проде структуру лучше накатывать через Alembic
    # (alembic upgrade head). При AUTO_INIT_DB=false create_all не вызывается,
    # чтобы схема не расходилась с миграциями.
    if settings.auto_init_db:
        await init_models()
    else:
        logger.info("AUTO_INIT_DB=false — схема управляется через Alembic.")

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(errors_handler.router)

    # Middleware (порядок важен: троттлинг до проверки прав)
    dp.message.middleware(ThrottlingMiddleware())
    dp.message.middleware(AdminCheckMiddleware())
    dp.callback_query.middleware(AdminCheckMiddleware())

    # ── Сборка роутеров по пакетам (управляется ENABLED_MODULES) ──
    # Ядро подключается всегда: /start, /help, инлайн-меню, учёт статуса бота.
    dp.include_router(menu_inline_handler.router)  # /start, главное инлайн-меню, список чатов
    dp.include_router(bot_membership_handler.router)  # учёт добавления бота (my_chat_member)
    dp.include_router(start.router)  # /ping, /help

    # Карта: имя модуля -> список роутеров. Порядок внутри списков важен,
    # а moderation.router должен подключаться последним среди всех (см. ниже).
    MODULE_ROUTERS = {
        # Модерация групп: настройки, роли/словари/лог, статистика,
        # новички (капча/приветствие), ручные команды и авто-модерация.
        "moderation": [
            settings_handler,
            admin_handler,
            stats,
            newcomers,
            mod_commands,
        ],
        # Приём заявок на вступление (автоприём, приветствие в ЛС).
        "join": [join_requests_handler],
        # Автореакции на посты канала.
        "autoreact": [autoreact_handler],
        # Автопостинг (FSM, очередь отложенных постов).
        "posting": [posting],
        # Рассылка по подписчикам.
        "broadcast": [broadcast_handler],
        # Конкурсы / giveaway.
        "giveaway": [giveaway_handler],
        # Реферальная программа.
        "referral": [referral_handler],
        # Экспорт статистики в Google Sheets.
        "sheets": [sheets_handler],
    }

    # Если ENABLED_MODULES пуст — включаем все модули (демо/премиум-сборка).
    active = settings.modules or set(MODULE_ROUTERS.keys())
    logger.info("Активные модули: %s", ", ".join(sorted(active)))

    for name in MODULE_ROUTERS:
        if name in active:
            for r in MODULE_ROUTERS[name]:
                dp.include_router(r.router)

    # Авто-модерация ловит ВСЕ сообщения, поэтому подключается строго последней —
    # но только если модуль модерации включён.
    if "moderation" in active:
        dp.include_router(moderation.router)

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await set_bot_commands(bot)  # установить меню команд

        # Планировщик отложенных постов и конкурсов
        setup_scheduler(bot)
        try:
            await restore_jobs()  # восстановить посты из БД после рестарта
            await restore_giveaways(bot)  # восстановить таймеры конкурсов
        except OperationalError as e:
            logger.error(
                "Не удалось прочитать БД (%s). Похоже, таблицы не созданы. "
                "Поставьте AUTO_INIT_DB=true или выполните 'alembic upgrade head'.",
                e,
            )
            raise SystemExit(1)

        logger.info("Бот в режиме long polling.")
        await dp.start_polling(
            bot,
            allowed_updates=[
                "message",
                "edited_message",
                "channel_post",
                "edited_channel_post",
                "callback_query",
                "my_chat_member",
                "chat_member",
                "chat_join_request",
                "message_reaction",
                "message_reaction_count",
            ],
        )
    finally:
        from services.scheduler import scheduler

        if scheduler.running:
            scheduler.shutdown(wait=False)
        await bot.session.close()
        await engine.dispose()
        logger.info("Бот остановлен.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Завершение по сигналу.")
