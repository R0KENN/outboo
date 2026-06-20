"""ORM-модели таблиц (раздел 5 ТЗ)."""
from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.engine import Base


class ChatSettings(Base):
    """Настройки конкретного чата (флаги фильтров и их параметры)."""
    __tablename__ = "chat_settings"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    # Флаги включения фильтров
    antispam_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    antimat_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    antiflood_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    captcha_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    welcome_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    clean_service_msgs: Mapped[bool] = mapped_column(Boolean, default=False)

    # Параметры
    warn_limit: Mapped[int] = mapped_column(Integer, default=3)
    warn_action: Mapped[str] = mapped_column(String(16), default="mute")  # mute|ban
    captcha_timeout: Mapped[int] = mapped_column(Integer, default=120)  # сек
    flood_messages: Mapped[int] = mapped_column(Integer, default=5)
    flood_seconds: Mapped[int] = mapped_column(Integer, default=5)
    flood_mute_seconds: Mapped[int] = mapped_column(Integer, default=300)
    newbie_quarantine_hours: Mapped[int] = mapped_column(Integer, default=24)
    captcha_type: Mapped[str] = mapped_column(String(16), default="button")  # button|math
    quarantine_enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    # Тексты
    welcome_text: Mapped[str] = mapped_column(
        Text, default="Добро пожаловать, {name}!"
    )
    rules_text: Mapped[str] = mapped_column(Text, default="")
    welcome_delete_after: Mapped[int] = mapped_column(Integer, default=0)  # 0=не удалять

    # Связи
    warns: Mapped[list["Warn"]] = relationship(back_populates="chat", cascade="all, delete-orphan")
    stopwords: Mapped[list["StopWord"]] = relationship(back_populates="chat", cascade="all, delete-orphan")
    allowed_domains: Mapped[list["AllowedDomain"]] = relationship(back_populates="chat", cascade="all, delete-orphan")


class Warn(Base):
    """Предупреждения участников (раздел 4.1)."""
    __tablename__ = "warns"
    __table_args__ = (UniqueConstraint("chat_id", "user_id", name="uq_warn_chat_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chat_settings.chat_id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    history: Mapped[str] = mapped_column(Text, default="")  # JSON-история выдачи
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    chat: Mapped["ChatSettings"] = relationship(back_populates="warns")


class Moderator(Base):
    """Младшие модераторы с ограниченным набором прав (раздел 4.4)."""
    __tablename__ = "moderators"
    __table_args__ = (UniqueConstraint("chat_id", "user_id", name="uq_mod_chat_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    # Права в виде набора флагов, хранятся как CSV: "mute,warn"
    permissions: Mapped[str] = mapped_column(String(255), default="mute,warn")
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ScheduledPost(Base):
    """Отложенные посты (раздел 4.3)."""
    __tablename__ = "scheduled_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[int] = mapped_column(BigInteger, index=True)
    text: Mapped[str] = mapped_column(Text, default="")
    media: Mapped[str] = mapped_column(Text, default="")  # JSON: тип+file_id, альбомы
    buttons: Mapped[str] = mapped_column(Text, default="")  # JSON inline-кнопок
    parse_mode: Mapped[str] = mapped_column(String(16), default="HTML")
    publish_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    delete_after: Mapped[int] = mapped_column(Integer, default=0)  # сек, 0=не удалять
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|sent|failed|cancelled
    repeat_rule: Mapped[str] = mapped_column(String(32), default="")  # daily|weekly (премиум)
    created_by: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ModerationLog(Base):
    """Лог модераторских действий (раздел 4.4)."""
    __tablename__ = "moderation_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    action: Mapped[str] = mapped_column(String(32))  # ban|mute|warn|...
    actor_id: Mapped[int] = mapped_column(BigInteger)   # кто
    target_id: Mapped[int] = mapped_column(BigInteger)  # над кем
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class Stat(Base):
    """Агрегированная статистика по чатам и датам (раздел 4.5)."""
    __tablename__ = "stats"
    __table_args__ = (UniqueConstraint("chat_id", "date", "metric", name="uq_stat"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    date: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD
    metric: Mapped[str] = mapped_column(String(32))  # new_members|deleted_spam|bans|...
    value: Mapped[int] = mapped_column(Integer, default=0)


class StopWord(Base):
    """Словарь стоп-слов антимат-фильтра (раздел 4.1)."""
    __tablename__ = "stopwords"
    __table_args__ = (UniqueConstraint("chat_id", "word", name="uq_stopword"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chat_settings.chat_id", ondelete="CASCADE"))
    word: Mapped[str] = mapped_column(String(128))

    chat: Mapped["ChatSettings"] = relationship(back_populates="stopwords")


class AllowedDomain(Base):
    """Белый список доменов для антиспам-фильтра (раздел 4.1)."""
    __tablename__ = "allowed_domains"
    __table_args__ = (UniqueConstraint("chat_id", "domain", name="uq_domain"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(ForeignKey("chat_settings.chat_id", ondelete="CASCADE"))
    domain: Mapped[str] = mapped_column(String(255))

    chat: Mapped["ChatSettings"] = relationship(back_populates="allowed_domains")

class MemberJoin(Base):
    """Фиксация времени входа участника — для карантина новичков (раздел 4.2)."""
    __tablename__ = "member_joins"
    __table_args__ = (UniqueConstraint("chat_id", "user_id", name="uq_member_join"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

class Subscriber(Base):
    """База подписчиков бота для массовых рассылок (раздел 4.6).

    Запись создаётся, когда пользователь пишет боту /start в личку.
    Поле is_active снимается, если при рассылке выяснилось, что
    пользователь заблокировал бота (TelegramForbiddenError).
    """
    __tablename__ = "subscribers"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), default="")
    full_name: Mapped[str] = mapped_column(String(255), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

class Referral(Base):
    """Связь «кто кого пригласил» + счётчик приглашений (раздел 4.6).

    referrer_id — тот, кто пригласил; invited_id — приглашённый (PK,
    чтобы один приглашённый засчитался только один раз).
    """
    __tablename__ = "referrals"

    invited_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    referrer_id: Mapped[int] = mapped_column(BigInteger, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

class Giveaway(Base):
    """Конкурс/розыгрыш (раздел 4.6).

    require_channel_id — канал, подписка на который обязательна для участия
    (0, если условия подписки нет). post_chat_id/post_message_id — где висит
    пост с кнопкой «Участвовать», чтобы потом отредактировать его результатом.
    """
    __tablename__ = "giveaways"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(Text, default="")
    winners_count: Mapped[int] = mapped_column(Integer, default=1)
    require_channel_id: Mapped[int] = mapped_column(BigInteger, default=0)
    require_channel_title: Mapped[str] = mapped_column(String(255), default="")
    post_chat_id: Mapped[int] = mapped_column(BigInteger, default=0)
    post_message_id: Mapped[int] = mapped_column(Integer, default=0)
    finish_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|finished|cancelled
    created_by: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class GiveawayParticipant(Base):
    """Участник конкурса (раздел 4.6). Пара (giveaway_id, user_id) уникальна."""
    __tablename__ = "giveaway_participants"
    __table_args__ = (
        UniqueConstraint("giveaway_id", "user_id", name="uq_giveaway_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    giveaway_id: Mapped[int] = mapped_column(
        ForeignKey("giveaways.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    full_name: Mapped[str] = mapped_column(String(255), default="")
    username: Mapped[str] = mapped_column(String(64), default="")
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
