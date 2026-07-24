"""椅子继电器联动: 久坐提醒时让椅子进入拉伸模式. simulate 只记日志; relay 通过 ESP32 的 /stretch 发脉冲."""
from __future__ import annotations

import urllib.request
from typing import Any

from .config import Config


def stretch(config: Config, source: str = "sit_nudge") -> dict[str, Any]:
    mode = config.get("chair", "mode", default="simulate")
    if mode == "relay":
        url = config.get("chair", "relay_url", default="")
        if not url:
            return {"ok": False, "mode": mode, "error": "relay_url not configured"}
        try:
            req = urllib.request.Request(url, method="POST")
            with urllib.request.urlopen(req, timeout=3) as resp:
                body = resp.read().decode("utf-8", "replace")
            return {"ok": True, "mode": mode, "source": source, "relay_response": body}
        except Exception as exc:  # 网络/设备错误不致命
            return {"ok": False, "mode": mode, "source": source, "error": str(exc)}
    return {"ok": True, "mode": "simulate", "source": source, "action": "stretch_pulse"}
