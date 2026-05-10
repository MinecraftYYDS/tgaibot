from __future__ import annotations

import logging

from aiogram import Router
from aiogram.types import CallbackQuery

from src.bot.keyboards import control_keyboard

logger = logging.getLogger(__name__)
router = Router(name="callbacks")


@router.callback_query(lambda c: c.data and c.data.startswith("model:"))
async def on_model_switch(callback: CallbackQuery) -> None:
    selected = callback.data.split(":", maxsplit=1)[1]
    await callback.answer(f"Model switched to {selected}")
    if callback.message:
        await callback.message.answer(f"Model set: {selected}", reply_markup=control_keyboard())


@router.callback_query(lambda c: c.data and c.data == "control:stop")
async def on_stop(callback: CallbackQuery) -> None:
    await callback.answer("Generation stop requested")


@router.callback_query(lambda c: c.data and c.data == "control:summarize")
async def on_summarize(callback: CallbackQuery) -> None:
    await callback.answer("Summary job queued")


@router.callback_query(lambda c: c.data and c.data == "control:clear")
async def on_clear(callback: CallbackQuery) -> None:
    await callback.answer("Context clear requested")
