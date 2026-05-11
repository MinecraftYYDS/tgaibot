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
    logger.info("Starting Telegram bot polling")
    bot = Bot(token=settings.telegram_bot_token)
    from src.bot import runtime
    runtime.bot = bot
    me = await bot.get_me()
    logger.info("Telegram bot connected as @%s", me.username or "<unknown>")
    runtime.bot_username = (me.username or "").lower()
    dp = Dispatcher()
    dp.include_router(callbacks_router)
    dp.include_router(handlers_router)
    await asyncio.gather(dp.start_polling(bot), run_job_worker(bot))


async def run_api() -> None:
    logger.info("Starting FastAPI server on %s:%s", settings.fastapi_host, settings.fastapi_port)
    app = create_app()
    config = uvicorn.Config(app=app, host=settings.fastapi_host, port=settings.fastapi_port, log_level=settings.log_level.lower())
    server = uvicorn.Server(config)
    await server.serve()


async def run_component(name: str, awaitable) -> None:
    try:
        await awaitable
    except asyncio.CancelledError:
        logger.info("%s stopped", name)
        raise
    except Exception:
        logger.exception("%s crashed", name)
        raise


async def async_main() -> None:
    setup_logging(settings.log_level)
    logger.info("Logging initialized level=%s", settings.log_level.upper())
    logger.info("Database path=%s api=%s:%s", settings.db_path, settings.fastapi_host, settings.fastapi_port)
    run_migrations()
    logger.info("Migrations complete")
    await asyncio.gather(run_component("bot", run_bot()), run_component("api", run_api()))


if __name__ == "__main__":
    asyncio.run(async_main())
