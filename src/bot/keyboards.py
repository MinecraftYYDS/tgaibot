from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.config import settings


def _styled_button(text: str, callback_data: str, style: str) -> InlineKeyboardButton:
    try:
        return InlineKeyboardButton(text=text, callback_data=callback_data, style=style)  # type: ignore[call-arg]
    except (TypeError, ValueError):
        return InlineKeyboardButton(text=text, callback_data=callback_data)


def model_selection_keyboard() -> InlineKeyboardMarkup:
    from src.bot.runtime import failed_ping_models  # local import to avoid circular dep

    rows: list[list[InlineKeyboardButton]] = [[_styled_button(text="自动路由", callback_data="model:auto", style="primary")]]
    for model in settings.model_catalog:
        if "tts" in model.tags:
            continue
        failed = model.id in failed_ping_models
        label = f"{model.label} (暂不可用)" if failed else model.label
        rows.append([_styled_button(text=label, callback_data=f"model:{model.id}", style="danger" if failed else "success")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def control_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="停止生成", callback_data="control:stop")],
            [
                InlineKeyboardButton(text="对话总结", callback_data="control:summarize"),
                InlineKeyboardButton(text="生成标题", callback_data="control:rename_topic"),
            ],
        ]
    )
