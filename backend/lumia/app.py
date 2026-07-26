from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

import os
from pathlib import Path

from fastapi import FastAPI, File, Header, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import activity, asr, chair, gitstats, ideas
from .capture_stream import IDLE_FINALIZE_SEC, hub as capture_hub
from .coding import CodingTracker
from .config import Config
from .db import Database
from .events import EventBus
from .pet_director import PetDirector
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
    deepseek_cfg = dict(cfg.get("deepseek", default={}) or {})
    ds_changed = False
    if os.getenv("LUMIA_DEEPSEEK_API_KEY"):
        deepseek_cfg["api_key"] = os.environ["LUMIA_DEEPSEEK_API_KEY"]
        ds_changed = True
    if os.getenv("LUMIA_DEEPSEEK_BASE"):
        deepseek_cfg["base_url"] = os.environ["LUMIA_DEEPSEEK_BASE"]
        ds_changed = True
    if ds_changed:
        patch["deepseek"] = deepseek_cfg
    if patch:
        cfg.merge_runtime(patch)


_load_dotenv()
config = Config()
_apply_env_overrides(config)
db = Database()
bus = EventBus()
tracker = CodingTracker(config, db, bus)
reminders = Reminders(config, db, bus)
pet_director = PetDirector(config, reminders)
_last_pet_action: str | None = None


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
    global _last_pet_action
    while True:
        try:
            reminders.tick()
            snap = pet_director.snapshot()
            action = str(snap.get("action") or "idle")
            if action != _last_pet_action:
                _last_pet_action = action
                if action != "idle":
                    bus.emit(
                        f"pet_{action}",
                        "桌宠",
                        str(snap.get("bubble") or action),
                        data=snap,
                    )
        except Exception:
            pass
        await asyncio.sleep(5)


async def _stream_finalize_loop() -> None:
    """板端断电/断网后无法发 end：靠空闲超时收尾拼 WAV + 讯飞 + AI 深化。"""
    while True:
        try:
            for sid in capture_hub.list_idle(IDLE_FINALIZE_SEC):
                asr_cfg = config.get("asr", default={}) or {}
                await capture_hub.finalize(
                    sid, db, asr_cfg, reason="idle", config=config, bus=bus
                )
        except Exception as exc:
            print(f"[stream] finalize loop error: {exc}")
        await asyncio.sleep(2)


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = [
        asyncio.create_task(_coding_loop()),
        asyncio.create_task(_reminder_loop()),
        asyncio.create_task(_stream_finalize_loop()),
    ]
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
        "pet": pet_director.snapshot(),
        "latest_event_id": bus.latest_id(),
    }


@app.get("/api/pet/state")
def pet_state() -> dict[str, Any]:
    """地瓜派 / 电脑侧拉取猫导演状态（睡觉、吃饭、久坐走远、抓鼠标等）。"""
    return pet_director.snapshot()


class PetDebugIn(BaseModel):
    action: str = "auto"  # auto|idle|sleep|meal|sit_away
    minutes: float = 10
    bubble: str | None = None
    scale: float | None = None


@app.post("/api/pet/debug")
def pet_debug(body: PetDebugIn) -> dict[str, Any]:
    """调试：强制猫行为一段时间；action=auto 恢复自动。"""
    result = pet_director.set_debug(
        body.action,
        minutes=body.minutes,
        bubble=body.bubble,
        scale=body.scale,
    )
    if result.get("ok"):
        snap = result.get("state") or {}
        bus.emit(
            f"pet_{snap.get('action') or 'idle'}",
            "桌宠调试",
            str(snap.get("bubble") or body.action),
            data=snap,
        )
    return result


@app.get("/api/pet/debug")
def pet_debug_get() -> dict[str, Any]:
    return pet_director.debug_info()


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
async def capture_text(body: TextIdea) -> dict[str, Any]:
    """文本灵感（桌面兜底 / mock）→ AI 深化 → 事件总线。"""
    return await ideas.ingest_text(
        db, body.text, source=body.source, config=config, bus=bus
    )


