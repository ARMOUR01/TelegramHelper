"""Killer feature: общая выжимка по топ-N активным личным чатам за окно.

Не требует Telethon-клиент — читает только локальный кэш сообщений.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select

from src.core.chat_service import message_to_text
from src.core.text_sanitizer import sanitize_html
from src.db.models import Contact, Message, User
from src.db.repo import get_or_create_user
from src.db.session import get_session
from src.llm.base import ChatMessage, LLMProvider
from src.llm.router import build_provider


logger = logging.getLogger(__name__)


PER_CHAT_SYSTEM = (
    "Сделай очень короткую выжимку переписки — 1–2 предложения, до ~220 символов. "
    "Стиль: телеграфный, по делу. Что обсуждалось, кто чего ждёт, есть ли договорённости/обещания. "
    "Без приветствий, без воды, без HTML и без markdown — только plain-text в одну-две строки."
)


DEFAULT_TOP_N = 10
DEFAULT_HOURS = 24
MAX_TOP_N = 30
MAX_HOURS = 168  # неделя
MIN_MESSAGES_PER_CHAT = 2
PER_CHAT_MSG_LIMIT = 40
MAX_CONCURRENCY = 3


async def _top_active_personal_peers(
    owner: User,
    *,
    top_n: int,
    hours: int,
    include_archived: bool,
) -> list[tuple[int, str, int]]:
    """(peer_id, display_name, msg_count) — топ активных личных чатов за окно."""
    since = datetime.utcnow() - timedelta(hours=hours)
    async with get_session() as session:
        query = (
            select(
                Contact.peer_id,
                Contact.display_name,
                func.count(Message.id).label("cnt"),
            )
            .join(
                Message,
                (Message.peer_id == Contact.peer_id) & (Message.user_id == Contact.user_id),
            )
            .where(
                Contact.user_id == owner.id,
                Contact.peer_kind == "user",
                Contact.is_bot.is_(False),
                Message.date >= since,
            )
        )
        if not include_archived:
            query = query.where(Contact.is_archived.is_(False))
        query = (
            query.group_by(Contact.peer_id, Contact.display_name)
            .order_by(func.count(Message.id).desc())
            .limit(top_n)
        )
        rows = (await session.execute(query)).all()
    return [
        (int(p), n or str(p), int(c))
        for p, n, c in rows
        if int(c) >= MIN_MESSAGES_PER_CHAT
    ]


async def _fetch_recent_messages(
    owner_id: int,
    peer_id: int,
    *,
    since: datetime,
    limit: int,
) -> list[Message]:
    async with get_session() as session:
        rows = (
            await session.execute(
                select(Message)
                .where(
                    Message.user_id == owner_id,
                    Message.peer_id == peer_id,
                    Message.date >= since,
                )
                .order_by(Message.date.desc())
                .limit(limit)
            )
        ).scalars().all()
    return list(reversed(rows))


async def _summarize_one(
    provider: LLMProvider,
    display_name: str,
    messages: list[Message],
    *,
    sem: asyncio.Semaphore,
) -> tuple[str, str]:
    transcript = "\n".join(message_to_text(m) for m in messages)
    user_prompt = (
        f"Собеседник: {display_name}\n\n"
        f"Переписка (последние {len(messages)} сообщений):\n{transcript}"
    )
    async with sem:
        try:
            raw = await provider.chat(
                [
                    ChatMessage(role="system", content=PER_CHAT_SYSTEM),
                    ChatMessage(role="user", content=user_prompt),
                ],
                heavy=False,
            )
        except Exception:
            logger.exception("per-chat summary failed for %s", display_name)
            return display_name, "<i>не удалось получить выжимку</i>"
    cleaned = (raw or "").strip().replace("\n", " ")
    return display_name, sanitize_html(cleaned) or "<i>пусто</i>"


def _chunk_messages(text: str, max_len: int = 3800) -> list[str]:
    """Режет длинное сообщение на куски, чтобы влезть в 4096 лимит Telegram."""
    if len(text) <= max_len:
        return [text]
    parts: list[str] = []
    current = ""
    for line in text.split("\n"):
        candidate = (current + "\n" + line) if current else line
        if len(candidate) > max_len and current:
            parts.append(current)
            current = line
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


async def build_all_chats_summary(
    owner_telegram_id: int,
    *,
    top_n: int = DEFAULT_TOP_N,
    hours: int = DEFAULT_HOURS,
) -> list[str]:
    """Возвращает список текстов (1+) — потому что Telegram режет на 4096 символов."""
    top_n = max(1, min(MAX_TOP_N, int(top_n or DEFAULT_TOP_N)))
    hours = max(1, min(MAX_HOURS, int(hours or DEFAULT_HOURS)))

    async with get_session() as session:
        owner = await get_or_create_user(session, owner_telegram_id)
        provider = await build_provider(session, owner)
        include_archived = not owner.settings.ignore_archived

    if provider is None:
        return ["Нужен LLM-ключ — открой /settings → 🔑 API-ключи."]

    peers = await _top_active_personal_peers(
        owner, top_n=top_n, hours=hours, include_archived=include_archived,
    )
    if not peers:
        return [f"📋 За последние {hours}ч в личных чатах не было активности."]

    since = datetime.utcnow() - timedelta(hours=hours)
    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    async def _job(peer_id: int, name: str) -> tuple[str, str]:
        msgs = await _fetch_recent_messages(
            owner.id, peer_id, since=since, limit=PER_CHAT_MSG_LIMIT,
        )
        if not msgs:
            return name, "<i>сообщений в окне нет</i>"
        return await _summarize_one(provider, name, msgs, sem=sem)

    results = await asyncio.gather(*[_job(p, n) for p, n, _ in peers])

    header = (
        f"📋 <b>Выжимка по {len(results)} активным личным чатам</b> "
        f"· окно {hours}ч"
    )
    body_lines = [f"• <b>{name}</b> — {summary}" for name, summary in results]
    text = header + "\n\n" + "\n".join(body_lines)
    return _chunk_messages(text)
