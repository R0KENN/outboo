"""Администрирование: роли модераторов, словари, белый список, лог (разделы 4.1, 4.4)."""

import logging

from aiogram import Router
from aiogram.enums import ChatMemberStatus
from aiogram.filters import Command
from aiogram.types import Message

from database import crud
from database.engine import session_factory
from utils.datetime_parse import to_local_str
from utils.parse import get_target_id

logger = logging.getLogger(__name__)
router = Router(name="admin")

# Допустимые права младшего модератора
VALID_PERMS = {"mute", "warn", "ban", "kick"}


async def _is_full_admin(message: Message) -> bool:
    """Проверяет, что отправитель — полноценный админ чата (не младший модер).

    Управлять ролями, словарями и логом может только настоящий админ/создатель,
    чтобы младший модератор не повышал сам себя.
    """
    if message.chat.type not in ("group", "supergroup"):
        return False
    try:
        member = await message.bot.get_chat_member(message.chat.id, message.from_user.id)
        return member.status in (
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.CREATOR,
        )
    except Exception:
        return False


# ──────────────────────────────────────────────────────────────────────────
# Роли модераторов (раздел 4.4)
# ──────────────────────────────────────────────────────────────────────────
@router.message(Command("addmod"))
async def cmd_addmod(message: Message) -> None:
    """Назначает младшего модератора. Ответом на сообщение + список прав.

    Пример: ответом на сообщение пользователя написать /addmod mute warn
    Без указания прав по умолчанию выдаётся mute,warn.
    """
    if not await _is_full_admin(message):
        await message.answer("Управлять модераторами может только администратор чата.")
        return
    target_id, name = get_target_id(message)
    if target_id is None:
        await message.answer("Ответьте на сообщение пользователя.\nПример: /addmod mute warn")
        return

    parts = (message.text or "").split()[1:]
    perms = [p.lower() for p in parts if p.lower() in VALID_PERMS]
    if not perms:
        perms = ["mute", "warn"]
    perms_csv = ",".join(sorted(set(perms)))

    async with session_factory() as session:
        await crud.add_moderator(session, message.chat.id, target_id, perms_csv)
    await message.answer(f"{name} назначен модератором. Права: {perms_csv}")


@router.message(Command("delmod"))
async def cmd_delmod(message: Message) -> None:
    """Снимает младшего модератора (ответом на его сообщение)."""
    if not await _is_full_admin(message):
        await message.answer("Управлять модераторами может только администратор чата.")
        return
    target_id, name = get_target_id(message)
    if target_id is None:
        await message.answer("Ответьте на сообщение пользователя.")
        return
    async with session_factory() as session:
        ok = await crud.remove_moderator(session, message.chat.id, target_id)
    await message.answer(f"{name} больше не модератор." if ok else f"{name} не был модератором.")


@router.message(Command("mods"))
async def cmd_mods(message: Message) -> None:
    """Список назначенных модераторов чата."""
    if not await _is_full_admin(message):
        await message.answer("Список доступен только администратору чата.")
        return
    async with session_factory() as session:
        mods = await crud.list_moderators(session, message.chat.id)
    if not mods:
        await message.answer("Младшие модераторы не назначены.")
        return
    lines = ["👮 <b>Модераторы чата:</b>\n"]
    for m in mods:
        lines.append(f"<code>{m.user_id}</code> — права: {m.permissions}")
    await message.answer("\n".join(lines))


# ──────────────────────────────────────────────────────────────────────────
# Словарь стоп-слов (раздел 4.1)
# ──────────────────────────────────────────────────────────────────────────
@router.message(Command("addword"))
async def cmd_addword(message: Message) -> None:
    """Добавляет стоп-слово: /addword слово."""
    if not await _is_full_admin(message):
        return
    word = message.text.partition(" ")[2].strip()
    if not word:
        await message.answer("Формат: /addword &lt;слово&gt;")
        return
    async with session_factory() as session:
        ok = await crud.add_stopword(session, message.chat.id, word)
    await message.answer("Слово добавлено." if ok else "Такое слово уже есть.")


