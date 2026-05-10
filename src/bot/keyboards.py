from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.config import settings


def model_selection_keyboard() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [[InlineKeyboardButton(text="自动路由", callback_data="model:auto")]]
    for model in settings.model_catalog:
        if "tts" in model.tags:
            continue
        rows.append([InlineKeyboardButton(text=model.label, callback_data=f"model:{model.id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def control_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="停止生成", callback_data="control:stop")],
            [InlineKeyboardButton(text="对话总结", callback_data="control:summarize")],
        ]
    )
