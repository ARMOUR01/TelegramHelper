"""Re-engagement радар: каждые N минут проверяем POI'ев и пингуем владельца,
если давно нет переписки или контакт молчит дольше обычного."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select

from src.config import settings as app_settings
from src.core.notifier import notifier
from src.db.models import Contact, Message
from src.db.repo import get_or_create_user
from src.db.session import get_session


logger = logging.getLogger(__name__)


REENGAGEMENT_TICK_SECONDS = 60 * 60 * 6  # 4 раза в сутки
SILENCE_THRESHOLD_DAYS = 14  # «давно не писал X»
NOTIFY_COOLDOWN_DAYS = 5     # не пинать тем же POI чаще раза в N дней


async def _check_once(owner_telegram_id: int) -> None:
    async with get_session() as session:
        owner = await get_or_create_user(session, owner_telegram_id)
        rows = (await session.execute(
            select(Contact).where(
                Contact.user_id == owner.id,
                Contact.is_poi == True,  # noqa: E712
                Contact.peer_kind == "user",
            )
        )).scalars().all()
        if not rows:
            return

        now = datetime.utcnow()
        cooldown = timedelta(days=NOTIFY_COOLDOWN_DAYS)
        threshold = timedelta(days=SILENCE_THRESHOLD_DAYS)

        peer_ids = [c.peer_id for c in rows]

        # последнее исходящее по каждому POI
        last_out_q = select(
            Message.peer_id, func.max(Message.date).label("last_at"),
        ).where(
            Message.user_id == owner.id,
            Message.peer_id.in_(peer_ids),
            Message.is_outgoing == True,  # noqa: E712
        ).group_by(Message.peer_id)
        last_out: dict[int, datetime] = {
            pid: when for pid, when in (await session.execute(last_out_q)).all()
        }

        last_in_q = select(
            Message.peer_id, func.max(Message.date).label("last_at"),
        ).where(
            Message.user_id == owner.id,
            Message.peer_id.in_(peer_ids),
            Message.is_outgoing == False,  # noqa: E712
        ).group_by(Message.peer_id)
        last_in: dict[int, datetime] = {
            pid: when for pid, when in (await session.execute(last_in_q)).all()
        }

        to_notify: list[tuple[Contact, str, int]] = []
        for c in rows:
            if c.last_reengagement_at and (now - c.last_reengagement_at) < cooldown:
                continue
            last_o = last_out.get(c.peer_id)
            last_i = last_in.get(c.peer_id)
            last_any = max(filter(None, [last_o, last_i]), default=None)
            if last_any is None:
                continue  # нет истории — пропускаем
            silence = now - last_any
            if silence >= threshold:
                to_notify.append((c, "silence", silence.days))

        for c, _kind, days in to_notify:
            name = c.display_name
            await notifier.notify(
                f"🕯 <b>Re-engagement радар</b>\n"
                f"С <b>{name}</b> — тишина уже {days} дн. Возможно стоит пинговать?"
            )
            c.last_reengagement_at = now


async def reengagement_loop() -> None:
    while True:
        try:
            await _check_once(app_settings.owner_telegram_id)
        except Exception:
            logger.exception("re-engagement tick failed")
        await asyncio.sleep(REENGAGEMENT_TICK_SECONDS)
