"""Фоновый сторож POI: каждые N минут проверяет
- изменения профиля (имя, фамилия, username, аватар) → лог + пуш
- состояние онлайна (last_seen) → запись OnlineObservation для heatmap
- mutual_contact флаг → если стал False (был True) — это сигнал «возможно удалил тебя»."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from sqlalchemy import select
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.types import (
    UserStatusEmpty,
    UserStatusLastMonth,
    UserStatusLastWeek,
    UserStatusOffline,
    UserStatusOnline,
    UserStatusRecently,
)

from src.config import settings as app_settings
from src.core.notifier import notifier
from src.db.models import Contact, OnlineObservation, ProfileChange
from src.db.repo import get_or_create_user
from src.db.session import get_session
from src.userbot.manager import UserbotManager


logger = logging.getLogger(__name__)


POI_WATCHER_TICK_SECONDS = 5 * 60  # раз в 5 минут


def _photo_id_of(user_obj) -> int | None:
    p = getattr(user_obj, "photo", None)
    if p is None:
        return None
    return getattr(p, "photo_id", None)


def _is_online_status(status) -> bool:
    return isinstance(status, UserStatusOnline)


def _is_recent_status(status) -> bool:
    return isinstance(status, (UserStatusOnline, UserStatusRecently))


def _last_seen_at(status) -> datetime | None:
    if isinstance(status, UserStatusOffline):
        was_online = getattr(status, "was_online", None)
        if was_online:
            return was_online.replace(tzinfo=None)
    elif isinstance(status, UserStatusOnline):
        # сейчас онлайн — фиксируем "сейчас"
        return datetime.utcnow()
    return None


async def _check_one(client, owner_id: int, contact: Contact) -> None:
    """Проверяем одного POI. Открыто, без транзакции — пишем в новой сессии."""
    try:
        full = await client(GetFullUserRequest(contact.peer_id))
    except Exception:
        logger.debug("POI watcher: GetFullUser failed for %s", contact.peer_id)
        return

    user_obj = None
    for u in (full.users or []):
        if u.id == contact.peer_id:
            user_obj = u
            break
    if user_obj is None and full.users:
        user_obj = full.users[0]
    if user_obj is None:
        return

    cur_first = getattr(user_obj, "first_name", None)
    cur_last = getattr(user_obj, "last_name", None)
    cur_uname = getattr(user_obj, "username", None)
    cur_photo = _photo_id_of(user_obj)
    cur_mutual = bool(getattr(user_obj, "mutual_contact", False))
    cur_in_my = bool(getattr(user_obj, "contact", False))

    changes: list[tuple[str, str | None, str | None]] = []

    async with get_session() as session:
        c = (await session.execute(
            select(Contact).where(Contact.user_id == owner_id, Contact.peer_id == contact.peer_id)
        )).scalar_one_or_none()
        if c is None:
            return

        if c.last_known_first_name is None and c.last_known_last_name is None:
            # первая инициализация — просто заполняем без алёртов
            c.last_known_first_name = cur_first
            c.last_known_last_name = cur_last
            c.last_known_username = cur_uname
            c.last_known_photo_id = cur_photo
            c.is_mutual_contact = cur_mutual
        else:
            if (c.last_known_first_name or "") != (cur_first or "") or \
               (c.last_known_last_name or "") != (cur_last or ""):
                old = " ".join(filter(None, [c.last_known_first_name, c.last_known_last_name]))
                new = " ".join(filter(None, [cur_first, cur_last]))
                changes.append(("name", old or None, new or None))
                c.last_known_first_name = cur_first
                c.last_known_last_name = cur_last
            if (c.last_known_username or "") != (cur_uname or ""):
                changes.append(("username", c.last_known_username, cur_uname))
                c.last_known_username = cur_uname
            if c.last_known_photo_id != cur_photo:
                changes.append(("photo", str(c.last_known_photo_id), str(cur_photo)))
                c.last_known_photo_id = cur_photo
            if c.is_mutual_contact is not None and bool(c.is_mutual_contact) != cur_mutual and not cur_mutual and cur_in_my is False:
                # потерял взаимность — возможно удалил из контактов
                changes.append(("mutual", "true", "false"))
            c.is_mutual_contact = cur_mutual

        # online observation
        status = getattr(user_obj, "status", None)
        last_seen = _last_seen_at(status)
        if last_seen and (c.last_seen_online_at is None or last_seen > c.last_seen_online_at):
            c.last_seen_online_at = last_seen
            session.add(OnlineObservation(
                user_id=owner_id, peer_id=contact.peer_id, observed_at=last_seen,
            ))

        for kind, old, new in changes:
            session.add(ProfileChange(
                user_id=owner_id, peer_id=contact.peer_id,
                kind=kind, old_value=old, new_value=new,
            ))

    # пуши шлём вне сессии
    for kind, old, new in changes:
        await _notify_change(contact.display_name, kind, old, new)


async def _notify_change(name: str, kind: str, old: str | None, new: str | None) -> None:
    if kind == "name":
        await notifier.notify(f"👤 <b>{name}</b> сменил(а) имя: <i>{old}</i> → <b>{new}</b>")
    elif kind == "username":
        old_str = f"@{old}" if old else "—"
        new_str = f"@{new}" if new else "—"
        await notifier.notify(f"🏷 <b>{name}</b> сменил(а) username: {old_str} → {new_str}")
    elif kind == "photo":
        await notifier.notify(f"🖼 <b>{name}</b> сменил(а) аватар.")
    elif kind == "mutual":
        await notifier.notify(
            f"⚠ <b>{name}</b> возможно <b>удалил(а) тебя из контактов</b> "
            f"(потерян взаимный статус)."
        )


async def poi_watcher_loop(manager_getter) -> None:
    """manager_getter — callable() -> UserbotManager (отложенный доступ)."""
    while True:
        try:
            owner_tid = app_settings.owner_telegram_id
            manager: UserbotManager | None = manager_getter()
            client = manager.get_client(owner_tid) if manager else None
            if client is None:
                await asyncio.sleep(POI_WATCHER_TICK_SECONDS)
                continue

            async with get_session() as session:
                owner = await get_or_create_user(session, owner_tid)
                pois = (await session.execute(
                    select(Contact).where(
                        Contact.user_id == owner.id,
                        Contact.is_poi == True,  # noqa: E712
                        Contact.peer_kind == "user",
                    )
                )).scalars().all()
                pois = list(pois)
                owner_id = owner.id

            for c in pois:
                try:
                    await _check_one(client, owner_id, c)
                except Exception:
                    logger.exception("POI watcher: check failed for %s", c.display_name)
                await asyncio.sleep(1)  # не дудосим Telegram
        except Exception:
            logger.exception("poi_watcher tick failed")
        await asyncio.sleep(POI_WATCHER_TICK_SECONDS)
