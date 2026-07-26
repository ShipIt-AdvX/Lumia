"""灵感入库：文本 / 音频 / 流式收尾 → ASR 正文 → AI 深化 → 可选桌面通知。"""

from __future__ import annotations

import uuid
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any

from . import ai
from .db import Database
from .events import EventBus

BASE_DIR = Path(__file__).resolve().parent.parent
AUDIO_DIR = BASE_DIR / "data" / "audio"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _parse_hhmm(s: str) -> dtime:
    h, m = (s or "01:30").strip().split(":")[:2]
    return dtime(int(h), int(m))


def in_sleep_window(config: Any) -> bool:
    """睡觉时段：从 reminders.sleep_time 到 sleep_end（默认次日 07:00）。"""
    sleep_s = str(config.get("reminders", "sleep_time", default="01:30") or "01:30")
    end_s = str(config.get("reminders", "sleep_end", default="07:00") or "07:00")
    start = _parse_hhmm(sleep_s)
    end = _parse_hhmm(end_s)
    cur = datetime.now().time()
    if start <= end:
        return start <= cur < end
    return cur >= start or cur < end


def add_text(db: Database, text: str, source: str = "manual") -> dict[str, Any]:
    """同步占位写入（无 AI）；完整链路请用 ingest_text。"""
    row = db.append(
        "ideas",
        {
            "ts": _now(),
            "kind": "text",
            "text": text,
            "raw_text": text,
            "audio_path": None,
            "source": source,
            "uploaded": 1,
            "ai_title": None,
            "ai_body": None,
            "list_type": "idea",
            "status": "pending",
            "silent": 0,
        },
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
        {
            "ts": _now(),
            "kind": "audio",
            "text": None,
            "raw_text": None,
            "audio_path": str(stored),
            "source": source,
            "uploaded": 1,
            "ai_title": None,
            "ai_body": None,
            "list_type": "idea",
            "status": "pending",
            "silent": 0,
        },
    )
    return {
        "id": row["id"],
        "kind": "audio",
        "audio_path": str(stored),
        "bytes": len(content),
        "source": source,
    }


def list_ideas(db: Database, limit: int = 100, *, include_discarded: bool = False) -> list[dict[str, Any]]:
    rows = db.list_rows("ideas", limit=None)
    if not include_discarded:
        rows = [r for r in rows if (r.get("status") or "pending") != "discarded"]
    return rows[:limit]


def get_idea(db: Database, idea_id: int) -> dict[str, Any] | None:
    return db.get_row("ideas", idea_id)


def set_status(db: Database, idea_id: int, status: str) -> dict[str, Any]:
    idea = db.update_row("ideas", idea_id, status=status)
    return {"ok": idea is not None, "id": idea_id, "status": status, "idea": idea}


async def apply_deepen(
    db: Database,
    idea_id: int,
    raw: str,
    *,
    config: Any,
    bus: EventBus | None = None,
    notify: bool = True,
) -> dict[str, Any]:
    """对已有 idea 行写入 ASR/原文 + AI 深化，并可选推送桌面事件。"""
    deepseek = config.get("deepseek", default={}) or {}
    deepened = await ai.deepen(
        raw,
        api_key=str(deepseek.get("api_key") or ""),
        base_url=str(deepseek.get("base_url") or "https://api.deepseek.com"),
    )
    silent = 1 if in_sleep_window(config) else 0
    title = deepened.get("title") or (raw[:28] if raw else "灵感")
    body = deepened.get("body") or raw
    list_type = deepened.get("list_type") or "idea"
    idea = db.update_row(
        "ideas",
        idea_id,
        text=title,
        raw_text=raw,
        ai_title=title,
        ai_body=body,
        list_type=list_type,
        status="pending",
        silent=silent,
    ) or {}
    result = {
        "ok": True,
        "id": idea_id,
        "raw": raw,
        "deepened": deepened,
        "silent": bool(silent),
        "idea": idea,
        "notify": bool(notify) and not silent,
    }
    if bus is not None and result["notify"]:
        kind_label = "待办" if list_type == "todo" else "灵感"
        bus.emit(
            "idea_captured",
            f"灵感盒 · {kind_label}",
            f"{title}\n{(body or '')[:120]}",
            actions=[
                {"id": f"idea-confirm:{idea_id}", "label": "采纳"},
                {"id": f"idea-discard:{idea_id}", "label": "丢弃"},
            ],
            data={"idea_id": idea_id, "list_type": list_type, "silent": False},
        )
    elif bus is not None and silent:
        bus.emit(
            "idea_captured_silent",
            "灵感盒 · 静默入库",
            title,
            actions=[],
            data={"idea_id": idea_id, "silent": True},
        )
    return result


async def ingest_text(
    db: Database,
    text: str,
    *,
    source: str,
    config: Any,
    bus: EventBus | None = None,
) -> dict[str, Any]:
    stub = add_text(db, text.strip(), source=source)
    return await apply_deepen(
        db, int(stub["id"]), text.strip(), config=config, bus=bus, notify=True
    )


async def finalize_audio_text(
    db: Database,
    idea_id: int,
    text: str,
    *,
    config: Any,
    bus: EventBus | None = None,
) -> dict[str, Any]:
    return await apply_deepen(
        db, idea_id, (text or "").strip(), config=config, bus=bus, notify=True
    )
