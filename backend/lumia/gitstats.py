from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path
from typing import Any

from .config import Config


def _git(repo: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        timeout=15,
    )
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
            "--pretty=format:%h\x1f%an\x1f%cI\x1f%s",
            "--all",
        ],
    )
    commits: list[dict[str, str]] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("\x1f")
        if len(parts) != 4:
            continue
        h, author, when, subject = parts
        commits.append({"hash": h, "author": author, "time": when, "subject": subject})
    return commits


def achievements(config: Config) -> dict[str, Any]:
    repos = config.get("git", "repos", default=[]) or []
    result: list[dict[str, Any]] = []
    total = 0
    for raw in repos:
        repo = Path(raw)
        entry: dict[str, Any] = {"repo": str(repo), "name": repo.name}
        if not (repo / ".git").exists():
            entry["error"] = "not a git repo"
            entry["commits"] = []
        else:
            try:
                commits = commits_today(repo)
                entry["commits"] = commits
                total += len(commits)
            except Exception as exc:
                entry["error"] = str(exc)
                entry["commits"] = []
        result.append(entry)
    return {
        "date": date.today().isoformat(),
        "total_commits": total,
        "repos": result,
    }
