# Telegram Topics AI Bot

Python implementation of a Telegram Topics based multi-session AI bot.

## Current scope

- Project scaffold and runnable entrypoint
- SQLite persistence with topic/message core tables
- Per-topic lock for strong consistency under concurrent updates
- Basic bot commands: `/start`, `/new`, `/stop`
- Topic-level model switch persistence and audit trail
- Streaming reply updates with stop control and checkpoint storage
- OpenAI-compatible streaming provider (when API key is configured)
- Lightweight built-in tool loop protocol: `[tool:echo] text`, `[tool:time_now]`
- Multi-model catalog config with per-model `base_url` and `model_name`

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

Optional for real LLM streaming:

- `OPENAI_API_KEY`
- `OPENAI_BASE_URL` (leave empty for official endpoint)
- `OPENAI_MODEL`

Model catalog and routing:

- `MODEL_CONFIG_PATH` points to a JSON model catalog (default: `./config/models.json`)
- `AUTO_SIMPLE_MODEL_ID`, `AUTO_REASONING_MODEL_ID`, `AUTO_LONG_MODEL_ID`, `AUTO_TOOL_MODEL_ID` control auto-routing targets
- Each catalog model can define its own `base_url` and `model_name`

Example model catalog entry:

```json
{
	"id": "my_proxy_fast",
	"label": "My Proxy Fast",
	"provider": "openai_compatible",
	"model_name": "qwen-plus",
	"base_url": "https://your-proxy.example.com/v1",
	"api_key_env": "OPENAI_API_KEY",
	"tags": ["simple"]
}
```

4. Run:

```powershell
python -m src.main
```

## Notes

- SQLite DB file defaults to `./data/tgaibot.db`
- Bot and FastAPI server run in the same process
- This stage initializes the foundation for model routing, tool loop, and streaming controls
- Stop control works via `/stop` or the `Stop` inline button inside a topic
- Use `/models` to list loaded model IDs and resolved model names
