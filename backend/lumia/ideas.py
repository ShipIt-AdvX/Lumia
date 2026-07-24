"""灵感捕获存储: 接收 T5AI-Core (经 Orange Pi 3B 转发) 或手动录入的灵感. 文本直接入库, 音频落盘后引用路径."""
from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from .db import Database

BASE_DIR = Path(__file__).resolve().parent.parent
AUDIO_DIR = BASE_DIR / "data" / "audio"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def add_text(db: Database, text: str, source: str = "manual") -> dict[str, Any]:
    cur = db.execute(
        "INSERT INTO ideas (ts, kind, text, source, uploaded) VALUES (?,?,?,?,1)",
        (_now(), "text", text, source),
    )
    return {"id": cur.lastrowid, "kind": "text", "text": text, "source": source}


def add_audio(
    db: Database, content: bytes, filename: str, source: str = "t5ai"
) -> dict[str, Any]:
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    suffix = Path(filename).suffix or ".wav"
    stored = AUDIO_DIR / f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}{suffix}"
    stored.write_bytes(content)
    cur = db.execute(
        "INSERT INTO ideas (ts, kind, audio_path, source, uploaded) VALUES (?,?,?,?,1)",
        (_now(), "audio", str(stored), source),
    )
    return {
        "id": cur.lastrowid,
        "kind": "audio",
        "audio_path": str(stored),
        "bytes": len(content),
        "source": source,
    }


def list_ideas(db: Database, limit: int = 100) -> list[dict[str, Any]]:
    rows = db.query(
        "SELECT * FROM ideas ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    return [dict(r) for r in rows]
