"""Per-contact AI takeover.

Когда `contact.ai_takeover_enabled = True` и я не отвечал контакту дольше
`ai_takeover_idle_min` минут с момента последнего входящего — бот сам
отвечает за меня. В первом сообщении представляется как AI-ассистент.

Логика идл-окна:
1. Контакт пишет → проверяем `ai_takeover_enabled`.
2. Если ВКЛ → находим последнее моё исходящее в БД для этого пира.
3. Если оно старше N минут (или никогда не было) → AI отвечает.
4. После моего следующего исходящего сообщения окно сбрасывается
   автоматически (на новое входящее опять ждём N минут).
"""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta

from sqlalchemy import select
from telethon import TelegramClient, events
from telethon.tl.custom import Message as TgMessage
from telethon.tl.types import User as TgUser

from src.core.chat_service import load_chat, message_to_text
from src.core.notifier import notifier
from src.core.style_profile import style_profile_as_prompt_hint
from src.db.models import Message, User
from src.db.repo import (
    add_auto_reply_log,
    get_contact,
    get_or_create_user,
    upsert_contact,
)
from src.db.session import get_session
from src.llm.base import ChatMessage
from src.llm.router import build_provider


logger = logging.getLogger(__name__)


CONTEXT_LIMIT = 30
# жёсткий внутренний кулдаун: даже если контакт спамит — отвечаем не чаще
MIN_REPLY_GAP_SEC = 20


AI_TAKEOVER_SYSTEM_INTRO = (
    "Ты — AI-ассистент пользователя по имени {owner_name}. "
    "Хозяин сейчас не в сети дольше {idle_min} минут, поэтому я отвечаю за него.\n\n"
    "ПРАВИЛА:\n"
    "1) В САМОМ ПЕРВОМ сообщении этого диалога ОБЯЗАТЕЛЬНО представься: "
    "коротко, по-человечески, скажи что ты AI-ассистент {owner_name}, он сейчас не в сети, "
    "ты можешь поболтать или передать сообщение. ОДИН РАЗ.\n"
    "2) В последующих ответах НЕ повторяй представление, веди диалог как ассистент.\n"
    "3) НЕ обещай ничего конкретного за хозяина (встречи, деньги, обязательства). "
    "Если просят — отвечай «передам ему, как будет в сети».\n"
    "4) НЕ выдавай конфиденциальную информацию (адреса, реквизиты, пароли, личные данные).\n"
    "5) Тон — дружелюбный, естественный. Пиши коротко: 1–3 предложения.\n"
    "6) Если человек спрашивает «ты бот?» / «ты ии?» — честно подтверди.\n"
    "7) Не используй фразы типа «как AI» / «как ассистент» в середине разговора — "
    "только если уместно по контексту."
)

AI_TAKEOVER_SYSTEM_CONTINUE = (
    "Ты — AI-ассистент пользователя {owner_name}, ведёшь за него этот чат пока "
    "он не в сети. Представление уже было сделано ранее — НЕ повторяй его.\n\n"
    "ПРАВИЛА: не обещай ничего за хозяина (передавай «скажу ему»), не выдавай "
    "приватные данные, отвечай коротко 1–3 предложениями, не упоминай что ты AI "
    "если не спрашивают."
)


async def _owner_silent_for_long(
    owner_db_id: int, peer_id: int, idle_min: int
) -> bool:
    """True если я (owner) ничего не писал контакту дольше idle_min минут."""
    threshold = datetime.utcnow() - timedelta(minutes=idle_min)
    async with get_session() as session:
        # последнее моё исходящее этому контакту
        result = await session.execute(
            select(Message.date)
            .where(
                Message.user_id == owner_db_id,
                Message.peer_id == peer_id,
                Message.is_outgoing.is_(True),
            )
            .order_by(Message.date.desc())
            .limit(1)
        )
        last_outgoing = result.scalar_one_or_none()
    # если никогда не писал — окно открыто сразу
    if last_outgoing is None:
        return True
    return last_outgoing < threshold


