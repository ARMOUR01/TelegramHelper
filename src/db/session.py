from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings
from src.db.models import Base


engine = create_async_engine(settings.database_url, future=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


# SQLite FTS5: virtual table + триггеры синхронизации с messages.
# Хранит rowid = messages.id.
_FTS_SETUP = [
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
        text,
        transcript,
        extracted_text,
        sender_name,
        content='messages',
        content_rowid='id',
        tokenize='unicode61 remove_diacritics 2'
    );
    """,
    """
    CREATE TRIGGER IF NOT EXISTS messages_fts_ai AFTER INSERT ON messages BEGIN
        INSERT INTO messages_fts(rowid, text, transcript, extracted_text, sender_name)
        VALUES (new.id, new.text, new.transcript, new.extracted_text, new.sender_name);
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS messages_fts_ad AFTER DELETE ON messages BEGIN
        INSERT INTO messages_fts(messages_fts, rowid, text, transcript, extracted_text, sender_name)
        VALUES('delete', old.id, old.text, old.transcript, old.extracted_text, old.sender_name);
    END;
    """,
    """
    CREATE TRIGGER IF NOT EXISTS messages_fts_au AFTER UPDATE ON messages BEGIN
        INSERT INTO messages_fts(messages_fts, rowid, text, transcript, extracted_text, sender_name)
        VALUES('delete', old.id, old.text, old.transcript, old.extracted_text, old.sender_name);
        INSERT INTO messages_fts(rowid, text, transcript, extracted_text, sender_name)
        VALUES (new.id, new.text, new.transcript, new.extracted_text, new.sender_name);
    END;
    """,
]


# Колонки, которые в новых релизах добавляются на существующую таблицу. SQLite не
# умеет ALTER ADD COLUMN IF NOT EXISTS, поэтому проверяем существующие колонки и
# доливаем недостающие.
_SOFT_MIGRATIONS: dict[str, list[tuple[str, str]]] = {
    "contacts": [
        ("is_poi", "BOOLEAN DEFAULT 0 NOT NULL"),
        ("is_mutual_contact", "BOOLEAN"),
        ("last_known_first_name", "VARCHAR(256)"),
        ("last_known_last_name", "VARCHAR(256)"),
        ("last_known_username", "VARCHAR(128)"),
        ("last_known_photo_id", "BIGINT"),
        ("last_seen_online_at", "DATETIME"),
        ("dossier", "TEXT"),
        ("dossier_updated_at", "DATETIME"),
        ("last_reengagement_at", "DATETIME"),
    ],
}


async def _apply_soft_migrations(conn) -> None:
    for table, cols in _SOFT_MIGRATIONS.items():
        existing = await conn.execute(text(f"PRAGMA table_info({table})"))
        existing_names = {row[1] for row in existing.fetchall()}
        for col_name, col_def in cols:
            if col_name in existing_names:
                continue
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}"))


async def init_db() -> None:
    settings.data_dir  # триггерит создание директории
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _apply_soft_migrations(conn)
        for stmt in _FTS_SETUP:
            await conn.execute(text(stmt))


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
