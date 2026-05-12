"""Сводка запланированных встреч / звонков / дедлайнов из истории по всем чатам.

Берёт активные личные чаты за окно времени, прогоняет каждый через Gemini,
извлекает запланированные события в структурированном JSON, собирает в один список."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select

from src.core.chat_service import message_to_text
from src.db.models import Contact, Message, User
from src.db.session import get_session
from src.llm.base import ChatMessage, LLMProvider


logger = logging.getLogger(__name__)


@dataclass
class Meeting:
    contact: str
    title: str
    when: str  # ISO-8601 или человекочитаемая дата если LLM не смог
    when_dt: datetime | None  # parsed, None если не удалось
    status: str  # confirmed | tentative | cancelled
    initiator: str  # me | them | both | unknown
    notes: str = ""


MEETINGS_SYSTEM = """\
Ты выделяешь ЗАПЛАНИРОВАННЫЕ ВСТРЕЧИ / звонки / события / дедлайны из переписки
владельца с одним конкретным контактом. Верни СТРОГИЙ JSON без markdown:

{
  "items": [
    {
      "title":     "короткое название (5-10 слов)",
      "when":      "ISO-8601 если есть точная дата ('2026-05-15T19:00:00'); иначе человеко-формат ('завтра в 19', 'на майские')",
      "when_iso":  "ISO-8601 в UTC если можешь определить, иначе null",
      "status":    "confirmed" | "tentative" | "cancelled",
      "initiator": "me" | "them" | "both" | "unknown",
      "notes":     "1-2 предложения деталей: место, цель, договорённости. Пусто если нет."
    }
  ]
}

Правила:
- Включай ТОЛЬКО события с конкретным временем или хотя бы датой/днём недели в будущем.
- "Договоримся как-нибудь" / "созвонимся скоро" — НЕ включай.
- Прошедшие события не включай (даже если о них договорились).
- Отмены ("давай перенесём", "не получится") — включай как cancelled, чтобы было видно.
- Если в чате 0 событий — верни {"items": []}.
"""


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.astimezone().replace(tzinfo=None)
        return dt
    except Exception:
        return None


def _strip_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    return text


def _parse(raw: str) -> list[dict]:
    try:
        data = json.loads(_strip_fence(raw))
        items = data.get("items") if isinstance(data, dict) else None
        if isinstance(items, list):
            return [i for i in items if isinstance(i, dict)]
    except Exception:
        pass
    return []


async def _extract_one(
    provider: LLMProvider, name: str, messages: list[Message], *, heavy: bool,
) -> list[Meeting]:
    if not messages:
        return []
    transcript = "\n".join(message_to_text(m) for m in messages)
    if len(transcript) > 12000:
        transcript = transcript[-12000:]
    try:
        raw = await provider.chat(
            [
                ChatMessage(role="system", content=MEETINGS_SYSTEM),
                ChatMessage(role="user", content=f"Переписка с {name}:\n\n{transcript}"),
            ],
            heavy=heavy,
        )
    except Exception:
        logger.exception("meetings extract failed for %s", name)
        return []
    out: list[Meeting] = []
    for it in _parse(raw):
        title = (it.get("title") or "").strip()
        if not title:
            continue
        when = (it.get("when") or "").strip() or "?"
        when_dt = _parse_iso(it.get("when_iso")) or _parse_iso(when)
        out.append(Meeting(
            contact=name,
            title=title,
            when=when,
            when_dt=when_dt,
            status=(it.get("status") or "tentative").strip() or "tentative",
            initiator=(it.get("initiator") or "unknown").strip() or "unknown",
            notes=(it.get("notes") or "").strip(),
        ))
    return out


async def list_meetings(
    provider: LLMProvider,
    user_id: int,
    *,
    hours: int = 168,
    future_only: bool = True,
    top_chats: int = 15,
    msgs_per_chat: int = 50,
    heavy: bool = False,
) -> list[Meeting]:
    """Собрать встречи из активных личных чатов."""
    since = datetime.utcnow() - timedelta(hours=hours)

    # Топ-чаты по количеству сообщений за окно
    async with get_session() as session:
        sub = (
            select(
                Message.peer_id,
                func.count(Message.id).label("cnt"),
            )
            .where(
                Message.user_id == user_id,
                Message.date >= since,
            )
            .group_by(Message.peer_id)
            .order_by(func.count(Message.id).desc())
            .limit(top_chats * 3)
        ).subquery()

        result = await session.execute(
            select(Contact, sub.c.cnt)
            .join(sub, sub.c.peer_id == Contact.peer_id)
            .where(Contact.user_id == user_id, Contact.peer_kind == "user")
            .order_by(sub.c.cnt.desc())
            .limit(top_chats)
        )
        chats = list(result.all())

        all_meetings: list[Meeting] = []
        for contact, _cnt in chats:
            msgs_res = await session.execute(
                select(Message)
                .where(
                    Message.user_id == user_id,
                    Message.peer_id == contact.peer_id,
                    Message.date >= since,
                )
                .order_by(Message.date.asc())
                .limit(msgs_per_chat)
            )
            msgs = list(msgs_res.scalars().all())
            if not msgs:
                continue
            try:
                items = await _extract_one(provider, contact.display_name, msgs, heavy=heavy)
            except Exception:
                logger.exception("meetings extract crashed for %s", contact.display_name)
                continue
            all_meetings.extend(items)

    if future_only:
        now = datetime.utcnow()
        all_meetings = [
            m for m in all_meetings
            if m.when_dt is None or m.when_dt >= now
        ]

    # Сортировка: сначала точные даты (по возрастанию), потом без даты
    all_meetings.sort(key=lambda m: (m.when_dt is None, m.when_dt or datetime.max))
    return all_meetings


_STATUS_EMOJI = {
    "confirmed": "✅",
    "tentative": "⏳",
    "cancelled": "❌",
}


def format_meetings(items: list[Meeting], *, hours: int, future_only: bool) -> str:
    if not items:
        scope = "будущие встречи" if future_only else "встречи в окне"
        return (
            f"🗓 <b>Запланированных встреч не нашёл</b> "
            f"(окно: {hours}ч, режим: {scope}).\n\n"
            f"<i>Я смотрю чаты за последние {hours} часов. Если что-то планировалось "
            f"раньше — увеличь окно: «<code>покажи встречи за месяц</code>».</i>"
        )

    lines = [f"🗓 <b>Встречи и события</b> · окно {hours}ч", ""]
    for m in items[:30]:
        emoji = _STATUS_EMOJI.get(m.status, "•")
        line = f"{emoji} <b>{m.title}</b>"
        if m.when:
            line += f" · <i>{m.when}</i>"
        line += f"\n   ↳ с <b>{m.contact}</b>"
        if m.initiator == "me":
            line += " (инициировал я)"
        elif m.initiator == "them":
            line += " (инициировал контакт)"
        if m.notes:
            line += f"\n   <i>{m.notes}</i>"
        lines.append(line)
    if len(items) > 30:
        lines.append(f"\n<i>… и ещё {len(items) - 30}</i>")
    return "\n".join(lines)
