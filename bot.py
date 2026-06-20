"""Точка входа бота-движка."""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import settings
from database.engine import engine, init_models
from middlewares.admin_check import AdminCheckMiddleware
from middlewares.throttling import ThrottlingMiddleware
from utils.logger import setup_logging
from utils.commands import set_bot_commands

# ── обработчики ──
from handlers import start
from handlers import mod_commands, moderation, newcomers, posting
from handlers import stats, admin as admin_handler
from handlers import settings as settings_handler
from handlers import broadcast as broadcast_handler
from handlers import giveaway as giveaway_handler
from handlers import sheets as sheets_handler
# ── новые модули ──
from handlers import menu_inline as menu_inline_handler
from handlers import bot_membership as bot_membership_handler
from handlers import join_requests as join_requests_handler
from handlers import autoreact as autoreact_handler

# ── сервисы ──
from services.scheduler import setup_scheduler, restore_jobs
from services.giveaway import restore_giveaways

logger = logging.getLogger(__name__)


async def main() -> None:
    setup_logging()
    logger.info("Запуск бота…")

    # Инициализация БД. В проде структуру лучше накатывать через Alembic
    # (alembic upgrade head); init_models удобен для первого локального старта.
    await init_models()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    # Middleware (порядок важен: троттлинг до проверки прав)
    dp.message.middleware(ThrottlingMiddleware())
    dp.message.middleware(AdminCheckMiddleware())

    # ── Роутеры (порядок важен) ──
    # Инлайн-меню и события статуса бота — раньше «ловящих всё» роутеров.
    dp.include_router(menu_inline_handler.router)    # /start, главное инлайн-меню, список чатов
    dp.include_router(bot_membership_handler.router) # учёт добавления бота (my_chat_member)
    dp.include_router(join_requests_handler.router)  # автоприём заявок (chat_join_request)
    dp.include_router(autoreact_handler.router)      # автореакции на посты (channel_post)

    # Базовые команды (/ping, /help) — /start здесь уже не обрабатывается
    dp.include_router(start.router)

    # Функциональные модули в личке
    dp.include_router(settings_handler.router)
    dp.include_router(broadcast_handler.router)  # рассылка по подписчикам (FSM)
    dp.include_router(giveaway_handler.router)   # конкурсы (FSM + callback)
    dp.include_router(sheets_handler.router)      # экспорт в Google Sheets
    dp.include_router(posting.router)            # автопостинг (FSM, очередь)

    # Модули внутри групп
    dp.include_router(admin_handler.router)      # роли, словари, лог
    dp.include_router(stats.router)              # статистика
    dp.include_router(newcomers.router)          # новички: капча, приветствие
    dp.include_router(mod_commands.router)       # ручные команды модерации
    dp.include_router(moderation.router)         # авто-модерация — всегда последним

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await set_bot_commands(bot)   # установить меню команд

        # Планировщик отложенных постов и конкурсов
        setup_scheduler(bot)
        await restore_jobs()           # восстановить посты из БД после рестарта
        await restore_giveaways(bot)   # восстановить таймеры конкурсов

        logger.info("Бот в режиме long polling.")
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
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
