from __future__ import annotations

import os
import shutil
import subprocess
from datetime import date
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
            "--pretty=format:%H\x1f%h\x1f%an\x1f%cI\x1f%s\x1f%b\x1e",
            "--all",
        ],
    )
    commits: list[dict[str, str]] = []
    for chunk in out.split("\x1e"):
        chunk = chunk.strip("\r\n")
        if not chunk.strip():
            continue
        parts = chunk.split("\x1f")
        if len(parts) != 6:
            continue
        full, short, author, when, subject, body = parts
        commits.append(
            {
                "hash": short,
                "full_hash": full,
                "author": author,
                "time": when,
                "subject": subject,
                "body": body.strip(),
            }
        )
    return commits


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


def achievements(config: Config) -> dict[str, Any]:
    repos = config.get("git", "repos", default=[]) or []
    repo_infos: list[dict[str, Any]] = []
    all_commits: list[dict[str, Any]] = []
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
                all_commits.extend(commits)
            except Exception as exc:
                entry["error"] = str(exc)
        repo_infos.append(entry)
    all_commits.sort(key=lambda c: c["time"], reverse=True)
    return {
        "date": date.today().isoformat(),
        "total_commits": len(all_commits),
        "commits": all_commits,
        "repos": repo_infos,
    }
