"""构建 Ubuntu 离线自包含包（在 Windows/任意平台开发机上运行）。

产物 dist/lumia-offline.tar.gz 拷到全新 Ubuntu 机器后：
    tar -xzf lumia-offline.tar.gz
    cd lumia-offline
    bash start.sh
全程无需 apt / pip 联网 / sudo。

原理：
- 内置 python-build-standalone 的便携版 Linux CPython（解压即用，不依赖系统 Python）
- 预下载 PyQt6/Pillow 的 manylinux wheels，目标机上离线安装
- PyQt6 锁 6.4.2：Qt 6.5+ 需要系统库 libxcb-cursor0（全新 Ubuntu 默认没有），
  Qt 6.4 无此依赖，可在纯净桌面系统直接运行

用法：python scripts/make_offline_bundle.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build" / "offline"
DIST = ROOT / "dist"
BUNDLE_NAME = "lumia-offline"

# 便携版 CPython（Linux x86_64, glibc 2.17+，覆盖所有近代 Ubuntu）
PY_VERSION = "3.11.10"
PY_RELEASE = "20241016"
PY_TARBALL = f"cpython-{PY_VERSION}+{PY_RELEASE}-x86_64-unknown-linux-gnu-install_only.tar.gz"
PY_URL = (
    "https://github.com/astral-sh/python-build-standalone/releases/download/"
    f"{PY_RELEASE}/{PY_TARBALL.replace('+', '%2B')}"
)

# 离线包依赖（PyQt6 6.4.x 见模块 docstring；abi3 wheel 与 cp311 兼容）
OFFLINE_REQUIREMENTS = ["PyQt6==6.4.2", "Pillow==10.4.0"]

START_SH = """#!/usr/bin/env bash
# Lumia 桌宠离线启动脚本：无需联网、无需 apt、无需 sudo
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$HERE/runtime/python/bin/python3"

if [ ! -x "$PY" ]; then
    echo "[start.sh] 首次运行：解压内置 Python 运行时 ..."
    tar -xzf "$HERE"/runtime/cpython-*.tar.gz -C "$HERE/runtime"
fi

if ! "$PY" -c "import PyQt6" 2>/dev/null; then
    echo "[start.sh] 首次运行：离线安装依赖（本地 wheels，无需网络）..."
    "$PY" -m pip install --no-index --find-links "$HERE/wheels" -q PyQt6 Pillow
fi

if [ "${XDG_SESSION_TYPE:-}" = "wayland" ]; then
    export QT_QPA_PLATFORM=xcb
fi

echo "[start.sh] 启动 Lumia 桌宠 ..."
exec "$PY" "$HERE/app/main.py" "$@"
"""


def download(url: str, dest: Path) -> None:
    if dest.exists():
        print(f"已存在，跳过下载: {dest.name}")
        return
    print(f"下载 {url} ...")
    tmp = dest.with_suffix(".part")
    with urllib.request.urlopen(url, timeout=60) as resp, open(tmp, "wb") as f:
        shutil.copyfileobj(resp, f)
    tmp.rename(dest)
    print(f"完成: {dest.name} ({dest.stat().st_size // 1024 // 1024} MB)")


def download_wheels(wheel_dir: Path) -> None:
    wheel_dir.mkdir(parents=True, exist_ok=True)
    print("下载 Linux manylinux wheels ...")
    cmd = [
        sys.executable, "-m", "pip", "download",
        "--only-binary=:all:",
        "--platform", "manylinux_2_28_x86_64",
        "--platform", "manylinux2014_x86_64",
        "--platform", "manylinux1_x86_64",
        "--python-version", "3.11",
        "--implementation", "cp",
        "-d", str(wheel_dir),
        *OFFLINE_REQUIREMENTS,
    ]
    subprocess.run(cmd, check=True)
    print(f"wheels 就绪: {[p.name for p in wheel_dir.glob('*.whl')]}")


def copy_app(app_dir: Path) -> None:
    if app_dir.exists():
        shutil.rmtree(app_dir)
    app_dir.mkdir(parents=True)
    shutil.copy2(ROOT / "main.py", app_dir / "main.py")
    shutil.copytree(ROOT / "lumia", app_dir / "lumia",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "assets", app_dir / "assets")
    print("项目代码与素材已拷贝")


def make_tarball() -> Path:
    DIST.mkdir(exist_ok=True)
    out = DIST / f"{BUNDLE_NAME}.tar.gz"
    if out.exists():
        out.unlink()
    print("打包 tar.gz（Windows 下手动赋予脚本可执行权限）...")

    def set_permissions(info: tarfile.TarInfo) -> tarfile.TarInfo:
        if info.name.endswith((".sh", "/python3")):
            info.mode = 0o755
        return info

    with tarfile.open(out, "w:gz") as tar:
        tar.add(BUILD, arcname=BUNDLE_NAME, filter=set_permissions)
    print(f"产物: {out} ({out.stat().st_size // 1024 // 1024} MB)")
    return out


def main() -> None:
    BUILD.mkdir(parents=True, exist_ok=True)

    runtime_dir = BUILD / "runtime"
    runtime_dir.mkdir(exist_ok=True)
    download(PY_URL, runtime_dir / PY_TARBALL)

    download_wheels(BUILD / "wheels")
    copy_app(BUILD / "app")
    (BUILD / "start.sh").write_text(START_SH, encoding="utf-8", newline="\n")

    out = make_tarball()
    print(
        f"\n构建完成！把 {out.name} 拷到 Ubuntu 机器后执行:\n"
        f"  tar -xzf {out.name}\n"
        f"  cd {BUNDLE_NAME}\n"
        f"  bash start.sh\n"
    )


if __name__ == "__main__":
    main()
