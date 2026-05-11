from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class LLMDefaults:
    # Имена моделей на май 2026 — менять при выходе новых
    OPENAI_CHAT_LIGHT = "gpt-5-mini"
    OPENAI_CHAT_HEAVY = "gpt-5.5"
    OPENAI_EMBED = "text-embedding-3-small"

    # Google API принимает только generic-алиасы без номера версии: они автоматически
    # указывают на актуальную стабильную модель (gemini-3-flash и т.п. дают 404).
    GEMINI_CHAT_LIGHT = "gemini-flash-latest"
    GEMINI_CHAT_HEAVY = "gemini-pro-latest"
    GEMINI_EMBED = "text-embedding-004"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: str = Field(..., description="Токен control-бота из @BotFather")
    owner_telegram_id: int = Field(..., description="Telegram user_id единственного владельца")
    encryption_key: str = Field(..., description="Fernet-ключ (base64)")
    database_url: str = Field("sqlite+aiosqlite:///data/app.db")

    @property
    def data_dir(self) -> Path:
        path = PROJECT_ROOT / "data"
        path.mkdir(parents=True, exist_ok=True)
        return path


settings = Settings()