async def _ai_replied_recently(owner_db_id: int, peer_id: int) -> bool:
    threshold = datetime.utcnow() - timedelta(seconds=MIN_REPLY_GAP_SEC)
    async with get_session() as session:
        from src.db.models import AutoReplyLog

        result = await session.execute(
            select(AutoReplyLog.id)
            .where(
                AutoReplyLog.user_id == owner_db_id,
                AutoReplyLog.peer_id == peer_id,
                AutoReplyLog.kind == "ai_takeover",
                AutoReplyLog.created_at >= threshold,
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None


async def _build_reply(
    owner_telegram_id: int,
    peer_id: int,
    sender_name: str,
    incoming_text: str,
    is_first_message: bool,
) -> str | None:
    async with get_session() as session:
        owner = await get_or_create_user(session, owner_telegram_id)
        provider = await build_provider(session, owner)
        contact = await get_contact(session, owner, peer_id)
        heavy = owner.settings.use_heavy_model
        idle_min = contact.ai_takeover_idle_min if contact else 30
        persona = (contact.ai_takeover_persona if contact else None) or ""
        # имя владельца — first_name из Telegram, либо «хозяин»
        owner_name = "хозяин чата"

    if provider is None:
        logger.warning("ai-takeover: no LLM provider")
        return None

    from src.userbot.manager import _MANAGER_SINGLETON  # локальный импорт

    client = _MANAGER_SINGLETON.get_client(owner_telegram_id) if _MANAGER_SINGLETON else None
    history_text = ""
    if client is not None:
        try:
            me = await client.get_me()
            owner_name = (
                (getattr(me, "first_name", None) or "")
                + (" " + getattr(me, "last_name", "") if getattr(me, "last_name", None) else "")
            ).strip() or owner_name
            messages = await load_chat(client, owner_telegram_id, peer_id, limit=CONTEXT_LIMIT)
            history_text = "\n".join(message_to_text(m) for m in messages[-CONTEXT_LIMIT:])
        except Exception:
            logger.exception("ai-takeover: load context failed")

    style_hint = ""
    try:
        async with get_session() as session:
            owner = await get_or_create_user(session, owner_telegram_id)
            contact = await get_contact(session, owner, peer_id)
            if contact is not None:
                style_hint = style_profile_as_prompt_hint(contact.style_profile)
    except Exception:
        pass

    if is_first_message:
        system = AI_TAKEOVER_SYSTEM_INTRO.format(owner_name=owner_name, idle_min=idle_min)
    else:
        system = AI_TAKEOVER_SYSTEM_CONTINUE.format(owner_name=owner_name)

    if persona.strip():
        system += "\n\nДополнительные инструкции от хозяина: " + persona.strip()
    if style_hint:
        system += "\n\nСтиль письма хозяина (подражай НЕМНОГО, но НЕ полностью — ты ведь AI):\n" + style_hint

    user_prompt = (
        f"Собеседник: {sender_name}.\n"
        f"Последние сообщения чата:\n{history_text}\n\n"
        f"Новое входящее: {incoming_text}\n\n"
        "Сформируй один ответ от лица AI-ассистента."
    )
    try:
        return await provider.chat(
            [
                ChatMessage(role="system", content=system),
                ChatMessage(role="user", content=user_prompt),
            ],
            heavy=heavy,
        )
    except Exception:
        logger.exception("ai-takeover: LLM call failed")
        return None


async def _make_handler(client: TelegramClient, owner_telegram_id: int):
    async def handler(event: events.NewMessage.Event) -> None:
        try:
            msg: TgMessage = event.message
            if msg.out:
                return
            sender = await event.get_sender()
            if not isinstance(sender, TgUser) or sender.bot:
                return
            if not event.is_private:
                return

            async with get_session() as session:
                owner: User = await get_or_create_user(session, owner_telegram_id)
                contact = await get_contact(session, owner, sender.id)
                # запомним контакт если его нет
                if contact is None:
                    parts = [getattr(sender, "first_name", None), getattr(sender, "last_name", None)]
                    display = " ".join(p for p in parts if p).strip() or (sender.username or str(sender.id))
                    await upsert_contact(
                        session,
                        owner,
                        peer_id=sender.id,
                        peer_kind="user",
                        is_bot=False,
                        display_name=display,
                        username=getattr(sender, "username", None),
                        phone=getattr(sender, "phone", None),
                    )
                    contact = await get_contact(session, owner, sender.id)

                if not contact or not contact.ai_takeover_enabled:
                    return

                idle_min = contact.ai_takeover_idle_min or 30
                owner_db_id = owner.id
                display = contact.display_name
                intro_already_sent = contact.ai_takeover_intro_sent_at is not None

            # игдл-окно: я молчу дольше idle_min мин?
            if not await _owner_silent_for_long(owner_db_id, sender.id, idle_min):
                return
            # внутренний антифлуд
            if await _ai_replied_recently(owner_db_id, sender.id):
                return

            incoming_text = msg.text or msg.message or ""
            if not incoming_text.strip():
                return

            # имитация «человеческой задержки» — 1.5..4 сек
            try:
                async with client.action(sender.id, "typing"):
                    await asyncio.sleep(random.uniform(1.5, 4.0))
            except Exception:
                pass

            reply = await _build_reply(
                owner_telegram_id,
                sender.id,
                display,
                incoming_text,
                is_first_message=not intro_already_sent,
            )
            if not reply:
                return

            await event.respond(reply)

            now = datetime.utcnow()
            async with get_session() as session:
                owner = await get_or_create_user(session, owner_telegram_id)
                contact = await get_contact(session, owner, sender.id)
                if contact is not None:
                    contact.ai_takeover_last_reply_at = now
                    if not intro_already_sent:
                        contact.ai_takeover_intro_sent_at = now
                await add_auto_reply_log(
                    session,
                    user_id=owner.id,
                    peer_id=sender.id,
                    peer_name=display,
                    incoming_text=incoming_text[:500],
                    reply_text=reply,
                    kind="ai_takeover",
                )

            await notifier.notify(
                f"🤖 <b>AI ответил за тебя</b> · {display}\n\n"
                f"<i>Им:</i> {incoming_text[:200]}\n"
                f"<i>AI:</i> {reply}"
            )
        except Exception:
            logger.exception("ai-takeover handler failed")

    return handler


def attach_ai_takeover(client: TelegramClient, owner_telegram_id: int) -> None:
    async def _wrapper(event):
        h = await _make_handler(client, owner_telegram_id)
        await h(event)

    client.add_event_handler(_wrapper, events.NewMessage(incoming=True))
    logger.info("AI takeover handler attached for user %s", owner_telegram_id)
