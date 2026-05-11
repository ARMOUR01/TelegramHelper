"""Команды personal-CRM («сталкер-пак»):
/poi, /profile, /rel, /mutual, /visitors, /deletions, /activity."""
from __future__ import annotations

import asyncio
import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from src.bot.filters import OwnerOnly
from src.bot.task_registry import cancellable
from src.bot.typing import typing
from src.core.chat_service import load_chat
from src.core.contact_resolver import ContactCandidate, resolve
from src.core.stalker.activity import format_heatmap, hourly_activity
from src.core.stalker.deletions import format_deletions, list_recent_deletions
from src.core.stalker.dossier import format_dossier, get_or_build_dossier
from src.core.stalker.engagement import format_visitors, list_visitors
from src.core.stalker.mutual import check_mutual, format_mutual
from src.core.stalker.poi import list_pois, set_poi
from src.core.stalker.rel import compute_rel_stats, format_rel_stats
from src.db.repo import get_contact, get_or_create_user
from src.db.session import get_session
from src.llm.router import build_provider
from src.userbot.manager import UserbotManager


logger = logging.getLogger(__name__)
router = Router(name="stalker_cmd")
router.message.filter(OwnerOnly())
router.callback_query.filter(OwnerOnly())


def _candidates_keyboard(action: str, candidates: list[ContactCandidate]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for c in candidates:
        kb.row(InlineKeyboardButton(
            text=f"{c.label()} · {c.score}",
            callback_data=f"stk:{action}:{c.peer_id}",
        ))
    kb.row(InlineKeyboardButton(text="❌ Отмена", callback_data="stk:cancel:0"))
    return kb.as_markup()


async def _resolve_one(
    message: Message, userbot_manager: UserbotManager, query: str, action: str,
) -> int | None:
    """Резолвит имя в peer_id. Если несколько кандидатов — спрашивает.
    Возвращает peer_id или None (если попросили выбор)."""
    client = userbot_manager.get_client(message.from_user.id)
    if client is None:
        await message.answer("Сначала /login.")
        return None
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
    candidates = await resolve(client, owner, query)
    if not candidates:
        await message.answer("Не нашёл такого контакта. Уточни имя или сделай /sync.")
        return None
    if len(candidates) == 1 or candidates[0].score >= 90:
        return candidates[0].peer_id
    await message.answer(
        "Кого имеешь в виду?",
        reply_markup=_candidates_keyboard(action, candidates),
    )
    return None


@router.callback_query(F.data == "stk:cancel:0")
async def cb_cancel(callback: CallbackQuery) -> None:
    if callback.message:
        await callback.message.edit_text("Отменено.")
    await callback.answer()


# ─────────────────────────── /poi ───────────────────────────

@router.message(Command("poi"))
async def cmd_poi(message: Message, command: CommandObject, userbot_manager: UserbotManager) -> None:
    args = (command.args or "").strip()
    if not args or args == "list":
        async with get_session() as session:
            owner = await get_or_create_user(session, message.from_user.id)
            pois = await list_pois(session, user_id=owner.id)
        if not pois:
            await message.answer(
                "Список POI пуст.\n\n"
                "Добавить: <code>/poi add имя</code>\n"
                "Удалить:  <code>/poi remove имя</code>\n\n"
                "POI = «отслеживаемые контакты». Бот глубже их анализирует, "
                "пишет когда давно тишина, замечает смену имени/аватара, "
                "собирает heatmap активности."
            )
            return
        lines = [f"⭐ <b>POI ({len(pois)}):</b>", ""]
        for c in pois:
            lines.append(f"• <b>{c.display_name}</b>" + (f" · @{c.username}" if c.username else ""))
        lines.append("\n<i>/poi remove имя — снять; /profile имя — досье; /rel имя — стэты.</i>")
        await message.answer("\n".join(lines))
        return

    parts = args.split(maxsplit=1)
    cmd = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""
    if cmd not in ("add", "remove", "rm", "del"):
        await message.answer(
            "Использование:\n"
            "<code>/poi list</code>\n"
            "<code>/poi add имя</code>\n"
            "<code>/poi remove имя</code>"
        )
        return
    if not rest:
        await message.answer("Укажи имя контакта после команды.")
        return
    action = "addpoi" if cmd == "add" else "rmpoi"
    peer_id = await _resolve_one(message, userbot_manager, rest, action)
    if peer_id is None:
        return
    await _apply_poi(message, message.from_user.id, peer_id, is_poi=(cmd == "add"))


async def _apply_poi(target: Message | CallbackQuery, user_tid: int, peer_id: int, *, is_poi: bool) -> None:
    async with get_session() as session:
        owner = await get_or_create_user(session, user_tid)
        ok = await set_poi(session, user_id=owner.id, peer_id=peer_id, is_poi=is_poi)
        contact = await get_contact(session, owner, peer_id)
    if not ok or contact is None:
        text = "Контакт не найден в БД (попробуй /sync)."
    elif is_poi:
        text = (
            f"⭐ <b>{contact.display_name}</b> добавлен в POI.\n\n"
            f"Теперь бот:\n"
            f"• следит за сменой имени/username/аватара\n"
            f"• собирает heatmap активности\n"
            f"• пингует если тишина &gt; 14 дн\n"
            f"• помечает в /visitors звёздочкой"
        )
    else:
        text = f"☆ <b>{contact.display_name}</b> снят с POI."

    if isinstance(target, CallbackQuery):
        if target.message:
            await target.message.edit_text(text)
        await target.answer()
    else:
        await target.answer(text)


@router.callback_query(F.data.startswith("stk:addpoi:"))
async def cb_addpoi(callback: CallbackQuery) -> None:
    peer_id = int(callback.data.split(":")[2])
    await _apply_poi(callback, callback.from_user.id, peer_id, is_poi=True)


@router.callback_query(F.data.startswith("stk:rmpoi:"))
async def cb_rmpoi(callback: CallbackQuery) -> None:
    peer_id = int(callback.data.split(":")[2])
    await _apply_poi(callback, callback.from_user.id, peer_id, is_poi=False)


# ─────────────────────────── /profile ───────────────────────────

@router.message(Command("profile"))
async def cmd_profile(message: Message, command: CommandObject, userbot_manager: UserbotManager) -> None:
    args = (command.args or "").strip()
    force = False
    if args.endswith("--refresh") or args.endswith("--force"):
        force = True
        args = args.rsplit(" ", 1)[0].strip()
    if not args:
        await message.answer("Использование: <code>/profile имя</code> (или <code>/profile имя --refresh</code>)")
        return
    peer_id = await _resolve_one(message, userbot_manager, args, ("profile_r" if force else "profile"))
    if peer_id is None:
        return
    await _run_profile(message, message.from_user.id, peer_id, userbot_manager, force=force)


async def _run_profile(target: Message | CallbackQuery, user_tid: int, peer_id: int,
                        userbot_manager: UserbotManager, *, force: bool) -> None:
    client = userbot_manager.get_client(user_tid)
    if client is None:
        await _edit_or_answer(target, "Сначала /login.")
        return

    notice_text = "⏳ Собираю досье…"
    await _edit_or_answer(target, notice_text)

    messages = await load_chat(client, user_tid, peer_id, limit=500, transcribe=False)
    async with get_session() as session:
        owner = await get_or_create_user(session, user_tid)
        contact = await get_contact(session, owner, peer_id)
        provider = await build_provider(session, owner)
        heavy = owner.settings.use_heavy_model
        if contact is None:
            await _edit_or_answer(target, "Контакт не найден в БД.")
            return
        if provider is None:
            await _edit_or_answer(target, "Нет API-ключа LLM, добавь в /settings.")
            return
        try:
            async with cancellable(user_tid), typing(target):
                data, updated = await get_or_build_dossier(
                    session, provider, contact, messages, force_refresh=force, heavy=heavy,
                )
        except asyncio.CancelledError:
            await _edit_or_answer(target, "🛑 Отменено.")
            return

    text = format_dossier(contact.display_name, data, updated_at=updated)
    await _edit_or_answer(target, text)


@router.callback_query(F.data.startswith("stk:profile:"))
async def cb_profile(callback: CallbackQuery, userbot_manager: UserbotManager) -> None:
    peer_id = int(callback.data.split(":")[2])
    await _run_profile(callback, callback.from_user.id, peer_id, userbot_manager, force=False)


@router.callback_query(F.data.startswith("stk:profile_r:"))
async def cb_profile_refresh(callback: CallbackQuery, userbot_manager: UserbotManager) -> None:
    peer_id = int(callback.data.split(":")[2])
    await _run_profile(callback, callback.from_user.id, peer_id, userbot_manager, force=True)


# ─────────────────────────── /rel ───────────────────────────

@router.message(Command("rel"))
async def cmd_rel(message: Message, command: CommandObject, userbot_manager: UserbotManager) -> None:
    args = (command.args or "").strip()
    days = 30
    parts = args.split()
    name_parts: list[str] = []
    for p in parts:
        if p.isdigit():
            days = max(1, min(365, int(p)))
        else:
            name_parts.append(p)
    name = " ".join(name_parts)
    if not name:
        await message.answer("Использование: <code>/rel имя [дней=30]</code>")
        return
    peer_id = await _resolve_one(message, userbot_manager, name, f"rel_{days}")
    if peer_id is None:
        return
    await _run_rel(message, message.from_user.id, peer_id, days)


async def _run_rel(target: Message | CallbackQuery, user_tid: int, peer_id: int, days: int) -> None:
    async with get_session() as session:
        owner = await get_or_create_user(session, user_tid)
        contact = await get_contact(session, owner, peer_id)
        if contact is None:
            await _edit_or_answer(target, "Контакт не найден.")
            return
        stats = await compute_rel_stats(session, user_id=owner.id, peer_id=peer_id, days=days)
    await _edit_or_answer(target, format_rel_stats(contact.display_name, stats))


@router.callback_query(F.data.startswith("stk:rel_"))
async def cb_rel(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    action = parts[1]  # rel_<days>
    days = int(action.split("_")[1])
    peer_id = int(parts[2])
    await _run_rel(callback, callback.from_user.id, peer_id, days)


# ─────────────────────────── /mutual ───────────────────────────

@router.message(Command("mutual"))
async def cmd_mutual(message: Message, command: CommandObject, userbot_manager: UserbotManager) -> None:
    args = (command.args or "").strip()
    if not args:
        await message.answer("Использование: <code>/mutual имя</code>")
        return
    peer_id = await _resolve_one(message, userbot_manager, args, "mutual")
    if peer_id is None:
        return
    await _run_mutual(message, message.from_user.id, peer_id, userbot_manager)


async def _run_mutual(target: Message | CallbackQuery, user_tid: int, peer_id: int,
                       userbot_manager: UserbotManager) -> None:
    client = userbot_manager.get_client(user_tid)
    if client is None:
        await _edit_or_answer(target, "Сначала /login.")
        return
    info = await check_mutual(client, peer_id)
    if info is None:
        await _edit_or_answer(target, "Не удалось получить инфо от Telegram.")
        return
    async with get_session() as session:
        owner = await get_or_create_user(session, user_tid)
        contact = await get_contact(session, owner, peer_id)
    name = contact.display_name if contact else str(peer_id)
    await _edit_or_answer(target, format_mutual(name, info))


@router.callback_query(F.data.startswith("stk:mutual:"))
async def cb_mutual(callback: CallbackQuery, userbot_manager: UserbotManager) -> None:
    peer_id = int(callback.data.split(":")[2])
    await _run_mutual(callback, callback.from_user.id, peer_id, userbot_manager)


# ─────────────────────────── /visitors ───────────────────────────

@router.message(Command("visitors"))
async def cmd_visitors(message: Message, command: CommandObject) -> None:
    args = (command.args or "").strip().split()
    hours = 24 * 7
    top_n = 20
    if args:
        try:
            hours = max(1, min(24 * 30, int(args[0])))
        except ValueError:
            pass
    if len(args) > 1:
        try:
            top_n = max(1, min(50, int(args[1])))
        except ValueError:
            pass
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        rows = await list_visitors(session, user_id=owner.id, hours=hours, top_n=top_n)
    await message.answer(format_visitors(rows, hours))


# ─────────────────────────── /deletions ───────────────────────────

@router.message(Command("deletions", "deleted"))
async def cmd_deletions(message: Message, command: CommandObject) -> None:
    args = (command.args or "").strip()
    hours = 72
    if args.isdigit():
        hours = max(1, min(24 * 30, int(args)))
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        rows = await list_recent_deletions(session, user_id=owner.id, hours=hours, limit=30)
    await message.answer(format_deletions(rows, hours))


# ─────────────────────────── /activity ───────────────────────────

@router.message(Command("activity"))
async def cmd_activity(message: Message, command: CommandObject, userbot_manager: UserbotManager) -> None:
    args = (command.args or "").strip()
    if not args:
        await message.answer("Использование: <code>/activity имя [дней=14]</code>")
        return
    parts = args.split()
    days = 14
    name_parts: list[str] = []
    for p in parts:
        if p.isdigit():
            days = max(1, min(60, int(p)))
        else:
            name_parts.append(p)
    name = " ".join(name_parts)
    if not name:
        await message.answer("Укажи имя контакта.")
        return
    peer_id = await _resolve_one(message, userbot_manager, name, f"act_{days}")
    if peer_id is None:
        return
    await _run_activity(message, message.from_user.id, peer_id, days)


async def _run_activity(target: Message | CallbackQuery, user_tid: int, peer_id: int, days: int) -> None:
    async with get_session() as session:
        owner = await get_or_create_user(session, user_tid)
        contact = await get_contact(session, owner, peer_id)
        tz_offset = 0  # tz offset displays UTC; локализация — задача побольше
        buckets = await hourly_activity(session, user_id=owner.id, peer_id=peer_id, days=days)
    name = contact.display_name if contact else str(peer_id)
    await _edit_or_answer(target, format_heatmap(name, buckets, days=days, tz_offset_hours=tz_offset))


@router.callback_query(F.data.startswith("stk:act_"))
async def cb_activity(callback: CallbackQuery) -> None:
    parts = callback.data.split(":")
    days = int(parts[1].split("_")[1])
    peer_id = int(parts[2])
    await _run_activity(callback, callback.from_user.id, peer_id, days)


# ─────────────────────────── helpers ───────────────────────────

async def _edit_or_answer(target: Message | CallbackQuery, text: str) -> None:
    if isinstance(target, CallbackQuery):
        try:
            if target.message:
                await target.message.edit_text(text)
            await target.answer()
            return
        except Exception:
            if target.message:
                await target.message.answer(text)
            await target.answer()
            return
    await target.answer(text)
