from __future__ import annotations

import uvicorn

from lumia.config import Config


def main() -> None:
    cfg = Config()
    host = cfg.get("server", "host", default="127.0.0.1")
    port = int(cfg.get("server", "port", default=8787))
    uvicorn.run("lumia.app:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
