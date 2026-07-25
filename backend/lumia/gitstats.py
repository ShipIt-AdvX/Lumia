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
            "--pretty=format:%H\x1f%h\x1f%an\x1f%ae\x1f%cn\x1f%ce\x1f%cI\x1f%s\x1f%b\x1e",
            "--all",
        ],
    )
    commits: list[dict[str, str]] = []
    for chunk in out.split("\x1e"):
        chunk = chunk.strip("\r\n")
        if not chunk.strip():
            continue
        parts = chunk.split("\x1f")
        if len(parts) != 9:
            continue
        full, short, author, email, committer, committer_email, when, subject, body = parts
        commits.append(
            {
                "hash": short,
                "full_hash": full,
                "author": author,
                "author_email": email,
                "committer": committer,
                "committer_email": committer_email,
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


_email_user_cache: dict[str, tuple[str | None, str | None]] = {}


def _github_user_by_email(email: str, token: str) -> tuple[str | None, str | None]:
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        return None, None
    if email in _email_user_cache:
        return _email_user_cache[email]
    try:
        query = urllib.parse.quote(f"author-email:{email}")
        url = "https://api.github.com/search/commits?q=" + query + "&per_page=1"
        req = urllib.request.Request(
            url,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "Lumia"},
        )
        if token:
            req.add_header("Authorization", "Bearer " + token)
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None, None
    result: tuple[str | None, str | None] = (None, None)
    items = data.get("items") or []
    if items:
        gh_user = items[0].get("author") or {}
        if gh_user.get("html_url"):
            result = (gh_user["html_url"], gh_user.get("avatar_url"))
    _email_user_cache[email] = result
    return result


def _resolve_identity(email: str, token: str) -> tuple[str | None, str | None]:
    url, avatar = _author_identity(email)
    if url:
        return url, avatar
    gh_url, gh_avatar = _github_user_by_email(email, token)
    if gh_url:
        return gh_url, gh_avatar
    return url, avatar


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


def _github_search(username: str, token: str, qualifier: str) -> list[dict[str, Any]]:
    since = (date.today() - timedelta(days=1)).isoformat()
    query = urllib.parse.quote(f"{qualifier}:{username} {qualifier}-date:>={since}")
    url = (
        "https://api.github.com/search/commits?q="
        + query
        + f"&sort={qualifier}-date&order=desc&per_page=100"
    )
    req = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "Lumia"},
    )
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=8) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("items", [])


def _github_fetch(username: str, token: str) -> list[dict[str, Any]]:
    today = date.today().isoformat()
    seen: set[str] = set()
    commits: list[dict[str, Any]] = []
    for qualifier in ("author", "committer"):
        for item in _github_search(username, token, qualifier):
            sha = item["sha"]
            if sha in seen:
                continue
            seen.add(sha)
            meta = item.get("commit") or {}
            author_meta = meta.get("author") or {}
            committer_meta = meta.get("committer") or {}
            gh_user = item.get("author") or {}
            local_time = _to_local_iso(author_meta.get("date", ""))
            if local_time[:10] != today:
                local_time = _to_local_iso(committer_meta.get("date", ""))
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
                    "committer": committer_meta.get("name", ""),
                    "committer_email": committer_meta.get("email", ""),
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


def _self_identity(repo: Path) -> tuple[str, str]:
    try:
        email = _git(repo, ["config", "user.email"]).strip().lower()
    except Exception:
        email = ""
    try:
        name = _git(repo, ["config", "user.name"]).strip()
    except Exception:
        name = ""
    return email, name


def _matches_self(name: str, email: str, me_email: str, me_name: str, username: str) -> bool:
    email = (email or "").strip().lower()
    name = (name or "").strip()
    if me_email and email == me_email:
        return True
    if me_name and name == me_name:
        return True
    if username:
        u = username.lower()
        if name.lower() == u:
            return True
        m = re.fullmatch(r"(?:\d+\+)?([a-z0-9-]+)@users\.noreply\.github\.com", email)
        if m and m.group(1) == u:
            return True
    return False


