"""Entry point: ``python run.py``.

Reads host/port from config.json and starts uvicorn. Keep the app running in a
terminal (or let Electron spawn it — see electron/config.json).
"""
from __future__ import annotations

import uvicorn

from lumia.config import Config


def main() -> None:
    cfg = Config()
    host = cfg.get("server", "host", default="127.0.0.1")
    port = int(cfg.get("server", "port", default=8787))
    # import string enables uvicorn's lifespan/reload machinery
    uvicorn.run("lumia.app:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
