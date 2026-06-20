"""Слой доступа к данным. Вся работа с БД — только здесь (раздел 3 ТЗ)."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import ChatSettings, Moderator


async def get_or_create_chat_settings(
    session: AsyncSession, chat_id: int
) -> ChatSettings:
    """Возвращает настройки чата, создавая запись со значениями по умолчанию."""
    obj = await session.get(ChatSettings, chat_id)
    if obj is None:
        obj = ChatSettings(chat_id=chat_id)
        session.add(obj)
        await session.commit()
        await session.refresh(obj)
    return obj


async def get_moderator(
    session: AsyncSession, chat_id: int, user_id: int
) -> Moderator | None:
    """Возвращает запись младшего модератора, если он назначен."""
    stmt = select(Moderator).where(
        Moderator.chat_id == chat_id, Moderator.user_id == user_id
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()

from datetime import datetime, timezone
from database.models import MemberJoin


async def record_join(session: AsyncSession, chat_id: int, user_id: int) -> None:
    """Запоминает время входа участника (для карантина)."""
    stmt = select(MemberJoin).where(
        MemberJoin.chat_id == chat_id, MemberJoin.user_id == user_id
    )
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing is None:
        session.add(MemberJoin(chat_id=chat_id, user_id=user_id))
        await session.commit()


async def get_join_time(
    session: AsyncSession, chat_id: int, user_id: int
) -> datetime | None:
    """Возвращает время входа участника или None, если запись не найдена."""
    stmt = select(MemberJoin).where(
        MemberJoin.chat_id == chat_id, MemberJoin.user_id == user_id
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    return row.joined_at if row else None

# ──────────────────────────────────────────────────────────────────────────
# Отложенные посты (раздел 4.3 ТЗ)
# ──────────────────────────────────────────────────────────────────────────
from datetime import datetime as _dt
from database.models import ScheduledPost


async def create_scheduled_post(
    session: AsyncSession,
    channel_id: int,
    text: str,
    media: str,
    buttons: str,
    parse_mode: str,
    publish_at: _dt,
    delete_after: int,
    created_by: int,
    repeat_rule: str = "",
) -> ScheduledPost:
    """Создаёт запись отложенного поста в очереди и возвращает её."""
    post = ScheduledPost(
        channel_id=channel_id,
        text=text,
        media=media,
        buttons=buttons,
        parse_mode=parse_mode,
        publish_at=publish_at,
        delete_after=delete_after,
        created_by=created_by,
        repeat_rule=repeat_rule,
        status="pending",
    )
    session.add(post)
    await session.commit()
    await session.refresh(post)
    return post


async def get_post(session: AsyncSession, post_id: int) -> ScheduledPost | None:
    """Возвращает пост по id."""
    return await session.get(ScheduledPost, post_id)


async def list_pending_posts(
    session: AsyncSession, created_by: int | None = None
) -> list[ScheduledPost]:
    """Возвращает запланированные (pending) посты, опц. только конкретного автора."""
    stmt = select(ScheduledPost).where(ScheduledPost.status == "pending")
    if created_by is not None:
        stmt = stmt.where(ScheduledPost.created_by == created_by)
    stmt = stmt.order_by(ScheduledPost.publish_at.asc())
    return list((await session.execute(stmt)).scalars().all())


async def list_due_posts(session: AsyncSession, now: _dt) -> list[ScheduledPost]:
    """Возвращает посты, время публикации которых уже наступило, но не отправленные."""
    stmt = select(ScheduledPost).where(
        ScheduledPost.status == "pending",
        ScheduledPost.publish_at <= now,
    )
    return list((await session.execute(stmt)).scalars().all())


async def set_post_status(
    session: AsyncSession, post_id: int, status: str
) -> None:
    """Меняет статус поста (sent|failed|cancelled|pending)."""
    post = await session.get(ScheduledPost, post_id)
    if post is not None:
        post.status = status
        await session.commit()


async def cancel_post(session: AsyncSession, post_id: int) -> bool:
    """Помечает пост как отменённый. True, если пост существовал и был pending."""
    post = await session.get(ScheduledPost, post_id)
    if post is None or post.status != "pending":
        return False
    post.status = "cancelled"
    await session.commit()
    return True


async def reschedule_post(
    session: AsyncSession, post_id: int, new_time: _dt
) -> bool:
    """Переносит время публикации pending-поста. True при успехе."""
    post = await session.get(ScheduledPost, post_id)
    if post is None or post.status != "pending":
        return False
    post.publish_at = new_time
    await session.commit()
    return True

# ──────────────────────────────────────────────────────────────────────────
# Статистика (раздел 4.5 ТЗ)
# ──────────────────────────────────────────────────────────────────────────
from datetime import date as _date, timedelta as _timedelta
from sqlalchemy import func as _func
from database.models import Stat, Moderator, StopWord, AllowedDomain, ModerationLog


async def bump_stat(
    session: AsyncSession, chat_id: int, metric: str, amount: int = 1
) -> None:
    """Увеличивает счётчик метрики за сегодня. Переносимый upsert (select+update).

    metric: new_members | deleted_spam | deleted_profanity | bans | mutes |
            warns | messages | ...
    """
    today = _date.today().isoformat()
    stmt = select(Stat).where(
        Stat.chat_id == chat_id, Stat.date == today, Stat.metric == metric
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        session.add(Stat(chat_id=chat_id, date=today, metric=metric, value=amount))
    else:
        row.value += amount
    await session.commit()


async def get_stats_period(
    session: AsyncSession, chat_id: int, days: int
) -> dict[str, int]:
    """Суммирует метрики за последние N дней. Возвращает {metric: сумма}."""
    since = (_date.today() - _timedelta(days=days - 1)).isoformat()
    stmt = (
        select(Stat.metric, _func.sum(Stat.value))
        .where(Stat.chat_id == chat_id, Stat.date >= since)
        .group_by(Stat.metric)
    )
    rows = (await session.execute(stmt)).all()
    return {metric: int(total or 0) for metric, total in rows}


# ──────────────────────────────────────────────────────────────────────────
# Роли модераторов (раздел 4.4 ТЗ)
# ──────────────────────────────────────────────────────────────────────────
async def add_moderator(
    session: AsyncSession, chat_id: int, user_id: int, permissions: str
) -> Moderator:
    """Назначает (или обновляет права) младшего модератора."""
    stmt = select(Moderator).where(
        Moderator.chat_id == chat_id, Moderator.user_id == user_id
    )
    mod = (await session.execute(stmt)).scalar_one_or_none()
    if mod is None:
        mod = Moderator(chat_id=chat_id, user_id=user_id, permissions=permissions)
        session.add(mod)
    else:
        mod.permissions = permissions
    await session.commit()
    await session.refresh(mod)
    return mod


async def remove_moderator(
    session: AsyncSession, chat_id: int, user_id: int
) -> bool:
    """Снимает модератора. True, если запись существовала."""
    stmt = select(Moderator).where(
        Moderator.chat_id == chat_id, Moderator.user_id == user_id
    )
    mod = (await session.execute(stmt)).scalar_one_or_none()
    if mod is None:
        return False
    await session.delete(mod)
    await session.commit()
    return True


async def list_moderators(
    session: AsyncSession, chat_id: int
) -> list[Moderator]:
    """Список модераторов чата."""
    stmt = select(Moderator).where(Moderator.chat_id == chat_id)
    return list((await session.execute(stmt)).scalars().all())


# ──────────────────────────────────────────────────────────────────────────
# Словарь стоп-слов (раздел 4.1 ТЗ)
# ──────────────────────────────────────────────────────────────────────────
async def add_stopword(session: AsyncSession, chat_id: int, word: str) -> bool:
    """Добавляет стоп-слово. False, если оно уже есть."""
    word = word.lower().strip()
    if not word:
        return False
    stmt = select(StopWord).where(
        StopWord.chat_id == chat_id, StopWord.word == word
    )
    if (await session.execute(stmt)).scalar_one_or_none() is not None:
        return False
    session.add(StopWord(chat_id=chat_id, word=word))
    await session.commit()
    return True


async def remove_stopword(session: AsyncSession, chat_id: int, word: str) -> bool:
    """Удаляет стоп-слово. True, если оно было."""
    word = word.lower().strip()
    stmt = select(StopWord).where(
        StopWord.chat_id == chat_id, StopWord.word == word
    )
    obj = (await session.execute(stmt)).scalar_one_or_none()
    if obj is None:
        return False
    await session.delete(obj)
    await session.commit()
    return True


async def list_stopwords(session: AsyncSession, chat_id: int) -> list[str]:
    """Список стоп-слов чата."""
    stmt = select(StopWord.word).where(StopWord.chat_id == chat_id)
    return list((await session.execute(stmt)).scalars().all())


# ──────────────────────────────────────────────────────────────────────────
# Белый список доменов (раздел 4.1 ТЗ)
# ──────────────────────────────────────────────────────────────────────────
async def add_domain(session: AsyncSession, chat_id: int, domain: str) -> bool:
    """Добавляет домен в белый список. False, если он уже есть."""
    domain = domain.lower().strip().lstrip("@").removeprefix("https://").removeprefix("http://").removeprefix("www.")
    domain = domain.split("/")[0]
    if not domain:
        return False
    stmt = select(AllowedDomain).where(
        AllowedDomain.chat_id == chat_id, AllowedDomain.domain == domain
    )
    if (await session.execute(stmt)).scalar_one_or_none() is not None:
        return False
    session.add(AllowedDomain(chat_id=chat_id, domain=domain))
    await session.commit()
    return True


async def remove_domain(session: AsyncSession, chat_id: int, domain: str) -> bool:
    """Удаляет домен из белого списка. True, если он был."""
    domain = domain.lower().strip().lstrip("@").removeprefix("https://").removeprefix("http://").removeprefix("www.")
    domain = domain.split("/")[0]
    stmt = select(AllowedDomain).where(
        AllowedDomain.chat_id == chat_id, AllowedDomain.domain == domain
    )
    obj = (await session.execute(stmt)).scalar_one_or_none()
    if obj is None:
        return False
    await session.delete(obj)
    await session.commit()
    return True


async def list_domains(session: AsyncSession, chat_id: int) -> list[str]:
    """Список доменов белого списка чата."""
    stmt = select(AllowedDomain.domain).where(AllowedDomain.chat_id == chat_id)
    return list((await session.execute(stmt)).scalars().all())


# ──────────────────────────────────────────────────────────────────────────
# Лог модерации (раздел 4.4 ТЗ)
# ──────────────────────────────────────────────────────────────────────────
async def get_moderation_log(
    session: AsyncSession, chat_id: int, limit: int = 20
) -> list[ModerationLog]:
    """Последние записи журнала модерации (новые сверху)."""
    stmt = (
        select(ModerationLog)
        .where(ModerationLog.chat_id == chat_id)
        .order_by(ModerationLog.created_at.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())
