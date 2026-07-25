from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import Config

_git_path: str | None = None


def _find_git() -> str | None:
    exe = shutil.which("git")
    if exe:
        return exe
    local = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        r"C:\Program Files\Git\cmd\git.exe",
        r"C:\Program Files (x86)\Git\cmd\git.exe",
    ]
    if local:
        candidates.append(str(Path(local) / "Programs" / "Git" / "cmd" / "git.exe"))
    for drive in "CDEFGH":
        candidates.append(rf"{drive}:\Git\cmd\git.exe")
    for cand in candidates:
        if Path(cand).is_file():
            return cand
    return None


def _git(repo: Path, args: list[str]) -> str:
    global _git_path
    if _git_path is None:
        _git_path = _find_git()
    if not _git_path:
        raise RuntimeError("未找到 git 命令，请安装 Git 或将其加入 PATH")
    try:
        result = subprocess.run(
            [_git_path, "-C", str(repo), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except FileNotFoundError:
        _git_path = None
        raise RuntimeError("git 执行失败：可执行文件不存在")
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git error")
    return result.stdout


def commits_today(repo: Path) -> list[dict[str, str]]:
    since = f"{date.today().isoformat()} 00:00:00"
    out = _git(
        repo,
        [
            "log",
            f"--since={since}",
            "--pretty=format:%H\x1f%h\x1f%an\x1f%ae\x1f%cI\x1f%s\x1f%b\x1e",
            "--all",
        ],
    )
    commits: list[dict[str, str]] = []
    for chunk in out.split("\x1e"):
        chunk = chunk.strip("\r\n")
        if not chunk.strip():
            continue
        parts = chunk.split("\x1f")
        if len(parts) != 7:
            continue
        full, short, author, email, when, subject, body = parts
        commits.append(
            {
                "hash": short,
                "full_hash": full,
                "author": author,
                "author_email": email,
                "time": when,
                "subject": subject,
                "body": body.strip(),
            }
        )
    return commits


def _author_identity(email: str) -> tuple[str | None, str | None]:
    email = (email or "").strip().lower()
    if not email:
        return None, None
    m = re.fullmatch(r"(?:\d+\+)?([a-z0-9-]+)@users\.noreply\.github\.com", email)
    if m:
        user = m.group(1)
        return f"https://github.com/{user}", f"https://github.com/{user}.png?size=80"
    digest = hashlib.md5(email.encode("utf-8")).hexdigest()
    return None, f"https://www.gravatar.com/avatar/{digest}?d=identicon&s=80"


def _remote_web_url(repo: Path) -> str | None:
    try:
        raw = _git(repo, ["config", "--get", "remote.origin.url"]).strip()
    except Exception:
        return None
    if not raw:
        return None
    url = raw
    if url.startswith("git@"):
        host, _, path = url[4:].partition(":")
        url = f"https://{host}/{path}"
    elif url.startswith("ssh://"):
        rest = url[6:]
        if rest.startswith("git@"):
            rest = rest[4:]
        url = "https://" + rest
    if url.endswith(".git"):
        url = url[:-4]
    if url.startswith("http://") or url.startswith("https://"):
        return url.rstrip("/")
    return None


def _to_local_iso(t: str) -> str:
    try:
        dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
        return dt.astimezone().isoformat(timespec="seconds")
    except ValueError:
        return t


def _sort_key(c: dict[str, Any]) -> datetime:
    try:
        dt = datetime.fromisoformat(c["time"])
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return datetime.min.replace(tzinfo=timezone.utc)


_gh_cache: dict[str, Any] = {"key": None, "ts": 0.0, "commits": [], "error": None}


def _github_post(url: str, fields: dict[str, str]) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(fields).encode("utf-8"),
        headers={"Accept": "application/json", "User-Agent": "Lumia"},
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.loads(resp.read().decode("utf-8"))


def github_device_start(client_id: str) -> dict[str, Any]:
    if not client_id:
        return {"ok": False, "reason": "未配置 GitHub Client ID (设置->成就墙)"}
    try:
        data = _github_post(
            "https://github.com/login/device/code",
            {"client_id": client_id, "scope": "repo"},
        )
    except Exception as exc:
        return {"ok": False, "reason": f"请求 GitHub 失败: {exc}"}
    if "device_code" not in data:
        return {"ok": False, "reason": f"GitHub 返回异常: {data.get('error_description') or data}"}
    return {
        "ok": True,
        "device_code": data["device_code"],
        "user_code": data["user_code"],
        "verification_uri": data["verification_uri"],
        "interval": int(data.get("interval", 5)),
        "expires_in": int(data.get("expires_in", 900)),
    }


def github_device_poll(config: Config, device_code: str) -> dict[str, Any]:
    client_id = (config.get("git", "github_client_id", default="") or "").strip()
    try:
        data = _github_post(
            "https://github.com/login/oauth/access_token",
            {
                "client_id": client_id,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
        )
    except Exception as exc:
        return {"ok": False, "reason": f"请求 GitHub 失败: {exc}"}
    error = data.get("error")
    if error in ("authorization_pending", "slow_down"):
        return {"ok": False, "pending": True, "slow_down": error == "slow_down"}
    token = data.get("access_token")
    if not token:
        return {"ok": False, "reason": f"授权失败: {data.get('error_description') or error}"}
    try:
        req = urllib.request.Request(
            "https://api.github.com/user",
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "Lumia",
                "Authorization": "Bearer " + token,
            },
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            user = json.loads(resp.read().decode("utf-8"))
        username = user.get("login", "")
    except Exception as exc:
        return {"ok": False, "reason": f"获取用户信息失败: {exc}"}
    config.update({"git": {"github_username": username, "github_token": token}})
    _gh_cache["key"] = None
    return {"ok": True, "username": username}


def _github_fetch(username: str, token: str) -> list[dict[str, Any]]:
    since = (date.today() - timedelta(days=1)).isoformat()
    query = urllib.parse.quote(f"author:{username} author-date:>={since}")
    url = (
        "https://api.github.com/search/commits?q="
        + query
        + "&sort=author-date&order=desc&per_page=100"
    )
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "Lumia"},
    )
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=8) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    today = date.today().isoformat()
    commits: list[dict[str, Any]] = []
    for item in data.get("items", []):
        meta = item.get("commit") or {}
        author_meta = meta.get("author") or {}
        gh_user = item.get("author") or {}
        local_time = _to_local_iso(author_meta.get("date", ""))
        if local_time[:10] != today:
            continue
        message = meta.get("message") or ""
        subject, _, body = message.partition("\n")
        commits.append(
            {
                "hash": item["sha"][:7],
                "full_hash": item["sha"],
                "author": author_meta.get("name") or gh_user.get("login") or username,
                "author_email": author_meta.get("email", ""),
                "time": local_time,
                "subject": subject.strip(),
                "body": body.strip(),
                "repo": (item.get("repository") or {}).get("name", ""),
                "url": item.get("html_url"),
                "author_url": gh_user.get("html_url"),
                "avatar_url": gh_user.get("avatar_url"),
            }
        )
    return commits


