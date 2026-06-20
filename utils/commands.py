"""Установка меню команд бота с разделением по областям видимости."""
from aiogram import Bot
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeAllChatAdministrators,
)


async def set_bot_commands(bot: Bot) -> None:
    """Задаёт разные наборы команд для лички и для админов в группах."""

    # Команды, видимые в личке с ботом (всем пользователям)
    private_commands = [
        BotCommand(command="start", description="Запустить бота"),
        BotCommand(command="help", description="Справка по командам"),
        BotCommand(command="ref", description="Моя реферальная ссылка"),
        BotCommand(command="reftop", description="Рейтинг пригласителей"),
        BotCommand(command="newpost", description="Создать отложенный пост"),
        BotCommand(command="queue", description="Очередь постов"),
        BotCommand(command="newgiveaway", description="Создать конкурс"),
    ]

    # Команды, видимые только администраторам внутри групп
    admin_commands = [
        BotCommand(command="settings", description="Настройки чата"),
        BotCommand(command="stats", description="Статистика чата"),
        BotCommand(command="ban", description="Забанить (ответом)"),
        BotCommand(command="mute", description="Замутить, напр. /mute 30m"),
        BotCommand(command="warn", description="Предупреждение (ответом)"),
        BotCommand(command="warns", description="Сколько предупреждений"),
        BotCommand(command="addmod", description="Назначить модератора"),
        BotCommand(command="words", description="Список стоп-слов"),
        BotCommand(command="log", description="Журнал модерации"),
    ]

    await bot.set_my_commands(
        private_commands, scope=BotCommandScopeAllPrivateChats()
    )
    await bot.set_my_commands(
        admin_commands, scope=BotCommandScopeAllChatAdministrators()
    )
