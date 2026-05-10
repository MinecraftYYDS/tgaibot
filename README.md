# Telegram Topics AI Bot

Python implementation of a Telegram Topics based multi-session AI bot.

## Current scope

- Project scaffold and runnable entrypoint
- SQLite persistence with topic/message core tables
- Per-topic lock for strong consistency under concurrent updates
- Basic bot commands: `/start`, `/new`, `/stop`
- Placeholder model routing and provider interfaces

## Quick start (Windows PowerShell)

1. Create and activate virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

2. Install dependencies:

```powershell
pip install -r requirements.txt
```

3. Configure environment:

```powershell
Copy-Item .env.example .env
```

Then set `TELEGRAM_BOT_TOKEN` in `.env`.

4. Run:

```powershell
python -m src.main
```

## Notes

- SQLite DB file defaults to `./data/tgaibot.db`
- Bot and FastAPI server run in the same process
- This stage initializes the foundation for model routing, tool loop, and streaming controls
