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
    row = db.append(
        "ideas",
        {"ts": _now(), "kind": "text", "text": text, "audio_path": None,
         "source": source, "uploaded": 1},
    )
    return {"id": row["id"], "kind": "text", "text": text, "source": source}


def add_audio(
    db: Database, content: bytes, filename: str, source: str = "t5ai"
) -> dict[str, Any]:
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    suffix = Path(filename).suffix or ".wav"
    stored = AUDIO_DIR / f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}{suffix}"
    stored.write_bytes(content)
    row = db.append(
        "ideas",
        {"ts": _now(), "kind": "audio", "text": None, "audio_path": str(stored),
         "source": source, "uploaded": 1},
    )
    return {
        "id": row["id"],
        "kind": "audio",
        "audio_path": str(stored),
        "bytes": len(content),
        "source": source,
    }


def list_ideas(db: Database, limit: int = 100) -> list[dict[str, Any]]:
    return db.list_rows("ideas", limit)
