from __future__ import annotations

import argparse
import json
import urllib.request

BASE = "http://127.0.0.1:8787"


def _post(path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload or {}).encode() if payload is not None else b"{}"
    req = urllib.request.Request(
        BASE + path, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode())


def _get(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=5) as resp:
        return json.loads(resp.read().decode())


def main() -> None:
    ap = argparse.ArgumentParser(description="Lumia hardware mock")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_idea = sub.add_parser("idea", help="send a text idea")
    p_idea.add_argument("text")

    p_sit = sub.add_parser("sit", help="report seated state")
    p_sit.add_argument("--seated", dest="seated", action="store_true", default=True)
    p_sit.add_argument("--no-seated", dest="seated", action="store_false")
    p_sit.add_argument("--pressure", type=float, default=0.6)

    sub.add_parser("stretch", help="trigger chair stretch")
    sub.add_parser("delay", help="request a coding delay")
    sub.add_parser("state", help="print current state")

    args = ap.parse_args()
    if args.cmd == "idea":
        print(json.dumps(_post("/api/capture/text", {"text": args.text, "source": "mock"}), ensure_ascii=False, indent=2))
    elif args.cmd == "sit":
        print(json.dumps(_post("/api/sit", {"seated": args.seated, "pressure": args.pressure}), ensure_ascii=False, indent=2))
    elif args.cmd == "stretch":
        print(json.dumps(_post("/api/chair/stretch", {"source": "mock"}), ensure_ascii=False, indent=2))
    elif args.cmd == "delay":
        print(json.dumps(_post("/api/coding/delay"), ensure_ascii=False, indent=2))
    elif args.cmd == "state":
        print(json.dumps(_get("/api/state"), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
