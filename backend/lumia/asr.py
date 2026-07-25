"""ASR backends: iFlytek (讯飞) WebIAT, optional local Whisper, stub fallback."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import ssl
from datetime import datetime
from email.utils import formatdate
from pathlib import Path
from time import mktime
from urllib.parse import urlencode


async def asr_stub(path: Path) -> str:
    from datetime import datetime as dt

    return (
        f"（本地演示转写）灵感片段 {dt.now().strftime('%H:%M')} "
        f"— 请配置 LUMIA_XFYUN_* 或安装 faster-whisper / openai-whisper"
    )


async def asr_local(path: Path, model_size: str = "base") -> str | None:
    """端侧：优先 faster-whisper，其次 openai-whisper。不可用则返回 None。"""

    def _run() -> str | None:
        try:
            from faster_whisper import WhisperModel  # type: ignore

            model = WhisperModel(model_size, device="cpu", compute_type="int8")
            segments, _info = model.transcribe(str(path), language="zh")
            text = "".join(s.text for s in segments).strip()
            return text or None
        except Exception:
            pass
        try:
            import whisper  # type: ignore

            model = whisper.load_model(model_size)
            result = model.transcribe(str(path), language="zh")
            text = str(result.get("text") or "").strip()
            return text or None
        except Exception:
            return None

    return await asyncio.to_thread(_run)


def _xfyun_auth_url(api_key: str, api_secret: str) -> str:
    host = "iat-api.xfyun.cn"
    path = "/v2/iat"
    now = datetime.now()
    date = formatdate(timeval=mktime(now.timetuple()), localtime=False, usegmt=True)
    signature_origin = f"host: {host}\ndate: {date}\nGET {path} HTTP/1.1"
    digest = hmac.new(
        api_secret.encode("utf-8"),
        signature_origin.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    signature = base64.b64encode(digest).decode("utf-8")
    authorization_origin = (
        f'api_key="{api_key}", algorithm="hmac-sha256", '
        f'headers="host date request-line", signature="{signature}"'
    )
    authorization = base64.b64encode(authorization_origin.encode("utf-8")).decode("utf-8")
    query = urlencode({"authorization": authorization, "date": date, "host": host})
    return f"wss://{host}{path}?{query}"


def _extract_pcm(path: Path) -> bytes:
    raw = path.read_bytes()
    if len(raw) >= 44 and raw[:4] == b"RIFF" and raw[8:12] == b"WAVE":
        # 跳过标准 44 字节头；非标准头则尽量找 data chunk
        if raw[36:40] == b"data":
            return raw[44:]
        idx = raw.find(b"data")
        if idx > 0 and idx + 8 <= len(raw):
            return raw[idx + 8 :]
        return raw[44:]
    return raw


def _parse_iat_result(data: dict, texts: list[str]) -> bool:
    """Append words; return True if final (status==2)."""
    if data.get("code", -1) != 0:
        return True
    result = (data.get("data") or {}).get("result")
    if result:
        for ws_item in result.get("ws", []) or []:
            for cw in ws_item.get("cw", []) or []:
                w = cw.get("w")
                if w:
                    texts.append(str(w))
    return (data.get("data") or {}).get("status") == 2


async def asr_xfyun(
    path: Path,
    *,
    app_id: str,
    api_key: str,
    api_secret: str,
) -> str | None:
    """讯飞短语音听写（≤60s），16 kHz PCM / WAV。"""
    try:
        import websockets
    except ImportError:
        return None

    pcm = _extract_pcm(path)
    if len(pcm) < 3200:
        return None

    url = _xfyun_auth_url(api_key, api_secret)
    ssl_ctx = ssl.create_default_context()
    texts: list[str] = []
    frame_size = 1280  # 40ms @ 16kHz 16bit mono

    try:
        async with websockets.connect(url, ssl=ssl_ctx, max_size=8 * 1024 * 1024) as ws:
            offset = 0
            first = True
            # 必须把整段 PCM 发完；中途收到 status=2（VAD）就提前 return
            # 会导致「说完了只剩一半」——常见于句中短暂停顿。
            while True:
                chunk = pcm[offset : offset + frame_size]
                offset += len(chunk)
                last = offset >= len(pcm)
                if first:
                    status = 0
                    first = False
                elif last:
                    status = 2
                else:
                    status = 1
                if last and status == 0:
                    status = 2

                payload = {
                    "common": {"app_id": app_id},
                    "business": {
                        "language": "zh_cn",
                        "domain": "iat",
                        "accent": "mandarin",
                        "vinfo": 1,
                        "vad_eos": 10000,
                        "ptt": 0,
                    },
                    "data": {
                        "status": status,
                        "format": "audio/L16;rate=16000",
                        "encoding": "raw",
                        "audio": base64.b64encode(chunk).decode("utf-8") if chunk else "",
                    },
                }
                await ws.send(json.dumps(payload))

                # 发送过程中只吞结果、不提前结束会话
                try:
                    while True:
                        msg = await asyncio.wait_for(ws.recv(), timeout=0.05)
                        data = json.loads(msg)
                        if data.get("code", -1) != 0:
                            print(f"[asr_xfyun] api error: {data}")
                            return None
                        _parse_iat_result(data, texts)
                except asyncio.TimeoutError:
                    pass

                if last:
                    try:
                        while True:
                            msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                            data = json.loads(msg)
                            if data.get("code", -1) != 0:
                                print(f"[asr_xfyun] api error: {data}")
                                break
                            if _parse_iat_result(data, texts):
                                break
                    except asyncio.TimeoutError:
                        print("[asr_xfyun] timeout waiting final status")
                    break
    except Exception as e:
        print(f"[asr_xfyun] error: {e}")
        return None

    text = "".join(texts).strip()
    print(f"[asr_xfyun] pcm={len(pcm)}B text={text!r}")
    return text or None


async def transcribe(
    path: Path,
    *,
    mode: str,
    xfyun_app_id: str,
    xfyun_api_key: str,
    xfyun_api_secret: str,
    local_model: str = "base",
) -> str:
    """
    mode:
      auto   — 有讯飞钥先讯飞，否则端侧，再 stub
      xfyun  — 仅讯飞
      local  — 仅端侧 Whisper
      stub   — 演示占位
    """
    mode = (mode or "auto").lower()

    async def try_xfyun() -> str | None:
        if not (xfyun_app_id and xfyun_api_key and xfyun_api_secret):
            return None
        return await asr_xfyun(
            path,
            app_id=xfyun_app_id,
            api_key=xfyun_api_key,
            api_secret=xfyun_api_secret,
        )

    if mode == "stub":
        return await asr_stub(path)
    if mode == "xfyun":
        return (await try_xfyun()) or await asr_stub(path)
    if mode == "local":
        return (await asr_local(path, local_model)) or await asr_stub(path)

    text = await try_xfyun()
    if text:
        return text
    text = await asr_local(path, local_model)
    if text:
        return text
    return await asr_stub(path)
