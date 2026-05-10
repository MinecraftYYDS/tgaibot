from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    telegram_bot_token: str = Field(alias="TELEGRAM_BOT_TOKEN")
    telegram_allowed_chat_ids: str = Field(default="", alias="TELEGRAM_ALLOWED_CHAT_IDS")

    db_path: str = Field(default="./data/tgaibot.db", alias="DB_PATH")

    default_model_mode: str = Field(default="auto", alias="DEFAULT_MODEL_MODE")
    reasoning_mode: str = Field(default="visible_then_hide", alias="REASONING_MODE")
    stream_edit_interval_seconds: float = Field(default=0.8, alias="STREAM_EDIT_INTERVAL_SECONDS")

    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_base_url: str = Field(default="", alias="OPENAI_BASE_URL")
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    google_api_key: str = Field(default="", alias="GOOGLE_API_KEY")

    fastapi_host: str = Field(default="127.0.0.1", alias="FASTAPI_HOST")
    fastapi_port: int = Field(default=8011, alias="FASTAPI_PORT")

    @property
    def allowed_chat_ids(self) -> set[int]:
        if not self.telegram_allowed_chat_ids.strip():
            return set()
        parts = [p.strip() for p in self.telegram_allowed_chat_ids.split(",") if p.strip()]
        values: set[int] = set()
        for part in parts:
            try:
                values.add(int(part))
            except ValueError:
                continue
        return values


settings = Settings()
