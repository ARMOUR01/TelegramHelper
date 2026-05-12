"""/del <имя> [количество] — удалить мои последние сообщения в чате.
Также используется агентом для интента delete_message."""
from __future__ import annotations

import json
import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select

from src.bot.filters import OwnerOnly
from src.core.contact_resolver import ContactCandidate, resolve
from src.db.models import Message as MessageRow
from src.db.repo import (
    create_pending_action,
    delete_pending_action,
    get_contact,
    get_or_create_user,
    get_pending_action,
)
from src.db.session import get_session
from src.userbot.manager import UserbotManager


logger = logging.getLogger(__name__)
router = Router(name="delete_cmd")
router.message.filter(OwnerOnly())
router.callback_query.filter(OwnerOnly())


def _confirm_keyboard(action_id: int):
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del:confirm:{action_id}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data=f"del:cancel:{action_id}"),
    )
    return kb.as_markup()


def _candidates_keyboard(candidates: list[ContactCandidate], count: int, match_text: str | None):
    kb = InlineKeyboardBuilder()
    for c in candidates:
        kb.row(InlineKeyboardButton(
            text=f"{c.label()} · {c.score}",
            callback_data=f"del:pick:{c.peer_id}:{count}",
        ))
    kb.row(InlineKeyboardButton(text="❌ Отмена", callback_data="del:cancel:0"))
    return kb.as_markup()


async def _find_outgoing_to_delete(
    *, user_id: int, peer_id: int, count: int, match_text: str | None,
) -> list[MessageRow]:
    """Находит последние мои исходящие в чате. Если match_text задан — фильтрует
    по подстроке в text/transcript/extracted_text."""
    async with get_session() as session:
        q = (
            select(MessageRow)
            .where(
                MessageRow.user_id == user_id,
                MessageRow.peer_id == peer_id,
                MessageRow.is_outgoing.is_(True),
            )
            .order_by(MessageRow.date.desc())
            .limit(50)
        )
        rows = list((await session.execute(q)).scalars().all())

    if match_text:
        needle = match_text.lower()
        rows = [
            r for r in rows
            if needle in ((r.text or "") + " " + (r.transcript or "") + " " + (r.extracted_text or "")).lower()
        ]
    return rows[:count]


async def offer_delete(
    target: Message,
    user_telegram_id: int,
    peer_id: int,
    *,
    count: int,
    match_text: str | None,
) -> None:
    """Показывает превью того что собираемся удалить + кнопки подтверждения."""
    async with get_session() as session:
        owner = await get_or_create_user(session, user_telegram_id)
        contact = await get_contact(session, owner, peer_id)
        owner_id = owner.id

    if contact is None:
        await target.answer("Контакт не найден в БД.")
        return

    msgs = await _find_outgoing_to_delete(
        user_id=owner_id, peer_id=peer_id,
        count=max(1, min(10, count)), match_text=match_text,
    )
    if not msgs:
        await target.answer(
            f"Не нашёл мои сообщения в чате с <b>{contact.display_name}</b>"
            + (f" с текстом «<i>{match_text}</i>»" if match_text else "")
            + ". Возможно, ещё не зеркалировались — попробуй позже."
        )
        return

    preview_lines = []
    for m in msgs:
        body = m.text or m.transcript or m.extracted_text or f"[{m.kind}]"
        body = body[:120].replace("\n", " ") + ("…" if len(body) > 120 else "")
        when = m.date.strftime("%H:%M")
        preview_lines.append(f"• <i>{when}</i> {body}")

    payload = json.dumps({
        "peer_id": peer_id,
        "message_ids": [m.message_id for m in msgs],
    }, ensure_ascii=False)
    async with get_session() as session:
        action = await create_pending_action(
            session, user_id=owner_id, kind="delete_messages", payload=payload,
        )

    await target.answer(
        f"🗑 <b>Удалить из чата с {contact.display_name}</b>\n"
        f"<i>(для всех — revoke)</i>\n\n"
        + "\n".join(preview_lines)
        + "\n\nПодтверждаешь?",
        reply_markup=_confirm_keyboard(action.id),
    )


