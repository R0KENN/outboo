"""Базовые команды: старт, проверка работоспособности, справка."""

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

router = Router(name="start")


@router.message(Command("ping"))
async def cmd_ping(message: Message, is_admin: bool) -> None:
    role = "администратор" if is_admin else "участник"
    await message.answer(f"pong — вы определены как: {role}")


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """Справка по всем командам бота."""
    text = (
        "<b>📖 Команды бота</b>\n\n"
        "<b>Настройки чата (только админ):</b>\n"
        "/settings — панель настроек (фильтры, параметры)\n"
        "/setwelcome &lt;текст&gt; — текст приветствия ({name} — имя)\n"
        "/setrules &lt;текст&gt; — текст правил\n\n"
        "<b>Модерация (админ/модератор, ответом на сообщение):</b>\n"
        "/ban, /unban — бан и снятие бана\n"
        "/kick — удалить из чата\n"
        "/mute 30m, /unmute — мут на срок и размут\n"
        "/warn, /unwarn — выдать/снять предупреждение\n"
        "/warns — посмотреть число предупреждений\n"
        "/resetwarns — обнулить предупреждения\n\n"
        "<b>Словари и роли (только админ):</b>\n"
        "/addword, /delword, /words — стоп-слова\n"
        "/adddomain, /deldomain, /domains — белый список доменов\n"
        "/addmod mute warn, /delmod, /mods — модераторы\n"
        "/log — журнал действий модерации\n\n"
        "<b>Статистика (админ/модератор):</b>\n"
        "/stats day|week|month — отчёт за период\n\n"
        "<b>Реферальная программа (в личке):</b>\n"
        "/ref — ваша ссылка и число приглашённых\n"
        "/reftop — рейтинг пригласителей\n\n"
        "<b>Конкурсы (в личке, для админов):</b>\n"
        "/newgiveaway — создать конкурс\n"
        "/endgiveaway <id> — подвести итоги досрочно\n\n"
        "<b>Экспорт данных (владелец, в личке):</b>\n"
        "/sheettest — проверить подключение к Google Sheets\n"
        "/export — выгрузить данные в таблицу\n\n"
        "<b>Автопостинг (в личке с ботом):</b>\n"
        "/newpost — создать отложенный пост\n"
        "/queue — очередь запланированных постов\n"
        "(отмена и перенос — кнопками внутри /queue)\n"
    )
    await message.answer(text)
