from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.config import settings


def model_selection_keyboard() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [[InlineKeyboardButton(text="Auto", callback_data="model:auto")]]
    for model in settings.model_catalog:
        rows.append([InlineKeyboardButton(text=model.label, callback_data=f"model:{model.id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def control_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Stop", callback_data="control:stop")],
            [InlineKeyboardButton(text="Summarize", callback_data="control:summarize")],
            [InlineKeyboardButton(text="Clear", callback_data="control:clear")],
        ]
    )
