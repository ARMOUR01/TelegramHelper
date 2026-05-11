"""Хендлеры на engagement-сигналы:
- events.MessageRead   — кто-то прочитал твоё сообщение в личке
- events.ChatAction    — typing / status updates
- events.MessageDeleted — лог удалений «для всех»

Все ивенты пишутся в БД через src.core.stalker.engagement.record_event."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from telethon import TelegramClient, events

from src.core.stalker.engagement import record_event
from src.db.models import DeletedMessage, Message
from src.db.repo import get_contact, get_or_create_user
from src.db.session import get_session


logger = logging.getLogger(__name__)


# typing-события приходят пачкой («still typing»), храним последний таймштамп
# на (owner, peer) чтобы не записывать одно и то же 10 раз в минуту.
_LAST_TYPING_AT: dict[tuple[int, int], datetime] = {}
_TYPING_DEDUP_WINDOW = timedelta(minutes=2)


def attach_engagement_handlers(client: TelegramClient, owner_telegram_id: int) -> None:
    async def on_message_read(event: events.MessageRead.Event) -> None:
        # Telethon шлёт MessageRead когда контакт прочитал твоё сообщение,
        # либо когда сам owner прочитал входящее. Интересно первое.
        try:
            if event.is_group or event.is_channel:
                return  # учитываем только личные диалоги
            if event.inbox:
                return  # это owner прочитал — не интересно
            peer_id = event.chat_id
            if peer_id is None:
                return
            async with get_session() as session:
                owner = await get_or_create_user(session, owner_telegram_id)
                await record_event(
                    session, user_id=owner.id, peer_id=peer_id, kind="read",
                )
        except Exception:
            logger.exception("on_message_read failed")

    async def on_user_update(event: events.UserUpdate.Event) -> None:
        try:
            # typing / recording / uploading и т.п.
            if not getattr(event, "typing", False):
                return
            peer_id = event.chat_id
            if peer_id is None or peer_id == owner_telegram_id:
                return
            key = (owner_telegram_id, peer_id)
            now = datetime.utcnow()
            last = _LAST_TYPING_AT.get(key)
            if last and (now - last) < _TYPING_DEDUP_WINDOW:
                return
            _LAST_TYPING_AT[key] = now
            async with get_session() as session:
                owner = await get_or_create_user(session, owner_telegram_id)
                await record_event(
                    session, user_id=owner.id, peer_id=peer_id, kind="typing", at=now,
                )
        except Exception:
            logger.exception("on_user_update failed")

    async def on_message_deleted(event: events.MessageDeleted.Event) -> None:
        # Telegram НЕ сообщает в каком чате удалили в личках/малых группах —
        # вытаскиваем peer_id поиском по нашему зеркалу messages.
        try:
            deleted_ids = list(event.deleted_ids or [])
            if not deleted_ids:
                return
            async with get_session() as session:
                owner = await get_or_create_user(session, owner_telegram_id)
                rows = (await session.execute(
                    select(Message).where(
                        Message.user_id == owner.id,
                        Message.message_id.in_(deleted_ids),
                    )
                )).scalars().all()
                if not rows:
                    return  # нечего восстановить — пропускаем тихо
                peer_ids = {r.peer_id for r in rows}
                contacts = {}
                for pid in peer_ids:
                    c = await get_contact(session, owner, pid)
                    contacts[pid] = c.display_name if c else None
                for r in rows:
                    session.add(DeletedMessage(
                        user_id=owner.id,
                        peer_id=r.peer_id,
                        peer_name=contacts.get(r.peer_id),
                        message_id=r.message_id,
                        sender_name=r.sender_name,
                        is_outgoing=bool(r.is_outgoing),
                        original_text=(r.text or r.transcript),
                        sent_at=r.date,
                    ))
        except Exception:
            logger.exception("on_message_deleted failed")

    # Зеркальные NewMessage уже пишутся mirror.py — engagement событий 'in'/'out'
    # туда докинем отдельным хендлером, чтобы не трогать существующий mirror.
    async def on_new_message_engagement(event: events.NewMessage.Event) -> None:
        try:
            msg = event.message
            peer_id = None
            chat = msg.chat
            if chat is not None:
                peer_id = chat.id
            elif msg.peer_id is not None and hasattr(msg.peer_id, "user_id"):
                peer_id = msg.peer_id.user_id
            if peer_id is None:
                return
            if event.is_group or event.is_channel:
                return
            kind = "out" if msg.out else "in"
            async with get_session() as session:
                owner = await get_or_create_user(session, owner_telegram_id)
                await record_event(
                    session, user_id=owner.id, peer_id=peer_id, kind=kind,
                    at=msg.date.replace(tzinfo=None) if msg.date else datetime.utcnow(),
                )
        except Exception:
            logger.exception("on_new_message_engagement failed")

    client.add_event_handler(on_message_read, events.MessageRead())
    client.add_event_handler(on_user_update, events.UserUpdate())
    client.add_event_handler(on_message_deleted, events.MessageDeleted())
    client.add_event_handler(on_new_message_engagement, events.NewMessage())
    logger.info("Engagement event handlers attached for user %s", owner_telegram_id)
