"""Досье по контакту: Gemini читает локальную историю и собирает профиль:
интересы, важные люди вокруг, тон, паттерны общения, дни рождения."""
from __future__ import annotations

import json
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.chat_service import message_to_text
from src.core.text_sanitizer import sanitize_html
from src.db.models import Contact, Message
from src.llm.base import ChatMessage, LLMProvider


logger = logging.getLogger(__name__)


DOSSIER_SYSTEM = (
    "Ты составляешь компактное досье на контакт из истории переписки. "
    "Только то, что реально упоминается. Не выдумывай. Верни СТРОГО JSON-объект:\n"
    "{\n"
    '  "summary":       "1-2 предложения: кто этот человек для меня, формат отношений",\n'
    '  "interests":     ["короткие фразы", "..."],\n'
    '  "people":        ["имена близких: жена Лена, кошка Мурка", "..."],\n'
    '  "locations":     ["упомянутые места: Питер, СПб, дача", "..."],\n'
    '  "events":        ["важные события/даты: \\"др 15 мая\\"", "..."],\n'
    '  "tone":          "одной фразой про стиль общения",\n'
    '  "patterns":      ["когда обычно пишет, как длинно, что любит обсуждать"],\n'
    '  "open_topics":   ["незакрытые вопросы / договорённости"]\n'
    "}\n"
    "Все поля — массивы (или строка для summary/tone). Если пусто — пустой массив. JSON, без markdown."
)


async def build_dossier(
    provider: LLMProvider,
    contact: Contact,
    messages: list[Message],
    *,
    heavy: bool = False,
) -> dict:
    if not messages:
        return {}
    # ограничим объём: последние ~500 сообщений
    msgs = messages[-500:]
    transcript = "\n".join(message_to_text(m) for m in msgs)
    user_prompt = (
        f"Контакт: {contact.display_name}\n\n"
        f"Переписка ({len(msgs)} сообщений):\n{transcript}\n\n"
        "Собери JSON-досье по схеме."
    )
    raw = await provider.chat(
        [
            ChatMessage(role="system", content=DOSSIER_SYSTEM),
            ChatMessage(role="user", content=user_prompt),
        ],
        heavy=heavy,
    )
    # очищаем от ```json
    s = raw.strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:]
    try:
        data = json.loads(s)
        if not isinstance(data, dict):
            return {}
        return data
    except Exception:
        logger.warning("dossier JSON parse failed for %s", contact.display_name)
        return {}


def format_dossier(name: str, data: dict, *, updated_at: datetime | None = None) -> str:
    if not data:
        return f"Пока не получилось собрать досье на {name}. Маловато сообщений или LLM не справился."

    def _arr(key: str) -> list[str]:
        v = data.get(key)
        if isinstance(v, list):
            return [str(x) for x in v if str(x).strip()]
        return []

    summary = str(data.get("summary") or "").strip()
    tone = str(data.get("tone") or "").strip()

    lines = [f"📇 <b>Досье — {name}</b>"]
    if updated_at:
        lines.append(f"<i>обновлено: {updated_at.strftime('%Y-%m-%d %H:%M UTC')}</i>")
    lines.append("")
    if summary:
        lines.append(summary)
        lines.append("")

    def _section(emoji: str, title: str, items: list[str]) -> None:
        if not items:
            return
        lines.append(f"{emoji} <b>{title}</b>")
        for it in items[:12]:
            lines.append(f"• {it}")
        lines.append("")

    _section("🎯", "Интересы", _arr("interests"))
    _section("👥", "Важные люди вокруг", _arr("people"))
    _section("📍", "Места", _arr("locations"))
    _section("📅", "События / даты", _arr("events"))
    _section("🌡", "Шаблоны общения", _arr("patterns"))
    _section("❓", "Открытые темы", _arr("open_topics"))

    if tone:
        lines.append(f"💬 <i>Тон:</i> {tone}")

    return sanitize_html("\n".join(lines).strip())


async def get_or_build_dossier(
    session: AsyncSession,
    provider: LLMProvider,
    contact: Contact,
    messages: list[Message],
    *,
    force_refresh: bool = False,
    heavy: bool = False,
) -> tuple[dict, datetime | None]:
    """Возвращает (досье, время обновления). Кэширует в Contact.dossier."""
    if not force_refresh and contact.dossier:
        try:
            return json.loads(contact.dossier), contact.dossier_updated_at
        except Exception:
            pass
    data = await build_dossier(provider, contact, messages, heavy=heavy)
    contact.dossier = json.dumps(data, ensure_ascii=False)
    contact.dossier_updated_at = datetime.utcnow()
    return data, contact.dossier_updated_at
