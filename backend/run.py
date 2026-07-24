"""入口: python run.py. 从 config.json 读 host/port 并启动 uvicorn."""
from __future__ import annotations

import uvicorn

from lumia.config import Config


def main() -> None:
    cfg = Config()
    host = cfg.get("server", "host", default="127.0.0.1")
    port = int(cfg.get("server", "port", default=8787))
    # 传字符串而非 app 对象, 才能启用 uvicorn 的 lifespan/reload
    uvicorn.run("lumia.app:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
