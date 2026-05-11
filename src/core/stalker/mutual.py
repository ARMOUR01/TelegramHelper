"""Mutual contact check через Telegram API (users.getFullUser)."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from telethon import TelegramClient
from telethon.tl.functions.users import GetFullUserRequest


logger = logging.getLogger(__name__)


@dataclass
class MutualInfo:
    in_my_contacts: bool
    has_me_in_contacts: bool | None
    is_mutual: bool
    phone: str | None
    username: str | None


async def check_mutual(client: TelegramClient, peer_id: int) -> MutualInfo | None:
    """Проверяет статус взаимности контакта.

    Telegram отдаёт `mutual_contact` (взаимный) и `contact` (у меня в контактах).
    «Он добавил меня» = mutual_contact (без contact это не отвечает напрямую,
    но если он мне не добавлен, а mutual_contact=True — значит у него я есть)."""
    try:
        full = await client(GetFullUserRequest(peer_id))
    except Exception:
        logger.exception("GetFullUserRequest failed for %s", peer_id)
        return None
    users = getattr(full, "users", []) or []
    user_obj = next((u for u in users if u.id == peer_id), users[0] if users else None)
    if user_obj is None:
        return None

    in_my = bool(getattr(user_obj, "contact", False))
    mutual = bool(getattr(user_obj, "mutual_contact", False))
    # Если я добавил его, а он не добавил меня — `contact=True, mutual_contact=False`.
    # Если он добавил меня, а я нет — Telegram отдаёт нам обычно `contact=False, mutual_contact=False`
    # (без contact mutual вычислить нельзя, поэтому помечаем None).
    if in_my:
        has_me = mutual
    else:
        has_me = None  # неизвестно достоверно

    return MutualInfo(
        in_my_contacts=in_my,
        has_me_in_contacts=has_me,
        is_mutual=mutual,
        phone=getattr(user_obj, "phone", None),
        username=getattr(user_obj, "username", None),
    )


def format_mutual(name: str, info: MutualInfo) -> str:
    lines = [f"🤝 <b>Контактный статус — {name}</b>", ""]
    lines.append(f"• У тебя в контактах: {'✅ да' if info.in_my_contacts else '❌ нет'}")
    if info.has_me_in_contacts is None:
        lines.append("• Он(а) добавил(а) тебя: 🤷 неизвестно (Telegram не отдаёт это пока ты сам не в его контактах)")
    elif info.has_me_in_contacts:
        lines.append("• Он(а) добавил(а) тебя: ✅ да (взаимный)")
    else:
        lines.append("• Он(а) добавил(а) тебя: ❌ нет (только у тебя)")

    if info.is_mutual:
        lines.append("\n💡 Полный взаимный контакт.")
    else:
        if info.in_my_contacts and info.has_me_in_contacts is False:
            lines.append("\n⚠ Один-сторонний: ты у него не в контактах. "
                         "Это может означать удалили или никогда не было.")
    return "\n".join(lines)
