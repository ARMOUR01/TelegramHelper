"""«Кто и как часто заходит к тебе в чат» — приближение через 3 сигнала Telegram:
1. read    — контакт прочитал твоё исходящее сообщение
2. typing  — контакт начал печатать в чате с тобой
3. in/out  — само сообщение (для контекста, не считается как «визит»)

Точного события «открыл чат» Telegram не отдаёт, поэтому «визит» определяется
как любая активность контакта в чате с тобой в течение часового окна (read и
typing внутри одного часа считаются за один визит — типичная сессия)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import ChatEngagementEvent, Contact


@dataclass
class VisitorStats:
    peer_id: int
    display_name: str
    is_poi: bool
    last_event_at: datetime
    visit_count: int  # часовые «сессии»
    read_count: int
    typing_count: int
    in_count: int
    out_count: int


async def record_event(
    session: AsyncSession,
    *,
    user_id: int,
    peer_id: int,
    kind: str,
    at: datetime | None = None,
) -> None:
    """Логирует engagement-сигнал. Дёшево, без сравнения дубликатов —
    дедупликация делается на чтении через GROUP BY часу."""
    session.add(
        ChatEngagementEvent(
            user_id=user_id, peer_id=peer_id, kind=kind,
            at=at or datetime.utcnow(),
        )
    )


async def list_visitors(
    session: AsyncSession,
    *,
    user_id: int,
    hours: int = 24 * 7,
    top_n: int = 20,
) -> list[VisitorStats]:
    """Топ-N контактов «кто чаще всего заходит в твой чат» за окно времени.

    «Визит» считается как hour-bucket: все события одного контакта в одном
    часовом интервале схлопываются в один визит."""
    since = datetime.utcnow() - timedelta(hours=hours)

    # Per-event-kind counters
    counter_q = select(
        ChatEngagementEvent.peer_id,
        ChatEngagementEvent.kind,
        func.count().label("cnt"),
    ).where(
        ChatEngagementEvent.user_id == user_id,
        ChatEngagementEvent.at >= since,
    ).group_by(ChatEngagementEvent.peer_id, ChatEngagementEvent.kind)

    counters: dict[int, dict[str, int]] = {}
    for peer_id, kind, cnt in (await session.execute(counter_q)).all():
        counters.setdefault(peer_id, {})[kind] = int(cnt)

    if not counters:
        return []

    # Last engagement timestamp per peer
    last_q = select(
        ChatEngagementEvent.peer_id,
        func.max(ChatEngagementEvent.at).label("last_at"),
    ).where(
        ChatEngagementEvent.user_id == user_id,
        ChatEngagementEvent.at >= since,
        ChatEngagementEvent.peer_id.in_(list(counters.keys())),
    ).group_by(ChatEngagementEvent.peer_id)
    last_at: dict[int, datetime] = {
        peer_id: when for peer_id, when in (await session.execute(last_q)).all()
    }

    # Hour-bucket distinct count = unique hours with activity
    visits_q = select(
        ChatEngagementEvent.peer_id,
        func.count(func.distinct(func.strftime("%Y-%m-%d %H", ChatEngagementEvent.at))).label("visits"),
    ).where(
        ChatEngagementEvent.user_id == user_id,
        ChatEngagementEvent.at >= since,
        ChatEngagementEvent.peer_id.in_(list(counters.keys())),
        ChatEngagementEvent.kind.in_(["read", "typing", "in"]),
    ).group_by(ChatEngagementEvent.peer_id)
    visits: dict[int, int] = {
        peer_id: int(cnt) for peer_id, cnt in (await session.execute(visits_q)).all()
    }

    # Contact display names
    contacts_q = select(Contact).where(
        Contact.user_id == user_id,
        Contact.peer_id.in_(list(counters.keys())),
    )
    name_by_peer: dict[int, tuple[str, bool]] = {
        c.peer_id: (c.display_name, bool(getattr(c, "is_poi", False)))
        for c in (await session.execute(contacts_q)).scalars().all()
    }

    rows: list[VisitorStats] = []
    for peer_id, kinds in counters.items():
        display_name, is_poi = name_by_peer.get(peer_id, (str(peer_id), False))
        rows.append(VisitorStats(
            peer_id=peer_id,
            display_name=display_name,
            is_poi=is_poi,
            last_event_at=last_at.get(peer_id, since),
            visit_count=visits.get(peer_id, 0),
            read_count=kinds.get("read", 0),
            typing_count=kinds.get("typing", 0),
            in_count=kinds.get("in", 0),
            out_count=kinds.get("out", 0),
        ))

    rows.sort(key=lambda r: (r.visit_count, r.last_event_at), reverse=True)
    return rows[:top_n]


def humanize_delta(now: datetime, then: datetime) -> str:
    delta = now - then
    s = int(delta.total_seconds())
    if s < 60:
        return "только что"
    if s < 3600:
        return f"{s // 60} мин назад"
    if s < 86400:
        return f"{s // 3600} ч назад"
    return f"{s // 86400} дн назад"


def format_visitors(stats: list[VisitorStats], hours: int) -> str:
    if not stats:
        return f"За последние {hours}ч никто к тебе в чат не заходил."

    if hours < 24:
        win_label = f"{hours}ч"
    elif hours == 24:
        win_label = "сутки"
    elif hours % 24 == 0:
        win_label = f"{hours // 24} дн"
    else:
        win_label = f"{hours}ч"

    lines = [f"📊 <b>Кто заходит к тебе в чат</b> · окно: {win_label}", ""]
    now = datetime.utcnow()
    for i, r in enumerate(stats, 1):
        star = " ⭐" if r.is_poi else ""
        lines.append(
            f"<b>{i}. {r.display_name}</b>{star} — <b>{r.visit_count}</b> "
            f"визитов · {humanize_delta(now, r.last_event_at)}"
        )
        breakdown = []
        if r.read_count:
            breakdown.append(f"прочёл: {r.read_count}")
        if r.typing_count:
            breakdown.append(f"печатал: {r.typing_count}")
        if r.in_count:
            breakdown.append(f"написал: {r.in_count}")
        if breakdown:
            lines.append("   └ " + " · ".join(breakdown))

    lines.append("")
    lines.append(
        "<i>«Визит» = час, в который контакт был активен (читал, печатал "
        "или писал). Точного события «открыл чат» Telegram не отдаёт.</i>"
    )
    return "\n".join(lines)
