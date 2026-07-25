from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

import os
from pathlib import Path

from fastapi import FastAPI, File, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import activity, asr, chair, gitstats, ideas
from .coding import CodingTracker
from .config import Config
from .db import Database
from .events import EventBus
from .reminders import Reminders


def _load_dotenv() -> None:
    roots = [
        Path(__file__).resolve().parent.parent,  # backend/
        Path(__file__).resolve().parent.parent.parent,  # repo root
    ]
    for root in roots:
        env_path = root / ".env"
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))
        break


def _apply_env_overrides(cfg: Config) -> None:
    patch: dict = {}
    asr_cfg = dict(cfg.get("asr", default={}) or {})
    changed = False
    for env_key, cfg_key in (
        ("LUMIA_XFYUN_APP_ID", "xfyun_app_id"),
        ("LUMIA_XFYUN_API_KEY", "xfyun_api_key"),
        ("LUMIA_XFYUN_API_SECRET", "xfyun_api_secret"),
        ("LUMIA_ASR_MODE", "mode"),
    ):
        if os.getenv(env_key):
            asr_cfg[cfg_key] = os.environ[env_key]
            changed = True
    if changed:
        patch["asr"] = asr_cfg
    chair_cfg = dict(cfg.get("chair", default={}) or {})
    chair_changed = False
    if os.getenv("LUMIA_CHAIR_MODE"):
        chair_cfg["mode"] = os.environ["LUMIA_CHAIR_MODE"]
        chair_changed = True
    if os.getenv("LUMIA_CHAIR_RELAY_URL"):
        chair_cfg["relay_url"] = os.environ["LUMIA_CHAIR_RELAY_URL"]
        chair_changed = True
    if chair_changed:
        patch["chair"] = chair_cfg
    if patch:
        cfg.merge_runtime(patch)


_load_dotenv()
config = Config()
_apply_env_overrides(config)
db = Database()
bus = EventBus()
tracker = CodingTracker(config, db, bus)
reminders = Reminders(config, db, bus)


class TextIdea(BaseModel):
    text: str
    source: str = "manual"


class SitReport(BaseModel):
    seated: bool
    pressure: float | None = None


class ChairRequest(BaseModel):
    source: str = "manual"


async def _coding_loop() -> None:
    while True:
        try:
            coding_now = activity.is_coding(
                config.get("coding", "dev_processes", default=[]),
                float(config.get("coding", "idle_threshold_seconds", default=60)),
            )
            tracker.tick(coding_now, dt=1)
        except Exception:
            pass
        await asyncio.sleep(1)


async def _reminder_loop() -> None:
    while True:
        try:
            reminders.tick()
        except Exception:
            pass
        await asyncio.sleep(30)


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = [asyncio.create_task(_coding_loop()), asyncio.create_task(_reminder_loop())]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()


app = FastAPI(title="Lumia Local Brain", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, Any]:
    # 灵感盒/椅控探活会匹配 "ok"
    return {"ok": True, "name": "Lumia", "service": "lumia", "version": app.version}


@app.get("/api/state")
def state() -> dict[str, Any]:
    return {
        "coding": tracker.snapshot(),
        "sit": reminders.sit_snapshot(),
        "latest_event_id": bus.latest_id(),
    }


@app.post("/api/coding/delay")
def coding_delay() -> dict[str, Any]:
    return tracker.request_delay()


@app.post("/api/dev/reset")
def dev_reset() -> dict[str, Any]:
    return tracker.reset_today()


@app.post("/api/dev/reset-history")
def dev_reset_history() -> dict[str, Any]:
    return tracker.reset_history()


@app.get("/api/events/poll")
def events_poll(after: int = Query(0, ge=0)) -> dict[str, Any]:
    evts = bus.poll(after)
    return {"events": evts, "latest_id": bus.latest_id()}


@app.post("/api/capture/text")
def capture_text(body: TextIdea) -> dict[str, Any]:
    return ideas.add_text(db, body.text, source=body.source)


@app.post("/api/capture/audio")
async def capture_audio(file: UploadFile = File(...)) -> dict[str, Any]:
    """T5AI 灵感盒：存 WAV → 讯飞 ASR → 写入灵感文本。"""
    content = await file.read()
    result = ideas.add_audio(db, content, file.filename or "capture.wav", source="t5ai")
    path = Path(result["audio_path"])
    asr_cfg = config.get("asr", default={}) or {}
    text = await asr.transcribe(
        path,
        mode=str(asr_cfg.get("mode") or "xfyun"),
        xfyun_app_id=str(asr_cfg.get("xfyun_app_id") or ""),
        xfyun_api_key=str(asr_cfg.get("xfyun_api_key") or ""),
        xfyun_api_secret=str(asr_cfg.get("xfyun_api_secret") or ""),
        local_model=str(asr_cfg.get("local_model") or "base"),
    )
    db.execute(
        "UPDATE ideas SET text = ? WHERE id = ?",
        (text, result["id"]),
    )
    try:
        path.with_suffix(".txt").write_text(
            f"file: {path.name}\nbytes: {len(content)}\nasr:\n{text}\n",
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"[capture/audio] meta write failed: {exc}")
    print(f"[capture/audio] saved {path} ({len(content)} bytes) asr={text!r}")
    result["text"] = text
    return result


@app.get("/api/ideas")
def get_ideas(limit: int = Query(100, ge=1, le=1000)) -> dict[str, Any]:
    return {"ideas": ideas.list_ideas(db, limit)}


@app.post("/api/sit")
def report_sit(body: SitReport) -> dict[str, Any]:
    return reminders.update_sit(body.seated, body.pressure)


@app.post("/api/chair/stretch")
def chair_stretch(body: ChairRequest) -> dict[str, Any]:
    return chair.stretch(config, source=body.source)


@app.get("/api/achievements/today")
def achievements_today() -> dict[str, Any]:
    return gitstats.achievements(config)


@app.get("/api/config")
def get_config() -> dict[str, Any]:
    return config.all()


@app.put("/api/config")
def put_config(patch: dict[str, Any]) -> dict[str, Any]:
    return config.update(patch)
