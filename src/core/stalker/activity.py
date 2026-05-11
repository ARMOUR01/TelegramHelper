"""Heatmap активности POI на основе OnlineObservation."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import OnlineObservation


async def hourly_activity(
    session: AsyncSession, *, user_id: int, peer_id: int, days: int = 14,
) -> dict[int, int]:
    """Возвращает {hour: count} — сколько раз POI был замечен онлайн в каждый час суток."""
    since = datetime.utcnow() - timedelta(days=days)
    rows = (await session.execute(
        select(OnlineObservation.observed_at).where(
            OnlineObservation.user_id == user_id,
            OnlineObservation.peer_id == peer_id,
            OnlineObservation.observed_at >= since,
        )
    )).all()
    buckets: dict[int, int] = {h: 0 for h in range(24)}
    for (when,) in rows:
        buckets[when.hour] += 1
    return buckets


def format_heatmap(name: str, buckets: dict[int, int], *, days: int, tz_offset_hours: int = 0) -> str:
    if not any(buckets.values()):
        return (
            f"Активность <b>{name}</b> пока не накоплена. POI-сторож собирает её "
            f"в фоне раз в 5 минут — данные появятся через пару часов."
        )
    max_v = max(buckets.values())
    rows = []
    for h in range(24):
        local_h = (h + tz_offset_hours) % 24
        cnt = buckets[h]
        bar_len = round(20 * cnt / max_v) if max_v else 0
        bar = "█" * bar_len + "·" * (20 - bar_len)
        rows.append((local_h, f"{local_h:02d} {bar} {cnt}"))
    rows.sort(key=lambda x: x[0])
    body = "\n".join(r[1] for r in rows)
    return f"📈 <b>Активность {name}</b> по часам · окно: {days} дн\n<pre>{body}</pre>"
