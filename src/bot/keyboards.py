from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def model_selection_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Auto", callback_data="model:auto")],
            [InlineKeyboardButton(text="small-fast", callback_data="model:small-fast")],
            [InlineKeyboardButton(text="strong-reasoning", callback_data="model:strong-reasoning")],
            [InlineKeyboardButton(text="long-context", callback_data="model:long-context")],
            [InlineKeyboardButton(text="tool-use", callback_data="model:tool-use")],
        ]
    )


def control_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Stop", callback_data="control:stop")],
            [InlineKeyboardButton(text="Summarize", callback_data="control:summarize")],
            [InlineKeyboardButton(text="Clear", callback_data="control:clear")],
        ]
    )
