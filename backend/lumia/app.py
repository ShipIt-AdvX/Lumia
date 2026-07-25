from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, File, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import activity, chair, gitstats, ideas
from .coding import CodingTracker
from .config import Config
from .db import Database
from .events import EventBus
from .reminders import Reminders

config = Config()
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


class DevUsed(BaseModel):
    minutes: float


class DeviceCode(BaseModel):
    device_code: str


class AuthStart(BaseModel):
    client_id: str = ""


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
    return {"ok": True, "service": "lumia", "version": app.version}


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


@app.post("/api/coding/save-grace")
def coding_save_grace() -> dict[str, Any]:
    return tracker.request_save_grace()


@app.post("/api/dev/reset")
def dev_reset() -> dict[str, Any]:
    return tracker.reset_today()


@app.post("/api/dev/reset-history")
def dev_reset_history() -> dict[str, Any]:
    return tracker.reset_history()


@app.post("/api/dev/set-used")
def dev_set_used(body: DevUsed) -> dict[str, Any]:
    return tracker.set_used_today(int(round(body.minutes * 60)))


@app.get("/api/events/poll")
def events_poll(after: int = Query(0, ge=0)) -> dict[str, Any]:
    evts = bus.poll(after)
    return {"events": evts, "latest_id": bus.latest_id()}


@app.post("/api/capture/text")
def capture_text(body: TextIdea) -> dict[str, Any]:
    return ideas.add_text(db, body.text, source=body.source)


@app.post("/api/capture/audio")
async def capture_audio(file: UploadFile = File(...)) -> dict[str, Any]:
    content = await file.read()
    return ideas.add_audio(db, content, file.filename or "capture.wav", source="t5ai")


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


@app.post("/api/github/auth/start")
def github_auth_start(body: AuthStart) -> dict[str, Any]:
    client_id = body.client_id.strip()
    if client_id:
        config.update({"git": {"github_client_id": client_id}})
    else:
        client_id = (config.get("git", "github_client_id", default="") or "").strip()
    return gitstats.github_device_start(client_id)


@app.post("/api/github/auth/poll")
def github_auth_poll(body: DeviceCode) -> dict[str, Any]:
    return gitstats.github_device_poll(config, body.device_code)


@app.get("/api/config")
def get_config() -> dict[str, Any]:
    return config.all()


@app.put("/api/config")
def put_config(patch: dict[str, Any]) -> dict[str, Any]:
    return config.update(patch)