@router.message(Command("delword"))
async def cmd_delword(message: Message) -> None:
    """Удаляет стоп-слово: /delword слово."""
    if not await _is_full_admin(message):
        return
    word = message.text.partition(" ")[2].strip()
    if not word:
        await message.answer("Формат: /delword &lt;слово&gt;")
        return
    async with session_factory() as session:
        ok = await crud.remove_stopword(session, message.chat.id, word)
    await message.answer("Слово удалено." if ok else "Такого слова нет в списке.")


@router.message(Command("words"))
async def cmd_words(message: Message) -> None:
    """Показывает словарь стоп-слов чата."""
    if not await _is_full_admin(message):
        return
    async with session_factory() as session:
        words = await crud.list_stopwords(session, message.chat.id)
    if not words:
        await message.answer("Словарь стоп-слов пуст.")
        return
    await message.answer("🚫 <b>Стоп-слова:</b>\n" + ", ".join(f"<code>{w}</code>" for w in words))


# ──────────────────────────────────────────────────────────────────────────
# Белый список доменов (раздел 4.1)
# ──────────────────────────────────────────────────────────────────────────
@router.message(Command("adddomain"))
async def cmd_adddomain(message: Message) -> None:
    """Добавляет домен в белый список: /adddomain example.com."""
    if not await _is_full_admin(message):
        return
    domain = message.text.partition(" ")[2].strip()
    if not domain:
        await message.answer("Формат: /adddomain &lt;домен&gt;, например example.com")
        return
    async with session_factory() as session:
        ok = await crud.add_domain(session, message.chat.id, domain)
    await message.answer("Домен добавлен в белый список." if ok else "Такой домен уже есть.")


@router.message(Command("deldomain"))
async def cmd_deldomain(message: Message) -> None:
    """Удаляет домен из белого списка: /deldomain example.com."""
    if not await _is_full_admin(message):
        return
    domain = message.text.partition(" ")[2].strip()
    if not domain:
        await message.answer("Формат: /deldomain &lt;домен&gt;")
        return
    async with session_factory() as session:
        ok = await crud.remove_domain(session, message.chat.id, domain)
    await message.answer("Домен удалён." if ok else "Такого домена нет в списке.")


@router.message(Command("domains"))
async def cmd_domains(message: Message) -> None:
    """Показывает белый список доменов чата."""
    if not await _is_full_admin(message):
        return
    async with session_factory() as session:
        domains = await crud.list_domains(session, message.chat.id)
    if not domains:
        await message.answer("Белый список доменов пуст (разрешённых ссылок нет).")
        return
    await message.answer(
        "✅ <b>Разрешённые домены:</b>\n" + ", ".join(f"<code>{d}</code>" for d in domains)
    )


# ──────────────────────────────────────────────────────────────────────────
# Журнал модерации (раздел 4.4)
# ──────────────────────────────────────────────────────────────────────────
@router.message(Command("log"))
async def cmd_log(message: Message) -> None:
    """Показывает последние записи журнала модерации."""
    if not await _is_full_admin(message):
        await message.answer("Журнал доступен только администратору чата.")
        return
    async with session_factory() as session:
        entries = await crud.get_moderation_log(session, message.chat.id, limit=20)
    if not entries:
        await message.answer("Журнал модерации пуст.")
        return
    lines = ["🗒 <b>Последние действия модерации:</b>\n"]
    for e in entries:
        when = to_local_str(e.created_at)
        reason = f" — {e.reason}" if e.reason else ""
        lines.append(
            f"{when}: <b>{e.action}</b> "
            f"<code>{e.actor_id}</code> → <code>{e.target_id}</code>{reason}"
        )
    await message.answer("\n".join(lines))