def _co_authors(body: str) -> list[tuple[str, str]]:
    return re.findall(
        r"^\s*co-authored-by:\s*(.*?)\s*<([^>]+)>", body or "", re.IGNORECASE | re.MULTILINE
    )


def _is_own_commit(c: dict[str, Any], me_email: str, me_name: str, username: str) -> bool:
    if _matches_self(c["author"], c["author_email"], me_email, me_name, username):
        return True
    if _matches_self(c.get("committer", ""), c.get("committer_email", ""), me_email, me_name, username):
        return True
    for name, email in _co_authors(c["body"]):
        if _matches_self(name, email, me_email, me_name, username):
            return True
    return False


def achievements(config: Config) -> dict[str, Any]:
    repos = config.get("git", "repos", default=[]) or []
    username = (config.get("git", "github_username", default="") or "").strip()
    token = (config.get("git", "github_token", default="") or "").strip()
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
                me_email, me_name = _self_identity(repo)
                if me_email or me_name or username:
                    commits = [
                        c for c in commits if _is_own_commit(c, me_email, me_name, username)
                    ]
                web = _remote_web_url(repo)
                for c in commits:
                    c["repo"] = name
                    c["own_author"] = _matches_self(
                        c["author"], c["author_email"], me_email, me_name, username
                    )
                    c["url"] = f"{web}/commit/{c['full_hash']}" if web else None
                    email = c["author_email"]
                    if email not in identities:
                        identities[email] = _resolve_identity(email, token)
                    c["author_url"], c["avatar_url"] = identities[email]
                all_commits.extend(commits)
            except Exception as exc:
                entry["error"] = str(exc)
        repo_infos.append(entry)
    github_error = None
    if username:
        gh_commits, github_error = _github_commits_today(username, token)
        seen = {c["full_hash"] for c in gh_commits}
        all_commits = [c for c in all_commits if c["full_hash"] not in seen]
        for c in all_commits:
            if c.get("own_author") and not c["author_url"]:
                c["author_url"] = f"https://github.com/{username}"
                c["avatar_url"] = f"https://github.com/{username}.png?size=80"
        all_commits.extend(gh_commits)
    for c in all_commits:
        c.pop("own_author", None)
        contributors = [
            {
                "name": c["author"],
                "email": c["author_email"],
                "url": c["author_url"],
                "avatar": c["avatar_url"],
            }
        ]
        seen_people = {(c["author_email"] or c["author"]).strip().lower()}
        for co_name, co_email in _co_authors(c.get("body", "")):
            key = (co_email or co_name).strip().lower()
            if key in seen_people:
                continue
            seen_people.add(key)
            if co_email not in identities:
                identities[co_email] = _resolve_identity(co_email, token)
            url, avatar = identities[co_email]
            contributors.append({"name": co_name, "email": co_email, "url": url, "avatar": avatar})
        cm_name = c.get("committer", "")
        cm_email = c.get("committer_email", "")
        cm_key = (cm_email or cm_name).strip().lower()
        if (
            cm_key
            and cm_key not in seen_people
            and cm_email.strip().lower() != "noreply@github.com"
            and not cm_name.endswith("[bot]")
        ):
            seen_people.add(cm_key)
            if cm_email not in identities:
                identities[cm_email] = _resolve_identity(cm_email, token)
            url, avatar = identities[cm_email]
            contributors.append({"name": cm_name, "email": cm_email, "url": url, "avatar": avatar})
        c["contributors"] = contributors
    all_commits.sort(key=_sort_key, reverse=True)
    return {
        "date": date.today().isoformat(),
        "total_commits": len(all_commits),
        "commits": all_commits,
        "repos": repo_infos,
        "github": {"username": username, "error": github_error},
    }
