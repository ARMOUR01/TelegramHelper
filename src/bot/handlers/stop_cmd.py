"""/stop (и /cancel) — отменяет все активные операции владельца."""
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from src.bot.filters import OwnerOnly
from src.bot.task_registry import cancel_all


router = Router(name="stop_cmd")
router.message.filter(OwnerOnly())


@router.message(Command("stop", "cancel"))
async def cmd_stop(message: Message) -> None:
    n = cancel_all(message.from_user.id)
    if n == 0:
        await message.answer("Нечего отменять — сейчас ничего не выполняется.")
        return
    word_op = "операция" if n == 1 else ("операции" if 2 <= n <= 4 else "операций")
    await message.answer(f"🛑 Отменено: {n} {word_op}.")
