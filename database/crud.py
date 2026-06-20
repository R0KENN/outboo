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
    batch_id: str = "",
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
        batch_id=batch_id,
        status="pending",
    )
    session.add(post)
    await session.commit()
    await session.refresh(post)
    return post


async def get_post(session: AsyncSession, post_id: int) -> ScheduledPost | None:
    """Возвращает пост по id."""
    return await session.get(ScheduledPost, post_id)

async def get_post(session, post_id: int):
    """Возвращает запланированный пост по id или None."""
    from database.models import ScheduledPost
    from sqlalchemy import select
    return (await session.execute(
        select(ScheduledPost).where(ScheduledPost.id == post_id)
    )).scalar_one_or_none()

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

async def list_pending_grouped(
    session: AsyncSession, created_by: int | None = None
) -> list[list[ScheduledPost]]:
    """Возвращает pending-посты, сгруппированные по batch_id.

    Каждая группа — список постов одной мультиканальной публикации.
    Посты без batch_id (старые/одиночные) образуют группу из одного элемента.
    """
    posts = await list_pending_posts(session, created_by)
    groups: dict[str, list[ScheduledPost]] = {}
    singles: list[list[ScheduledPost]] = []
    for p in posts:
        if p.batch_id:
            groups.setdefault(p.batch_id, []).append(p)
        else:
            singles.append([p])
    # Группы + одиночные, отсортированные по ближайшему времени публикации
    result = list(groups.values()) + singles
    result.sort(key=lambda grp: min(x.publish_at for x in grp))
    return result


async def cancel_batch(session: AsyncSession, batch_id: str) -> list[int]:
    """Отменяет все pending-посты группы. Возвращает список отменённых id."""
    stmt = select(ScheduledPost).where(
        ScheduledPost.batch_id == batch_id,
        ScheduledPost.status == "pending",
    )
    posts = list((await session.execute(stmt)).scalars().all())
    cancelled = []
    for p in posts:
        p.status = "cancelled"
        cancelled.append(p.id)
    if cancelled:
        await session.commit()
    return cancelled

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

# ──────────────────────────────────────────────────────────────────────────
# База подписчиков и рассылки (раздел 4.6 ТЗ)
# ──────────────────────────────────────────────────────────────────────────
from database.models import Subscriber


async def upsert_subscriber(
    session: AsyncSession,
    user_id: int,
    username: str = "",
    full_name: str = "",
) -> None:
    """Регистрирует подписчика или обновляет его данные.

    Вызывается на /start в личке. Если человек вернулся после блокировки —
    снова помечаем его активным.
    """
    sub = await session.get(Subscriber, user_id)
    if sub is None:
        sub = Subscriber(
            user_id=user_id, username=username or "",
            full_name=full_name or "", is_active=True,
        )
        session.add(sub)
    else:
        sub.username = username or sub.username
        sub.full_name = full_name or sub.full_name
        sub.is_active = True
    await session.commit()


async def get_active_subscriber_ids(session: AsyncSession) -> list[int]:
    """Список id всех активных подписчиков (для рассылки)."""
    stmt = select(Subscriber.user_id).where(Subscriber.is_active.is_(True))
    return list((await session.execute(stmt)).scalars().all())


async def deactivate_subscriber(session: AsyncSession, user_id: int) -> None:
    """Помечает подписчика неактивным (заблокировал бота)."""
    sub = await session.get(Subscriber, user_id)
    if sub is not None and sub.is_active:
        sub.is_active = False
        await session.commit()


async def count_subscribers(session: AsyncSession) -> tuple[int, int]:
    """Возвращает (всего, активных) подписчиков."""
    total = (await session.execute(
        select(_func.count()).select_from(Subscriber)
    )).scalar_one()
    active = (await session.execute(
        select(_func.count()).select_from(Subscriber)
        .where(Subscriber.is_active.is_(True))
    )).scalar_one()
    return int(total), int(active)

# ──────────────────────────────────────────────────────────────────────────
# Реферальная система (раздел 4.6 ТЗ)
# ──────────────────────────────────────────────────────────────────────────
from database.models import Referral


async def register_referral(
    session: AsyncSession, invited_id: int, referrer_id: int
) -> bool:
    """Фиксирует приглашение. True, если засчитано впервые.

    Не засчитывает самоприглашение и повторный приход одного и того же
    приглашённого (за счёт первичного ключа invited_id).
    """
    if invited_id == referrer_id:
        return False
    existing = await session.get(Referral, invited_id)
    if existing is not None:
        return False
    session.add(Referral(invited_id=invited_id, referrer_id=referrer_id))
    await session.commit()
    return True


async def count_referrals(session: AsyncSession, referrer_id: int) -> int:
    """Сколько человек пригласил пользователь."""
    stmt = (
        select(_func.count())
        .select_from(Referral)
        .where(Referral.referrer_id == referrer_id)
    )
    return int((await session.execute(stmt)).scalar_one())


async def get_referrer(session: AsyncSession, invited_id: int) -> int | None:
    """Возвращает id пригласившего для данного пользователя или None."""
    ref = await session.get(Referral, invited_id)
    return ref.referrer_id if ref else None