@app.post("/api/capture/audio")
async def capture_audio(file: UploadFile = File(...)) -> dict[str, Any]:
    """兼容旧板：整段 WAV 上传 → 讯飞 ASR → AI 深化。"""
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
    try:
        path.with_suffix(".txt").write_text(
            f"file: {path.name}\nbytes: {len(content)}\nasr:\n{text}\n",
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"[capture/audio] meta write failed: {exc}")
    print(f"[capture/audio] saved {path} ({len(content)} bytes) asr={text!r}")
    deepened = await ideas.finalize_audio_text(
        db, int(result["id"]), text, config=config, bus=bus
    )
    result["text"] = text
    result["deepened"] = deepened.get("deepened")
    result["silent"] = deepened.get("silent")
    result["idea"] = deepened.get("idea")
    result["notify"] = deepened.get("notify")
    return result


@app.post("/api/capture/stream/start")
def capture_stream_start() -> dict[str, Any]:
    """灵感盒开机连上后开新会话（可选；也可直接带 session 传 chunk）。"""
    import uuid as _uuid

    sid = _uuid.uuid4().hex
    sess = capture_hub.ensure(sid)
    return {"ok": True, "session": sess.session_id}


@app.post("/api/capture/stream/chunk")
async def capture_stream_chunk(
    request: Request,
    session: str = Query(""),
    x_lumia_session: str | None = Header(default=None, alias="X-Lumia-Session"),
) -> dict[str, Any]:
    """实时 PCM 分片（16kHz/16bit/mono little-endian）。板端读完即丢，不占本地。"""
    sid = (x_lumia_session or session or "").strip()
    if not sid:
        return {"ok": False, "error": "missing_session"}
    data = await request.body()
    return capture_hub.append_chunk(sid, data)


@app.post("/api/capture/stream/end")
async def capture_stream_end(
    session: str = Query(""),
    x_lumia_session: str | None = Header(default=None, alias="X-Lumia-Session"),
) -> dict[str, Any]:
    """主动结束（有电时）；断电场景靠空闲超时。"""
    sid = (x_lumia_session or session or "").strip()
    if not sid:
        return {"ok": False, "error": "missing_session"}
    asr_cfg = config.get("asr", default={}) or {}
    result = await capture_hub.finalize(
        sid, db, asr_cfg, reason="end", config=config, bus=bus
    )
    return result or {"ok": False, "error": "unknown_session"}


@app.get("/api/ideas")
def get_ideas(limit: int = Query(100, ge=1, le=1000)) -> dict[str, Any]:
    return {"ideas": ideas.list_ideas(db, limit)}


@app.post("/api/ideas/{idea_id}/confirm")
def confirm_idea(idea_id: int) -> dict[str, Any]:
    return ideas.set_status(db, idea_id, "confirmed")


@app.post("/api/ideas/{idea_id}/discard")
def discard_idea(idea_id: int) -> dict[str, Any]:
    return ideas.set_status(db, idea_id, "discarded")


@app.post("/api/sit")
def report_sit(body: SitReport) -> dict[str, Any]:
    return reminders.update_sit(body.seated, body.pressure)


@app.post("/api/chair/stretch")
def chair_stretch(body: ChairRequest) -> dict[str, Any]:
    return chair.stretch(config, source=body.source)


@app.get("/api/achievements/today")
def achievements_today(
    offset: int = Query(0, ge=0), limit: int = Query(0, ge=0, le=100)
) -> dict[str, Any]:
    return gitstats.achievements(config, offset=offset, limit=limit)



@app.post("/api/coding/save-grace")
def coding_save_grace() -> dict[str, Any]:
    return tracker.request_save_grace()


class DevUsed(BaseModel):
    minutes: float


@app.post("/api/dev/set-used")
def dev_set_used(body: DevUsed) -> dict[str, Any]:
    return tracker.set_used_today(int(round(body.minutes * 60)))


class DeviceCode(BaseModel):
    device_code: str


class AuthStart(BaseModel):
    client_id: str = ""


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
