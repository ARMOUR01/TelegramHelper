"""/ai_on / /ai_off / /ai_list — per-contact AI takeover управление.

`/ai_on <имя> [мин]` — включить, ставит ai_takeover_enabled=True и idle_min.
`/ai_off <имя>` — выключить.
`/ai_list` — кто сейчас под AI takeover.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select

from src.bot.filters import OwnerOnly
from src.core.contact_resolver import resolve
from src.db.models import Contact
from src.db.repo import get_contact, get_or_create_user
from src.db.session import get_session
from src.userbot.manager import UserbotManager


logger = logging.getLogger(__name__)
router = Router(name="ai_cmd")
router.message.filter(OwnerOnly())
router.callback_query.filter(OwnerOnly())


DEFAULT_IDLE_MIN = 30


async def _set_takeover(
    telegram_id: int,
    peer_id: int,
    *,
    enabled: bool,
    idle_min: int | None = None,
    persona: str | None = None,
) -> tuple[bool, str]:
    async with get_session() as session:
        owner = await get_or_create_user(session, telegram_id)
        contact = await get_contact(session, owner, peer_id)
        if contact is None:
            return False, "Контакт не найден"
        contact.ai_takeover_enabled = enabled
        if idle_min is not None:
            contact.ai_takeover_idle_min = max(1, min(720, idle_min))
        if persona is not None:
            contact.ai_takeover_persona = persona or None
        if not enabled:
            # сбрасываем флаг «представление сделано», чтобы при следующем включении
            # бот заново поздоровался.
            contact.ai_takeover_intro_sent_at = None
        return True, contact.display_name


@router.message(Command("ai_on"))
async def cmd_ai_on(
    message: Message,
    command: CommandObject,
    userbot_manager: UserbotManager,
) -> None:
    raw = (command.args or "").strip()
    if not raw:
        await message.answer(
            "Пример: <code>/ai_on Маша</code> · <code>/ai_on Артём 60</code> "
            "(60 — минут моего молчания до автоответа, по умолчанию 30)"
        )
        return

    parts = raw.rsplit(maxsplit=1)
    idle_min = DEFAULT_IDLE_MIN
    if len(parts) == 2 and parts[1].isdigit():
        name_query, idle_min_str = parts[0], parts[1]
        idle_min = int(idle_min_str)
    else:
        name_query = raw

    client = userbot_manager.get_client(message.from_user.id)
    if client is None:
        await message.answer("Сначала /login — нужен подключённый Telegram-аккаунт.")
        return
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
    candidates = await resolve(client, owner, name_query)
    if not candidates:
        await message.answer(f"Не нашёл «{name_query}». Попробуй /sync.")
        return
    if len(candidates) > 1 and candidates[0].score < 90:
        await message.answer(
            f"Несколько вариантов под «{name_query}». Уточни: " +
            ", ".join(c.display_name for c in candidates[:5])
        )
        return

    target = candidates[0]
    ok, name = await _set_takeover(
        message.from_user.id, target.peer_id,
        enabled=True, idle_min=idle_min,
    )
    if not ok:
        await message.answer(f"Не получилось: {name}")
        return
    await message.answer(
        f"🤖 <b>AI ведёт чат</b> с <b>{name}</b>\n"
        f"Через <b>{idle_min} мин</b> моего молчания AI начнёт отвечать сам.\n"
        f"В первом ответе представится как мой AI-ассистент.\n\n"
        f"Выключить: <code>/ai_off {name}</code>"
    )


@router.message(Command("ai_off"))
async def cmd_ai_off(
    message: Message,
    command: CommandObject,
    userbot_manager: UserbotManager,
) -> None:
    name_query = (command.args or "").strip()
    if not name_query:
        await message.answer("Пример: <code>/ai_off Маша</code>")
        return

    client = userbot_manager.get_client(message.from_user.id)
    if client is None:
        await message.answer("Сначала /login.")
        return
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
    candidates = await resolve(client, owner, name_query)
    if not candidates:
        await message.answer(f"Не нашёл «{name_query}».")
        return
    target = candidates[0]
    ok, name = await _set_takeover(message.from_user.id, target.peer_id, enabled=False)
    if not ok:
        await message.answer(f"Не получилось: {name}")
        return
    await message.answer(f"🔴 AI выключен на <b>{name}</b>. Теперь отвечаешь сам.")


@router.message(Command("ai_list"))
async def cmd_ai_list(message: Message) -> None:
    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        result = await session.execute(
            select(Contact)
            .where(Contact.user_id == owner.id, Contact.ai_takeover_enabled.is_(True))
            .order_by(Contact.display_name)
        )
        contacts = result.scalars().all()

    if not contacts:
        await message.answer(
            "AI takeover ни на ком не включён.\n\n"
            "Включить: <code>/ai_on имя</code>\n"
            "Подробнее: /help"
        )
        return

    lines = [f"🤖 <b>AI ведёт {len(contacts)} чат(ов)</b>\n"]
    for c in contacts:
        last = c.ai_takeover_last_reply_at.strftime("%d.%m %H:%M") if c.ai_takeover_last_reply_at else "—"
        intro = "✅" if c.ai_takeover_intro_sent_at else "—"
        lines.append(
            f"• <b>{c.display_name}</b> · окно {c.ai_takeover_idle_min}мин · "
            f"представился: {intro} · последний ответ: {last}"
        )
    lines.append("\nВыключить: <code>/ai_off имя</code>")
    await message.answer("\n".join(lines))


def takeover_keyboard(peer_id: int, enabled: bool) -> InlineKeyboardBuilder:
    """Возвращает строку клавиатуры с одной кнопкой включить/выключить AI."""
    kb = InlineKeyboardBuilder()
    if enabled:
        kb.row(InlineKeyboardButton(
            text="🔴 Выключить AI takeover",
            callback_data=f"ai:off:{peer_id}",
        ))
    else:
        kb.row(InlineKeyboardButton(
            text="🤖 AI ведёт чат за меня",
            callback_data=f"ai:on:{peer_id}",
        ))
    return kb


@router.callback_query(F.data.startswith("ai:on:"))
async def cb_ai_on(callback: CallbackQuery) -> None:
    peer_id = int(callback.data.split(":", 2)[2])
    ok, name = await _set_takeover(
        callback.from_user.id, peer_id,
        enabled=True, idle_min=DEFAULT_IDLE_MIN,
    )
    if not ok:
        await callback.answer(name, show_alert=True)
        return
    await callback.answer(f"AI ведёт чат с {name}")
    if callback.message:
        await callback.message.answer(
            f"🤖 <b>AI takeover ВКЛЮЧЁН</b> на <b>{name}</b>\n"
            f"Через {DEFAULT_IDLE_MIN} мин моего молчания AI начнёт отвечать сам."
        )


@router.callback_query(F.data.startswith("ai:off:"))
async def cb_ai_off(callback: CallbackQuery) -> None:
    peer_id = int(callback.data.split(":", 2)[2])
    ok, name = await _set_takeover(callback.from_user.id, peer_id, enabled=False)
    if not ok:
        await callback.answer(name, show_alert=True)
        return
    await callback.answer(f"AI выключен на {name}")
    if callback.message:
        await callback.message.answer(f"🔴 AI выключен на <b>{name}</b>")