async def top_referrers(
    session: AsyncSession, limit: int = 10
) -> list[tuple[int, int]]:
    """Топ пригласивших: список (referrer_id, число_приглашённых)."""
    stmt = (
        select(Referral.referrer_id, _func.count().label("cnt"))
        .group_by(Referral.referrer_id)
        .order_by(_func.count().desc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    return [(int(rid), int(cnt)) for rid, cnt in rows]

# ──────────────────────────────────────────────────────────────────────────
# Конкурсы и розыгрыши (раздел 4.6 ТЗ)
# ──────────────────────────────────────────────────────────────────────────
from database.models import Giveaway, GiveawayParticipant


async def create_giveaway(
    session: AsyncSession,
    title: str,
    winners_count: int,
    require_channel_id: int,
    require_channel_title: str,
    finish_at: _dt,
    created_by: int,
) -> Giveaway:
    """Создаёт конкурс в статусе active."""
    g = Giveaway(
        title=title,
        winners_count=winners_count,
        require_channel_id=require_channel_id,
        require_channel_title=require_channel_title,
        finish_at=finish_at,
        created_by=created_by,
        status="active",
    )
    session.add(g)
    await session.commit()
    await session.refresh(g)
    return g


async def set_giveaway_post(
    session: AsyncSession, giveaway_id: int, post_chat_id: int, post_message_id: int
) -> None:
    """Запоминает координаты опубликованного поста конкурса."""
    g = await session.get(Giveaway, giveaway_id)
    if g is not None:
        g.post_chat_id = post_chat_id
        g.post_message_id = post_message_id
        await session.commit()


async def get_giveaway(session: AsyncSession, giveaway_id: int) -> Giveaway | None:
    return await session.get(Giveaway, giveaway_id)


async def add_participant(
    session: AsyncSession, giveaway_id: int, user_id: int,
    full_name: str, username: str,
) -> bool:
    """Добавляет участника. False, если уже участвует."""
    stmt = select(GiveawayParticipant).where(
        GiveawayParticipant.giveaway_id == giveaway_id,
        GiveawayParticipant.user_id == user_id,
    )
    if (await session.execute(stmt)).scalar_one_or_none() is not None:
        return False
    session.add(GiveawayParticipant(
        giveaway_id=giveaway_id, user_id=user_id,
        full_name=full_name or "", username=username or "",
    ))
    await session.commit()
    return True


async def count_participants(session: AsyncSession, giveaway_id: int) -> int:
    stmt = (
        select(_func.count())
        .select_from(GiveawayParticipant)
        .where(GiveawayParticipant.giveaway_id == giveaway_id)
    )
    return int((await session.execute(stmt)).scalar_one())


async def list_participants(
    session: AsyncSession, giveaway_id: int
) -> list[GiveawayParticipant]:
    stmt = select(GiveawayParticipant).where(
        GiveawayParticipant.giveaway_id == giveaway_id
    )
    return list((await session.execute(stmt)).scalars().all())


async def set_giveaway_status(
    session: AsyncSession, giveaway_id: int, status: str
) -> None:
    g = await session.get(Giveaway, giveaway_id)
    if g is not None:
        g.status = status
        await session.commit()


async def list_active_giveaways(session: AsyncSession) -> list[Giveaway]:
    """Активные конкурсы (для восстановления таймеров после рестарта)."""
    stmt = select(Giveaway).where(Giveaway.status == "active")
    return list((await session.execute(stmt)).scalars().all())

# ──────────────────────────────────────────────────────────────────────────
# Реестр управляемых чатов/каналов (список + индивидуальные настройки)
# ──────────────────────────────────────────────────────────────────────────
from database.models import ManagedChat


async def upsert_managed_chat(
    session: AsyncSession,
    chat_id: int,
    chat_type: str,
    title: str,
    username: str,
    is_admin: bool,
    added_by: int,
) -> ManagedChat:
    """Создаёт или обновляет запись о чате, куда добавлен бот."""
    obj = await session.get(ManagedChat, chat_id)
    if obj is None:
        obj = ManagedChat(
            chat_id=chat_id,
            chat_type=chat_type,
            title=title,
            username=username,
            is_admin=is_admin,
            added_by=added_by,
            is_active=True,
        )
        session.add(obj)
    else:
        obj.chat_type = chat_type
        obj.title = title or obj.title
        obj.username = username
        obj.is_admin = is_admin
        obj.is_active = True
        # added_by сохраняем первоначальный, если он уже был
        if not obj.added_by and added_by:
            obj.added_by = added_by
    await session.commit()
    await session.refresh(obj)
    return obj


async def deactivate_managed_chat(session: AsyncSession, chat_id: int) -> None:
    """Помечает чат как неактивный (бота удалили/кикнули)."""
    obj = await session.get(ManagedChat, chat_id)
    if obj is not None:
        obj.is_active = False
        await session.commit()


async def get_managed_chat(
    session: AsyncSession, chat_id: int
) -> ManagedChat | None:
    """Возвращает запись чата по id."""
    return await session.get(ManagedChat, chat_id)


async def list_managed_chats(
    session: AsyncSession, only_active: bool = True
) -> list[ManagedChat]:
    """Список всех чатов/каналов, где есть бот (для глобальных админов бота)."""
    stmt = select(ManagedChat)
    if only_active:
        stmt = stmt.where(ManagedChat.is_active.is_(True))
    stmt = stmt.order_by(ManagedChat.title.asc())
    return list((await session.execute(stmt)).scalars().all())
