"""灵感盒实时 PCM 流：板端分片上传，断电/断连空闲后拼 WAV → ASR。"""

from __future__ import annotations

import asyncio
import struct
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import asr, ideas
from .db import Database
from .events import EventBus

BASE_DIR = Path(__file__).resolve().parent.parent
STREAM_DIR = BASE_DIR / "data" / "audio" / "streams"

# 板端断电后无法发结束包；超过该空闲时间视为会话结束
IDLE_FINALIZE_SEC = 8.0
MIN_PCM_BYTES = 3200  # ~0.1s @ 16kHz/16bit/mono
SAMPLE_RATE = 16000
CHANNELS = 1
BITS = 16


def _pcm_to_wav(pcm: bytes) -> bytes:
    data_size = len(pcm)
    byte_rate = SAMPLE_RATE * CHANNELS * BITS // 8
    block_align = CHANNELS * BITS // 8
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        CHANNELS,
        SAMPLE_RATE,
        byte_rate,
        block_align,
        BITS,
        b"data",
        data_size,
    )
    return header + pcm


@dataclass
class StreamSession:
    session_id: str
    pcm_path: Path
    created_at: float
    last_chunk_at: float
    bytes_total: int = 0
    finalized: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)


class CaptureStreamHub:
    def __init__(self) -> None:
        self._sessions: dict[str, StreamSession] = {}
        self._lock = threading.Lock()

    def ensure(self, session_id: str) -> StreamSession:
        sid = (session_id or "").strip() or uuid.uuid4().hex
        with self._lock:
            sess = self._sessions.get(sid)
            if sess and not sess.finalized:
                return sess
            STREAM_DIR.mkdir(parents=True, exist_ok=True)
            path = STREAM_DIR / f"{sid}.pcm"
            if path.exists() and sess is None:
                # 复用未完成文件（进程重启）
                path.write_bytes(b"")
            now = datetime.now().timestamp()
            sess = StreamSession(
                session_id=sid,
                pcm_path=path,
                created_at=now,
                last_chunk_at=now,
            )
            if not path.exists():
                path.write_bytes(b"")
            self._sessions[sid] = sess
            return sess

    def append_chunk(self, session_id: str, data: bytes) -> dict[str, Any]:
        if not data:
            return {"ok": True, "session": session_id, "bytes": 0, "total": 0}
        sess = self.ensure(session_id)
        with sess.lock:
            if sess.finalized:
                # 旧会话已收尾，开新同名会冲突 —— 换新 id 由板端负责；这里拒绝
                return {"ok": False, "error": "session_finalized", "session": sess.session_id}
            with sess.pcm_path.open("ab") as f:
                f.write(data)
            sess.bytes_total += len(data)
            sess.last_chunk_at = datetime.now().timestamp()
        return {
            "ok": True,
            "session": sess.session_id,
            "bytes": len(data),
            "total": sess.bytes_total,
        }

    def list_idle(self, idle_sec: float = IDLE_FINALIZE_SEC) -> list[str]:
        now = datetime.now().timestamp()
        out: list[str] = []
        with self._lock:
            for sid, sess in self._sessions.items():
                if sess.finalized:
                    continue
                if now - sess.last_chunk_at >= idle_sec:
                    out.append(sid)
        return out

    async def finalize(
        self,
        session_id: str,
        db: Database,
        asr_cfg: dict[str, Any],
        *,
        reason: str = "idle",
        config: Any = None,
        bus: EventBus | None = None,
    ) -> dict[str, Any] | None:
        with self._lock:
            sess = self._sessions.get(session_id)
        if not sess:
            return None
        with sess.lock:
            if sess.finalized:
                return {"ok": True, "session": session_id, "already": True}
            sess.finalized = True
            pcm = sess.pcm_path.read_bytes() if sess.pcm_path.exists() else b""
            try:
                sess.pcm_path.unlink(missing_ok=True)
            except OSError:
                pass

        if len(pcm) < MIN_PCM_BYTES:
            print(f"[stream] drop short session={session_id} bytes={len(pcm)} reason={reason}")
            with self._lock:
                self._sessions.pop(session_id, None)
            return {"ok": True, "session": session_id, "dropped": True, "bytes": len(pcm)}

        wav = _pcm_to_wav(pcm)
        result = ideas.add_audio(db, wav, f"stream-{session_id}.wav", source="t5ai-stream")
        path = Path(result["audio_path"])
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
                f"session: {session_id}\nreason: {reason}\nbytes: {len(pcm)}\nasr:\n{text}\n",
                encoding="utf-8",
            )
        except OSError as exc:
            print(f"[stream] meta write failed: {exc}")
        print(
            f"[stream] finalized session={session_id} pcm={len(pcm)} "
            f"reason={reason} asr={text!r}"
        )
        deepened: dict[str, Any] = {}
        if config is not None:
            deepened = await ideas.finalize_audio_text(
                db, int(result["id"]), text, config=config, bus=bus
            )
        else:
            db.update_row("ideas", int(result["id"]), text=text, raw_text=text)
        with self._lock:
            self._sessions.pop(session_id, None)
        result["text"] = text
        result["session"] = session_id
        result["reason"] = reason
        result["deepened"] = deepened.get("deepened")
        result["silent"] = deepened.get("silent")
        result["idea"] = deepened.get("idea")
        return result


hub = CaptureStreamHub()
