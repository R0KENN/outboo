"""Установка меню команд бота. Оставляем в личке только /start.

Вся навигация ведётся через инлайн-меню (кнопка «Главное меню» по /start).
Для админов в группах сохраняем рабочие команды модерации.
"""
from aiogram import Bot
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeAllChatAdministrators,
)


async def set_bot_commands(bot: Bot) -> None:
    """В личке — только /start; в группах админам — команды модерации."""

    # Личка: единственная команда — запуск инлайн-меню
    private_commands = [
        BotCommand(command="start", description="Открыть меню"),
    ]

    # В группах админам оставляем рабочие команды модерации
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
