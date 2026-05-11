from __future__ import annotations

import logging
import sys


def setup_logging(level: str = "INFO") -> None:
    normalized_level = level.upper().strip() or "INFO"
    logging.basicConfig(
        level=getattr(logging, normalized_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )
    logging.captureWarnings(True)
    logging.getLogger("asyncio").setLevel(getattr(logging, normalized_level, logging.INFO))
    logging.getLogger("uvicorn").setLevel(getattr(logging, normalized_level, logging.INFO))
    logging.getLogger("uvicorn.error").setLevel(getattr(logging, normalized_level, logging.INFO))
    logging.getLogger("uvicorn.access").setLevel(getattr(logging, normalized_level, logging.INFO))