def _github_commits_today(username: str, token: str) -> tuple[list[dict[str, Any]], str | None]:
    key = (username, token, date.today().isoformat())
    if _gh_cache["key"] == key and time.time() - _gh_cache["ts"] < 60:
        return _gh_cache["commits"], _gh_cache["error"]
    commits: list[dict[str, Any]] = []
    error: str | None = None
    try:
        commits = _github_fetch(username, token)
    except Exception as exc:
        error = f"GitHub 拉取失败: {exc}"
    _gh_cache.update({"key": key, "ts": time.time(), "commits": commits, "error": error})
    return commits, error


def achievements(config: Config) -> dict[str, Any]:
    repos = config.get("git", "repos", default=[]) or []
    repo_infos: list[dict[str, Any]] = []
    all_commits: list[dict[str, Any]] = []
    identities: dict[str, tuple[str | None, str | None]] = {}
    for raw in repos:
        repo = Path(raw)
        name = repo.name or repo.resolve().name
        entry: dict[str, Any] = {"repo": str(repo), "name": name}
        if not (repo / ".git").exists():
            entry["error"] = "not a git repo"
        else:
            try:
                commits = commits_today(repo)
                web = _remote_web_url(repo)
                for c in commits:
                    c["repo"] = name
                    c["url"] = f"{web}/commit/{c['full_hash']}" if web else None
                    email = c["author_email"]
                    if email not in identities:
                        identities[email] = _author_identity(email)
                    c["author_url"], c["avatar_url"] = identities[email]
                all_commits.extend(commits)
            except Exception as exc:
                entry["error"] = str(exc)
        repo_infos.append(entry)
    username = (config.get("git", "github_username", default="") or "").strip()
    token = (config.get("git", "github_token", default="") or "").strip()
    github_error = None
    if username:
        gh_commits, github_error = _github_commits_today(username, token)
        seen = {c["full_hash"] for c in gh_commits}
        all_commits = [c for c in all_commits if c["full_hash"] not in seen]
        for c in all_commits:
            if not c["author_url"]:
                c["author_url"] = f"https://github.com/{username}"
                c["avatar_url"] = f"https://github.com/{username}.png?size=80"
        all_commits.extend(gh_commits)
    all_commits.sort(key=_sort_key, reverse=True)
    return {
        "date": date.today().isoformat(),
        "total_commits": len(all_commits),
        "commits": all_commits,
        "repos": repo_infos,
        "github": {"username": username, "error": github_error},
    }
