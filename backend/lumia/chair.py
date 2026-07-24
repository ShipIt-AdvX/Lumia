"""Chair relay integration.

Mirrors HARDWARE_PROTOCOL.md: on a sedentary nudge the PC asks the ergonomic
chair to enter "stretch" mode. ``simulate`` mode only logs; ``relay`` mode
fires a short pulse via the ESP32 relay's ``/stretch`` endpoint.
"""
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
        except Exception as exc:  # network / device errors are non-fatal
            return {"ok": False, "mode": mode, "source": source, "error": str(exc)}
    # simulate
    return {"ok": True, "mode": "simulate", "source": source, "action": "stretch_pulse"}
