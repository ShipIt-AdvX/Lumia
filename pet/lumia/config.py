"""配置持久化：JSON 读写，位于用户配置目录。

- Linux:   ~/.config/lumia-pet/config.json
- Windows: %APPDATA%/lumia-pet/config.json
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

from . import APP_NAME

log = logging.getLogger("lumia.config")

DEFAULTS = {
    "walking_enabled": True,
    "autostart": False,
    "clean_mode": True,  # 纯净模式：全屏幕布隐藏桌面/面板，只保留桌宠
}


def config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / APP_NAME


class Config:
    def __init__(self):
        self.path = config_dir() / "config.json"
        self.data = dict(DEFAULTS)
        self.load()

    def load(self) -> None:
        try:
            if self.path.exists():
                stored = json.loads(self.path.read_text(encoding="utf-8"))
                self.data.update({k: v for k, v in stored.items() if k in DEFAULTS})
                log.info("配置已加载: %s", self.data)
            else:
                log.info("配置文件不存在，使用默认值: %s", self.data)
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("配置读取失败，使用默认值: %s", exc)

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            log.debug("配置已保存: %s", self.data)
        except OSError as exc:
            log.error("配置保存失败: %s", exc)

    def get(self, key: str):
        return self.data.get(key, DEFAULTS.get(key))

    def set(self, key: str, value) -> None:
        self.data[key] = value
        self.save()
