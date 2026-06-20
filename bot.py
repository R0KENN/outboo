"""Точка входа бота-движка."""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import settings
from database.engine import engine, init_models
from handlers import start
from middlewares.admin_check import AdminCheckMiddleware
from middlewares.throttling import ThrottlingMiddleware
from utils.logger import setup_logging
from handlers import start
from handlers import mod_commands, moderation, newcomers, posting
from handlers import stats, admin as admin_handler
from handlers import settings as settings_handler
from services.scheduler import setup_scheduler, restore_jobs

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

    # Роутеры (по мере роста проекта здесь добавляются модули)
    dp.include_router(start.router)
    dp.include_router(settings_handler.router)
    dp.include_router(admin_handler.router)    # роли, словари, лог
    dp.include_router(stats.router)            # статистика
    dp.include_router(posting.router)          # автопостинг (FSM, очередь)
    dp.include_router(newcomers.router)        # новички: капча, приветствие
    dp.include_router(mod_commands.router)
    dp.include_router(moderation.router)       # всегда последним

    try:
        await bot.delete_webhook(drop_pending_updates=True)

        # Планировщик отложенных постов (Этап 3)
        setup_scheduler(bot)
        await restore_jobs()  # восстановить задачи из БД после рестарта

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
