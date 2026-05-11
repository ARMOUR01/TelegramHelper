"""Просмотр удалённых сообщений + опциональное pushed-уведомление."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import DeletedMessage


async def list_recent_deletions(
    session: AsyncSession, *, user_id: int, hours: int = 72, limit: int = 30,
) -> list[DeletedMessage]:
    since = datetime.utcnow() - timedelta(hours=hours)
    rows = (await session.execute(
        select(DeletedMessage).where(
            DeletedMessage.user_id == user_id,
            DeletedMessage.deleted_at >= since,
        ).order_by(DeletedMessage.deleted_at.desc()).limit(limit)
    )).scalars().all()
    return list(rows)


def format_deletions(rows: list[DeletedMessage], hours: int) -> str:
    if not rows:
        return f"За последние {hours}ч никто ничего не удалял «для обоих» (или мы об этом не узнали)."
    lines = [f"🗑 <b>Удалённые сообщения</b> · окно: {hours}ч", ""]
    for r in rows:
        who = "ты" if r.is_outgoing else (r.sender_name or r.peer_name or "контакт")
        peer = r.peer_name or str(r.peer_id)
        when = r.deleted_at.strftime("%Y-%m-%d %H:%M")
        text = (r.original_text or "").strip().replace("\n", " ")
        if len(text) > 150:
            text = text[:147] + "…"
        text_html = text or "<i>(текст не сохранён)</i>"
        lines.append(f"• <b>{peer}</b> · {when} · удалил(а): <i>{who}</i>")
        lines.append(f"   {text_html}")
    return "\n".join(lines)
