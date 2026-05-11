"""Relationship health — статистика взаимодействия с конкретным контактом."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import ChatEngagementEvent, Message


@dataclass
class RelStats:
    days: int
    total_msgs: int
    my_msgs: int
    their_msgs: int
    msgs_per_day: float
    avg_my_reply_seconds: float | None
    avg_their_reply_seconds: float | None
    last_my_msg_at: datetime | None
    last_their_msg_at: datetime | None
    days_silence: int | None
    visits: int  # из engagement
    reads: int


async def compute_rel_stats(
    session: AsyncSession, *, user_id: int, peer_id: int, days: int = 30,
) -> RelStats:
    since = datetime.utcnow() - timedelta(days=days)

    rows = (await session.execute(
        select(Message.is_outgoing, Message.date).where(
            Message.user_id == user_id,
            Message.peer_id == peer_id,
            Message.date >= since,
        ).order_by(Message.date)
    )).all()

    my_msgs = sum(1 for is_out, _ in rows if is_out)
    their_msgs = sum(1 for is_out, _ in rows if not is_out)
    total = my_msgs + their_msgs

    # response times — для каждой смены направления (their → mine, mine → their)
    my_reply_deltas: list[float] = []
    their_reply_deltas: list[float] = []
    last_dir: bool | None = None
    last_at: datetime | None = None
    for is_out, when in rows:
        if last_dir is None:
            last_dir, last_at = bool(is_out), when
            continue
        if bool(is_out) != last_dir and last_at is not None:
            delta = (when - last_at).total_seconds()
            if delta < 7 * 24 * 3600:  # отсекаем недельные паузы
                if is_out:
                    my_reply_deltas.append(delta)
                else:
                    their_reply_deltas.append(delta)
            last_dir, last_at = bool(is_out), when
        else:
            last_at = when  # обновляем «последнее в серии»

    last_my = max((d for o, d in rows if o), default=None)
    last_their = max((d for o, d in rows if not o), default=None)

    days_silence: int | None = None
    if last_their:
        days_silence = (datetime.utcnow() - last_their).days

    # «визиты» из engagement-таблицы
    visits_q = select(
        func.count(func.distinct(func.strftime("%Y-%m-%d %H", ChatEngagementEvent.at)))
    ).where(
        ChatEngagementEvent.user_id == user_id,
        ChatEngagementEvent.peer_id == peer_id,
        ChatEngagementEvent.at >= since,
        ChatEngagementEvent.kind.in_(["read", "typing", "in"]),
    )
    visits = int((await session.execute(visits_q)).scalar() or 0)

    reads_q = select(func.count()).where(
        ChatEngagementEvent.user_id == user_id,
        ChatEngagementEvent.peer_id == peer_id,
        ChatEngagementEvent.at >= since,
        ChatEngagementEvent.kind == "read",
    )
    reads = int((await session.execute(reads_q)).scalar() or 0)

    return RelStats(
        days=days,
        total_msgs=total,
        my_msgs=my_msgs,
        their_msgs=their_msgs,
        msgs_per_day=total / max(1, days),
        avg_my_reply_seconds=(
            sum(my_reply_deltas) / len(my_reply_deltas) if my_reply_deltas else None
        ),
        avg_their_reply_seconds=(
            sum(their_reply_deltas) / len(their_reply_deltas) if their_reply_deltas else None
        ),
        last_my_msg_at=last_my,
        last_their_msg_at=last_their,
        days_silence=days_silence,
        visits=visits,
        reads=reads,
    )


def _fmt_seconds(secs: float | None) -> str:
    if secs is None:
        return "—"
    if secs < 60:
        return f"{int(secs)} сек"
    if secs < 3600:
        return f"{int(secs // 60)} мин"
    if secs < 86400:
        return f"{secs / 3600:.1f} ч"
    return f"{secs / 86400:.1f} дн"


def format_rel_stats(name: str, st: RelStats) -> str:
    lines = [f"📊 <b>Отношения с {name}</b> · окно: {st.days} дн", ""]
    if st.total_msgs == 0:
        lines.append("Сообщений за период не было.")
        return "\n".join(lines)

    pct_me = round(100 * st.my_msgs / st.total_msgs)
    pct_them = 100 - pct_me
    lines.append(f"💬 Сообщений: <b>{st.total_msgs}</b> ({st.msgs_per_day:.1f}/день)")
    lines.append(f"   ├ ты:     <b>{st.my_msgs}</b> ({pct_me}%)")
    lines.append(f"   └ {name}: <b>{st.their_msgs}</b> ({pct_them}%)")
    lines.append("")
    lines.append(f"⏱ Время ответа:")
    lines.append(f"   ├ твоё:   <b>{_fmt_seconds(st.avg_my_reply_seconds)}</b>")
    lines.append(f"   └ его/её: <b>{_fmt_seconds(st.avg_their_reply_seconds)}</b>")
    if st.visits or st.reads:
        lines.append("")
        lines.append(f"👀 Активность в чате: <b>{st.visits}</b> визитов · "
                     f"<b>{st.reads}</b> прочтений твоего")
    if st.days_silence is not None:
        lines.append("")
        if st.days_silence == 0:
            lines.append("✅ Писал(а) тебе сегодня")
        else:
            lines.append(f"🕯 Тишина от него/неё: <b>{st.days_silence}</b> дн")
    return "\n".join(lines)
