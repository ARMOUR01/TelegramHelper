"""/meetings — список запланированных встреч / звонков / дедлайнов по чатам."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from src.bot.filters import OwnerOnly
from src.bot.task_registry import cancellable
from src.bot.typing import typing
from src.core.meetings import format_meetings, list_meetings
from src.db.repo import get_or_create_user
from src.db.session import get_session
from src.llm.router import build_provider


logger = logging.getLogger(__name__)
router = Router(name="meetings_cmd")
router.message.filter(OwnerOnly())


@router.message(Command("meetings", "events"))
async def cmd_meetings(message: Message, command: CommandObject) -> None:
    args = (command.args or "").strip().split()
    hours = 168
    if args and args[0].isdigit():
        hours = max(1, min(720, int(args[0])))

    async with get_session() as session:
        owner = await get_or_create_user(session, message.from_user.id)
        provider = await build_provider(session, owner)
        heavy = bool(owner.settings.use_heavy_model)
        owner_id = owner.id

    if provider is None:
        await message.answer("Нет API-ключа LLM, добавь в /settings.")
        return

    notice = await message.answer(f"⏳ Сканю чаты за {hours}ч…")
    try:
        async with cancellable(message.from_user.id), typing(message):
            items = await list_meetings(
                provider, user_id=owner_id, hours=hours, future_only=True, heavy=heavy,
            )
    except asyncio.CancelledError:
        await notice.edit_text("🛑 Отменено.")
        return
    except Exception:
        logger.exception("meetings failed")
        await notice.edit_text("❌ Не получилось собрать встречи.")
        return

    await notice.edit_text(
        format_meetings(items, hours=hours, future_only=True),
        disable_web_page_preview=True,
    )
