"""POI (People of Interest) — пометка важных контактов для глубокой аналитики."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import Contact


async def set_poi(
    session: AsyncSession, *, user_id: int, peer_id: int, is_poi: bool,
) -> bool:
    contact = (await session.execute(
        select(Contact).where(Contact.user_id == user_id, Contact.peer_id == peer_id)
    )).scalar_one_or_none()
    if contact is None:
        return False
    contact.is_poi = is_poi
    return True


async def list_pois(session: AsyncSession, *, user_id: int) -> list[Contact]:
    rows = (await session.execute(
        select(Contact).where(
            Contact.user_id == user_id,
            Contact.is_poi == True,  # noqa: E712 (SQLAlchemy)
            Contact.peer_kind == "user",
        ).order_by(Contact.display_name)
    )).scalars().all()
    return list(rows)
