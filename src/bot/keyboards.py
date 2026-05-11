from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.config import settings


def model_selection_keyboard() -> InlineKeyboardMarkup:
    from src.bot.runtime import failed_ping_models  # local import to avoid circular dep

    rows: list[list[InlineKeyboardButton]] = [[InlineKeyboardButton(text="自动路由", callback_data="model:auto")]]
    for model in settings.model_catalog:
        if "tts" in model.tags:
            continue
        label = f"{model.label} (不可用)" if model.id in failed_ping_models else model.label
        rows.append([InlineKeyboardButton(text=label, callback_data=f"model:{model.id}")])
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
