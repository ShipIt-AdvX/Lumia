"""DeepSeek inspiration deepen (+ offline heuristic). Ported from legacy Lumia."""

from __future__ import annotations

import json
from typing import Any


async def deepen(raw: str, *, api_key: str = "", base_url: str = "") -> dict[str, str]:
    """整理口述/转写 → {list_type, title, body}。无 Key 时用本地启发式。"""
    raw = (raw or "").strip()
    base = (base_url or "https://api.deepseek.com").rstrip("/")
    if api_key:
        try:
            import httpx

            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(
                    f"{base}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "model": "deepseek-chat",
                        "messages": [
                            {
                                "role": "system",
                                "content": (
                                    "你是开发者灵感整理助手。把用户口述整理成 JSON："
                                    '{"list_type":"todo"|"idea","title":"短标题","body":"澄清与下一步"}。'
                                    "只输出 JSON。"
                                ),
                            },
                            {"role": "user", "content": raw},
                        ],
                        "temperature": 0.4,
                    },
                )
                r.raise_for_status()
                content = r.json()["choices"][0]["message"]["content"]
                start = content.find("{")
                end = content.rfind("}") + 1
                data: dict[str, Any] = json.loads(content[start:end])
                return {
                    "list_type": str(data.get("list_type") or "idea"),
                    "title": str(data.get("title") or raw[:24] or "未命名灵感"),
                    "body": str(data.get("body") or raw),
                }
        except Exception as e:
            return {
                "list_type": "idea",
                "title": raw[:24] or "未命名灵感",
                "body": f"AI 调用失败，保留原文。({e})",
            }

    is_todo = any(k in raw for k in ("修", "bug", "待办", "实现", "写", "fix", "做"))
    return {
        "list_type": "todo" if is_todo else "idea",
        "title": (raw[:28] + "…") if len(raw) > 28 else (raw or "空白灵感"),
        "body": f"本地启发式整理：\n原文：{raw}\n建议：明早确认是否开工，今晚不改当前 Focus。",
    }