@router.message(Command("del", "delete"))
async def cmd_delete(
    message: Message, command: CommandObject, userbot_manager: UserbotManager,
) -> None:
    client = userbot_manager.get_client(message.from_user.id)
    if client is None:
        await message.answer("Сначала /login.")
        return

    args = (command.args or "").strip()
    if not args:
        await message.answer(
            "Использование:\n"
            "<code>/del имя</code> — удалить последнее моё сообщение в чате\n"
            "<code>/del имя 3</code> — удалить 3 последних моих\n"
            "<code>/del имя про созвон</code> — удалить моё с подстрокой «про созвон»"
        )
        return

    parts = args.split()
    count = 1
    match_text: str | None = None
    name_parts: list[str] = []
    for p in parts:
        if p.isdigit():
            count = max(1, min(10, int(p)))
        else:
            name_parts.append(p)
    # Если в args больше слов чем одно имя — последние слова могут быть match_text.
    # Грубая эвристика: если 2+ нечисловых слов — первое имя, остальное match_text.
    if len(name_parts) >= 2:
        name = name_parts[0]
        match_text = " ".join(name_parts[1:])
    else:
        name = " ".join(name_parts)

    if not name:
        await message.answer("Не понял имя. Пример: <code>/del Маша</code>")
        return

    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)

    candidates = await resolve(client, owner, name)
    if not candidates:
        await message.answer(f"Не нашёл контакт «{name}». Попробуй /sync.")
        return
    if len(candidates) == 1 or candidates[0].score >= 90:
        await offer_delete(
            message, message.from_user.id, candidates[0].peer_id,
            count=count, match_text=match_text,
        )
        return
    await message.answer(
        f"Кого имеешь в виду?",
        reply_markup=_candidates_keyboard(candidates, count, match_text),
    )


@router.callback_query(F.data.startswith("del:pick:"))
async def cb_pick(callback: CallbackQuery, userbot_manager: UserbotManager) -> None:
    _, _, peer_id_s, count_s = callback.data.split(":")
    peer_id = int(peer_id_s)
    count = int(count_s)
    if callback.message:
        await callback.message.edit_text("⏳ Готовлю превью…")
    await callback.answer()
    await offer_delete(callback.message, callback.from_user.id, peer_id, count=count, match_text=None)


@router.callback_query(F.data.startswith("del:cancel:"))
async def cb_cancel(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    action_id = int(parts[2])
    if action_id:
        async with get_session() as session:
            await delete_pending_action(session, action_id)
    if callback.message:
        await callback.message.edit_text("Отменено.")
    await callback.answer()


@router.callback_query(F.data.startswith("del:confirm:"))
async def cb_confirm(callback: CallbackQuery, userbot_manager: UserbotManager) -> None:
    action_id = int(callback.data.split(":")[2])
    client = userbot_manager.get_client(callback.from_user.id)
    if client is None:
        await callback.answer("Сначала /login", show_alert=True)
        return

    async with get_session() as session:
        action = await get_pending_action(session, action_id)
        if action is None or action.kind != "delete_messages":
            await callback.answer("Запрос истёк.", show_alert=True)
            return
        payload = json.loads(action.payload)
        await delete_pending_action(session, action_id)

    peer_id = int(payload["peer_id"])
    message_ids = list(payload["message_ids"])

    try:
        await client.delete_messages(peer_id, message_ids, revoke=True)
    except Exception:
        logger.exception("delete_messages failed")
        if callback.message:
            await callback.message.edit_text("❌ Не получилось удалить (см. логи).")
        await callback.answer()
        return

    if callback.message:
        await callback.message.edit_text(
            f"🗑 Удалено: <b>{len(message_ids)}</b> сообщ. (revoke-for-everyone)."
        )
    await callback.answer()
