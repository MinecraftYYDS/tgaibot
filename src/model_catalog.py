from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ModelProfile:
    id: str
    label: str
    provider: str
    model_name: str
    base_url: str
    api_key_env: str
    tags: tuple[str, ...]


def _to_tags(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return tuple()
    tags: list[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            tags.append(item.strip())
    return tuple(tags)


def load_model_catalog(config_path: str) -> list[ModelProfile]:
    path = Path(config_path)
    if not path.exists():
        return [
            ModelProfile(
                id="openai_default",
                label="OpenAI Default",
                provider="openai_compatible",
                model_name="gpt-4o-mini",
                base_url="https://api.openai.com/v1",
                api_key_env="OPENAI_API_KEY",
                tags=("simple", "reasoning", "long", "tool"),
            )
        ]

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return []
    models_raw = data.get("models")
    if not isinstance(models_raw, list):
        return []

    models: list[ModelProfile] = []
    for item in models_raw:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id", "")).strip()
        if not model_id:
            continue
        profile = ModelProfile(
            id=model_id,
            label=str(item.get("label", model_id)).strip() or model_id,
            provider=str(item.get("provider", "openai_compatible")).strip() or "openai_compatible",
            model_name=str(item.get("model_name", "")).strip() or model_id,
            base_url=str(item.get("base_url", "https://api.openai.com/v1")).strip() or "https://api.openai.com/v1",
            api_key_env=str(item.get("api_key_env", "OPENAI_API_KEY")).strip() or "OPENAI_API_KEY",
            tags=_to_tags(item.get("tags")),
        )
        models.append(profile)
    return models


def resolve_model(models: list[ModelProfile], model_id: str) -> ModelProfile | None:
    for model in models:
        if model.id == model_id:
            return model
    return None
