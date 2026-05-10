from __future__ import annotations

import asyncio
import logging

import uvicorn
from aiogram import Bot, Dispatcher
from fastapi import FastAPI

from src.api.routes import router as api_router
from src.bot.callbacks import router as callbacks_router
from src.bot.handlers import router as handlers_router
from src.config import settings
from src.jobs.worker import run_job_worker
from src.logging_setup import setup_logging
from src.persistence.migrations import run_migrations

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(title="tgaibot", version="0.1.0")
    app.include_router(api_router)
    return app


async def run_bot() -> None:
    bot = Bot(token=settings.telegram_bot_token)
    from src.bot import runtime
    runtime.bot = bot
    dp = Dispatcher()
    dp.include_router(callbacks_router)
    dp.include_router(handlers_router)
    await asyncio.gather(dp.start_polling(bot), run_job_worker(bot))


async def run_api() -> None:
    app = create_app()
    config = uvicorn.Config(app=app, host=settings.fastapi_host, port=settings.fastapi_port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()


async def async_main() -> None:
    setup_logging()
    run_migrations()
    logger.info("Starting tgaibot with sqlite=%s", settings.db_path)
    await asyncio.gather(run_bot(), run_api())


if __name__ == "__main__":
    asyncio.run(async_main())
