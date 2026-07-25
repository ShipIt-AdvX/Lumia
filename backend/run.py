from __future__ import annotations

import uvicorn

from lumia.config import Config


def main() -> None:
    # app 模块加载时已读 .env；这里只取监听地址
    from lumia import app as appmod

    host = appmod.config.get("server", "host", default="0.0.0.0")
    port = int(appmod.config.get("server", "port", default=8787))
    uvicorn.run("lumia.app:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
