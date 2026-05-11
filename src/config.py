from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.model_catalog import ModelProfile, load_model_catalog


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore", protected_namespaces=("settings_",))

    telegram_bot_token: str = Field(alias="TELEGRAM_BOT_TOKEN")
    telegram_allowed_chat_ids: str = Field(default="", alias="TELEGRAM_ALLOWED_CHAT_IDS")
    telegram_allowed_user_ids: str = Field(default="", alias="TELEGRAM_ALLOWED_USER_IDS")

    db_path: str = Field(default="./data/tgaibot.db", alias="DB_PATH")
    model_config_path: str = Field(default="./config/models.json", alias="MODEL_CONFIG_PATH")

    default_model_mode: str = Field(default="auto", alias="DEFAULT_MODEL_MODE")
    auto_simple_model_id: str = Field(default="openai_fast", alias="AUTO_SIMPLE_MODEL_ID")
    auto_reasoning_model_id: str = Field(default="openai_strong", alias="AUTO_REASONING_MODEL_ID")
    auto_long_model_id: str = Field(default="openai_long", alias="AUTO_LONG_MODEL_ID")
    auto_tool_model_id: str = Field(default="openai_tool", alias="AUTO_TOOL_MODEL_ID")
    reasoning_mode: str = Field(default="disabled", alias="REASONING_MODE")
    stream_edit_interval_seconds: float = Field(default=0.8, alias="STREAM_EDIT_INTERVAL_SECONDS")
    max_context_tokens: int = Field(default=32000, alias="MAX_CONTEXT_TOKENS")
    compression_trigger_tokens: int = Field(default=24000, alias="COMPRESSION_TRIGGER_TOKENS")
    recent_window_size: int = Field(default=10, alias="RECENT_WINDOW_SIZE")
    retrieval_top_k: int = Field(default=6, alias="RETRIEVAL_TOP_K")
    embedding_chunk_size: int = Field(default=800, alias="EMBEDDING_CHUNK_SIZE")
    embedding_enable: bool = Field(default=True, alias="EMBEDDING_ENABLE")

    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_api_keys: str = Field(default="", alias="OPENAI_API_KEYS")
    openai_base_url: str = Field(default="https://api.openai.com/v1", alias="OPENAI_BASE_URL")
    openai_model: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL")
    provider_timeout_seconds: float = Field(default=60.0, alias="PROVIDER_TIMEOUT_SECONDS")
    search_proxy: str = Field(default="", alias="SEARCH_PROXY")
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    google_api_key: str = Field(default="", alias="GOOGLE_API_KEY")

    fastapi_host: str = Field(default="127.0.0.1", alias="FASTAPI_HOST")
    fastapi_port: int = Field(default=8011, alias="FASTAPI_PORT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @staticmethod
    def _parse_int_set(raw: str) -> set[int]:
        if not raw.strip():
            return set()
        parts = [part.strip() for part in raw.split(",") if part.strip()]
        values: set[int] = set()
        for part in parts:
            try:
                values.add(int(part))
            except ValueError:
                continue
        return values

    @property
    def allowed_chat_ids(self) -> set[int]:
        return self._parse_int_set(self.telegram_allowed_chat_ids)

    @property
    def allowed_user_ids(self) -> set[int]:
        return self._parse_int_set(self.telegram_allowed_user_ids)

    @property
    def openai_key_pool(self) -> list[str]:
        keys: list[str] = []
        if self.openai_api_key.strip():
            keys.append(self.openai_api_key.strip())
        if self.openai_api_keys.strip():
            for part in self.openai_api_keys.split(","):
                candidate = part.strip()
                if candidate:
                    keys.append(candidate)
        # Keep order while removing duplicates.
        seen: set[str] = set()
        unique: list[str] = []
        for key in keys:
            if key in seen:
                continue
            seen.add(key)
            unique.append(key)
        return unique

    @property
    def model_catalog(self) -> list[ModelProfile]:
        models = load_model_catalog(self.model_config_path)
        if models:
            return models
        return [
            ModelProfile(
                id="openai_default",
                label="OpenAI Default",
                provider="openai_compatible",
                model_name=self.openai_model,
                base_url=self.openai_base_url,
                api_key_env="OPENAI_API_KEY",
                tags=("simple", "reasoning", "long", "tool"),
            )
        ]


settings = Settings()
