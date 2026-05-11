"""Реестр активных asyncio-задач по пользователю.

Нужен для команды /stop: пока бот выполняет долгую операцию (LLM-запрос,
/all_summary, /digest и т.п.), задача регистрируется в реестре. Когда
владелец шлёт /stop — все его активные задачи отменяются через task.cancel().

CancelledError корректно прокинется внутрь LLM-клиента (aiohttp / httpx
поддерживают отмену) и остановит запрос на середине, не прожигая токены.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator


logger = logging.getLogger(__name__)


_active: dict[int, set[asyncio.Task]] = {}


def _register(user_id: int, task: asyncio.Task) -> None:
    _active.setdefault(user_id, set()).add(task)
    task.add_done_callback(lambda t: _unregister(user_id, t))


def _unregister(user_id: int, task: asyncio.Task) -> None:
    tasks = _active.get(user_id)
    if tasks is None:
        return
    tasks.discard(task)
    if not tasks:
        _active.pop(user_id, None)


def cancel_all(user_id: int) -> int:
    """Отменяет все активные задачи юзера. Возвращает кол-во отменённых."""
    tasks = list(_active.get(user_id, ()))
    cancelled = 0
    for t in tasks:
        if not t.done():
            t.cancel()
            cancelled += 1
    return cancelled


def count(user_id: int) -> int:
    return len(_active.get(user_id, ()))


@contextlib.asynccontextmanager
async def cancellable(user_id: int) -> AsyncIterator[None]:
    """Регистрирует current task в реестре на время выполнения блока.

    После выхода (любым способом — нормально, исключение, отмена) задача
    автоматически дерегистрируется. CancelledError из cancel_all() корректно
    долетит до этого блока."""
    current = asyncio.current_task()
    if current is None:
        # вне asyncio loop — fallback, ничего не регистрируем
        yield
        return
    _register(user_id, current)
    try:
        yield
    finally:
        _unregister(user_id, current)
