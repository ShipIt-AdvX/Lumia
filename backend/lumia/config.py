"""配置读写: 运行期配置在 backend/config.json, 首次运行从 config.example.json 复制. 全局共用一个 Config 实例."""
from __future__ import annotations

import json
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config.json"
EXAMPLE_PATH = BASE_DIR / "config.example.json"


class Config:
    """线程安全、以 JSON 为底的配置."""

    def __init__(self, path: Path = CONFIG_PATH) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._data: dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        with self._lock:
            if not self._path.exists():
                # 首次运行时用示例文件初始化
                seed = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
                self._path.write_text(
                    json.dumps(seed, indent=2, ensure_ascii=False), encoding="utf-8"
                )
            self._data = json.loads(self._path.read_text(encoding="utf-8"))

    def save(self) -> None:
        with self._lock:
            self._path.write_text(
                json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8"
            )

    def all(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._data)

    def get(self, *keys: str, default: Any = None) -> Any:
        with self._lock:
            node: Any = self._data
            for key in keys:
                if not isinstance(node, dict) or key not in node:
                    return default
                node = node[key]
            return deepcopy(node)

    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        """深合并 patch 到配置并落盘."""
        with self._lock:
            _deep_merge(self._data, patch)
            self.save()
            return deepcopy(self._data)


def _deep_merge(dst: dict[str, Any], src: dict[str, Any]) -> None:
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            _deep_merge(dst[key], value)
        else:
            dst[key] = value
