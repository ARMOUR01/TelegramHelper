from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    settings: Mapped["UserSettings"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan", lazy="selectin",
    )
    session: Mapped["TelegramSession | None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan", lazy="selectin",
    )
    api_keys: Mapped[list["ApiKey"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="selectin",
    )


class UserSettings(Base):
    __tablename__ = "user_settings"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    auto_reply_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    llm_provider: Mapped[str] = mapped_column(String(16), default="openai")
    use_heavy_model: Mapped[bool] = mapped_column(Boolean, default=False)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")  # IANA tz, например Europe/Moscow
    digest_time: Mapped[str] = mapped_column(String(5), default="09:00")  # HH:MM в timezone юзера
    digest_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    transcription_mode: Mapped[str] = mapped_column(String(16), default="local")  # local | api | hybrid
    auto_reply_cooldown_min: Mapped[int] = mapped_column(Integer, default=30)
    auto_reply_mode: Mapped[str] = mapped_column(String(8), default="static")  # static | smart
    auto_reply_text: Mapped[str] = mapped_column(
        Text,
        default="Сейчас не у телефона, отвечу как только смогу.",
    )
    ignore_archived: Mapped[bool] = mapped_column(Boolean, default=True)
    reminders_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    reminder_lead_hours: Mapped[int] = mapped_column(Integer, default=2)
    reminder_overdue_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    news_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    news_window_hours: Mapped[int] = mapped_column(Integer, default=24)
    news_digest_time: Mapped[str] = mapped_column(String(5), default="08:00")  # HH:MM в UTC
    # Vision / video-mozg тоггл. По умолчанию выключено: фото-описание и frame-sampling
    # видео тратит API-quotas, пусть включается явно.
    vision_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    video_vision_enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped[User] = relationship(back_populates="settings")


class TelegramSession(Base):
    __tablename__ = "telegram_sessions"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    api_id: Mapped[int] = mapped_column(BigInteger)
    api_hash_enc: Mapped[str] = mapped_column(Text)
    session_string_enc: Mapped[str] = mapped_column(Text)
    phone: Mapped[str] = mapped_column(String(32))
    account_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped[User] = relationship(back_populates="session")


class ApiKey(Base):
    __tablename__ = "api_keys"
    __table_args__ = (UniqueConstraint("user_id", "provider", name="uq_api_key_user_provider"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(16))
    key_enc: Mapped[str] = mapped_column(Text)

    user: Mapped[User] = relationship(back_populates="api_keys")


class Contact(Base):
    """Сохранённый профиль чата/контакта (peer)."""

    __tablename__ = "contacts"
    __table_args__ = (UniqueConstraint("user_id", "peer_id", name="uq_contact_user_peer"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    peer_id: Mapped[int] = mapped_column(BigInteger, index=True)
    peer_kind: Mapped[str] = mapped_column(String(16))  # user | chat | channel
    is_bot: Mapped[bool] = mapped_column(Boolean, default=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)  # лежит в архиве в Telegram
    is_news_source: Mapped[bool] = mapped_column(Boolean, default=False)  # помеченные для /news каналы
    display_name: Mapped[str] = mapped_column(String(256))
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    style_profile: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON-строка
    style_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_seen_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # «Сталкеринг» / personal CRM
    is_poi: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_mutual_contact: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_known_first_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    last_known_last_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    last_known_username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_known_photo_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_seen_online_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    dossier: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    dossier_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_reengagement_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class DeletedMessage(Base):
    """Лог удалённых «для всех» сообщений (то, что юзер ещё видит до удаления — на момент мирора)."""

    __tablename__ = "deleted_messages"
    __table_args__ = (
        Index("ix_deleted_user_peer_at", "user_id", "peer_id", "deleted_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    peer_id: Mapped[int] = mapped_column(BigInteger, index=True)
    peer_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    message_id: Mapped[int] = mapped_column(BigInteger)
    sender_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    is_outgoing: Mapped[bool] = mapped_column(Boolean, default=False)
    original_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deleted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class ProfileChange(Base):
    """Лог изменений профиля контакта (имя/username/аватар)."""

    __tablename__ = "profile_changes"
    __table_args__ = (
        Index("ix_profile_change_user_peer_at", "user_id", "peer_id", "changed_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    peer_id: Mapped[int] = mapped_column(BigInteger, index=True)
    kind: Mapped[str] = mapped_column(String(16))  # name | username | photo | mutual
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class OnlineObservation(Base):
    """Наблюдение «контакт был онлайн» — используется для heatmap'ов активности POI."""

    __tablename__ = "online_observations"
    __table_args__ = (
        Index("ix_online_obs_user_peer_at", "user_id", "peer_id", "observed_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    peer_id: Mapped[int] = mapped_column(BigInteger, index=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ChatEngagementEvent(Base):
    """Сигналы, что контакт «заходил в твой чат»:
    - read   : прочитал твоё исходящее сообщение
    - typing : начал печатать в чате с тобой
    - in     : написал тебе (incoming)
    - out    : ты сам написал (outgoing — для контекста)
    """

    __tablename__ = "chat_engagement_events"
    __table_args__ = (
        Index("ix_engagement_user_peer_at", "user_id", "peer_id", "at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    peer_id: Mapped[int] = mapped_column(BigInteger, index=True)
    kind: Mapped[str] = mapped_column(String(8))  # read | typing | in | out
    at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class Message(Base):
    """Кэш сообщений из чатов."""

    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("user_id", "peer_id", "message_id", name="uq_msg_user_peer_id"),
        Index("ix_messages_user_peer_date", "user_id", "peer_id", "date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    peer_id: Mapped[int] = mapped_column(BigInteger, index=True)
    message_id: Mapped[int] = mapped_column(BigInteger)
    sender_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sender_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    is_outgoing: Mapped[bool] = mapped_column(Boolean, default=False)
    date: Mapped[datetime] = mapped_column(DateTime, index=True)
    kind: Mapped[str] = mapped_column(String(16), default="text")  # text | voice | audio | document | photo | other
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)  # для документов
    indexed_in_vector: Mapped[bool] = mapped_column(Boolean, default=False)


class Commitment(Base):
    """Извлечённые обещания."""

    __tablename__ = "commitments"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    peer_id: Mapped[int] = mapped_column(BigInteger, index=True)
    peer_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    direction: Mapped[str] = mapped_column(String(8))  # mine | theirs
    text: Mapped[str] = mapped_column(Text)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | done | cancelled | reminded
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AutoReplyLog(Base):
    """Лог авто-ответов для прозрачности."""

    __tablename__ = "auto_reply_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    peer_id: Mapped[int] = mapped_column(BigInteger, index=True)
    peer_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    incoming_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    reply_text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class IndexJob(Base):
    """Состояние индексации чата (последний обработанный message_id)."""

    __tablename__ = "index_jobs"
    __table_args__ = (UniqueConstraint("user_id", "peer_id", name="uq_index_user_peer"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    peer_id: Mapped[int] = mapped_column(BigInteger, index=True)
    last_indexed_message_id: Mapped[int] = mapped_column(BigInteger, default=0)
    last_indexed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class TranscriptionCache(Base):
    """Кэш транскрипций по telegram media file_id."""

    __tablename__ = "transcription_cache"

    file_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PendingAction(Base):
    """Промежуточные действия, ожидающие подтверждения (отправка сообщения и т.п.)."""

    __tablename__ = "pending_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # send_message | catchup_reply | ...
    payload: Mapped[str] = mapped_column(Text)  # JSON
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class NewsTopic(Base):
    """Темы-фавориты для авто-новостей. Каждая утром собирается отдельным дайджестом."""

    __tablename__ = "news_topics"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    topic: Mapped[str] = mapped_column(String(256))
    hours: Mapped[int] = mapped_column(Integer, default=24)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
