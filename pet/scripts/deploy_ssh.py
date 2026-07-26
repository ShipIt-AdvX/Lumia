"""通过 SSH 将项目部署到 Ubuntu 测试机并远程启动/验证。

用法（密码经环境变量传入，不落盘）：
    set LUMIA_SSH_PASS=xxx
    python scripts/deploy_ssh.py --host 1.2.3.4 --user sunrise [--action deploy|start|status|stop|logs|screenshot]

action:
    deploy      打包项目源码上传、解压并执行 deploy/run.sh 启动（默认）
    start       远程启动桌宠（后台 nohup，绑定 X11 会话）
    status      查看远端进程与日志尾部
    stop        停止远端桌宠
    logs        拉取远端应用日志尾部
    screenshot  远端截屏并下载到本地（验证桌宠是否显示）
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import tarfile
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parent.parent
REMOTE_DIR = "lumia-desktopPet"  # 相对远端 $HOME
INCLUDE = ["main.py", "requirements.txt", "lumia", "assets", "scripts", "deploy"]
EXCLUDE_NAMES = {"__pycache__", ".venv", "build", "dist", "_probe_remote.py", "_probe_remote2.py"}

# 远端为 Xfce + X11 (lightdm)，直接绑定 :0 与用户 Xauthority
GUI_ENV = 'export DISPLAY=:0 XAUTHORITY="$HOME/.Xauthority" XDG_SESSION_TYPE=x11'


def connect(host: str, user: str, password: str) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, username=user, password=password, timeout=15)
    return client


def run(client: paramiko.SSHClient, cmd: str, timeout: int = 60) -> tuple[int, str, str]:
    _, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    code = stdout.channel.recv_exit_status()
    return code, out, err


def show(label: str, code: int, out: str, err: str) -> None:
    print(f"--- {label} (exit={code}) ---")
    if out.strip():
        print(out.rstrip())
    if err.strip():
        print("[stderr]", err.rstrip())


def make_source_tar() -> bytes:
    """内存打包项目源码（排除虚拟环境/缓存等）。"""

    def tar_filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
        parts = Path(info.name).parts
        if any(p in EXCLUDE_NAMES for p in parts):
            return None
        if info.name.endswith(".sh"):
            info.mode = 0o755
        return info

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for item in INCLUDE:
            src = ROOT / item
            if src.exists():
                tar.add(src, arcname=f"{REMOTE_DIR}/{item}", filter=tar_filter)
    return buf.getvalue()


def deploy(client: paramiko.SSHClient) -> None:
    data = make_source_tar()
    print(f"上传项目源码 ({len(data) // 1024} KB) ...")
    sftp = client.open_sftp()
    with sftp.open("lumia-src.tar.gz", "wb") as f:
        f.write(data)
    sftp.close()
    # 保留 .venv（在 REMOTE_DIR 内）避免重复装依赖：只删源码目录再解压
    code, out, err = run(
        client,
        f"cd ~ && for d in main.py requirements.txt lumia assets scripts deploy; do rm -rf {REMOTE_DIR}/$d; done; "
        f"tar -xzf lumia-src.tar.gz && rm lumia-src.tar.gz && ls {REMOTE_DIR}",
        timeout=60,
    )
    show("解压", code, out, err)
    if code != 0:
        sys.exit(1)


START_CMD = f"bash ~/{REMOTE_DIR}/deploy/remote_start.sh"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument(
        "--action", default="deploy",
        choices=["deploy", "start", "status", "stop", "logs", "screenshot"],
    )
    args = parser.parse_args()

    password = os.environ.get("LUMIA_SSH_PASS")
    if not password:
        sys.exit("请通过环境变量 LUMIA_SSH_PASS 提供 SSH 密码")

    client = connect(args.host, args.user, password)
    try:
        if args.action == "deploy":
            show("远端环境", *run(client, "uname -m && lsb_release -ds"))
            deploy(client)
            show("启动", *run(client, START_CMD, timeout=300))
        elif args.action == "start":
            show("启动", *run(client, START_CMD, timeout=300))
        elif args.action == "status":
            show("进程", *run(client, "pgrep -af '[m]ain\\.py --debug' || echo '<未运行>'"))
            show("nohup 日志", *run(client, f"tail -30 ~/{REMOTE_DIR}/nohup.log 2>/dev/null || true"))
            show("应用日志", *run(client, "tail -40 ~/.local/share/lumia-pet/logs/lumia.log 2>/dev/null || echo '<无日志>'"))
        elif args.action == "stop":
            show("停止", *run(client, "pkill -f '[m]ain\\.py --debug' && echo stopped || echo '<未运行>'"))
        elif args.action == "logs":
            show("应用日志", *run(client, "tail -80 ~/.local/share/lumia-pet/logs/lumia.log 2>/dev/null || echo '<无日志>'"))
        elif args.action == "screenshot":
            code, out, err = run(
                client,
                f"{GUI_ENV} && (command -v xfce4-screenshooter >/dev/null && xfce4-screenshooter -f -s /tmp/lumia_screen.png) "
                f"|| (command -v import >/dev/null && import -window root /tmp/lumia_screen.png) "
                f"|| (command -v scrot >/dev/null && scrot -o /tmp/lumia_screen.png); ls -la /tmp/lumia_screen.png",
                timeout=60,
            )
            show("截屏", code, out, err)
            if code == 0:
                local = ROOT / "build" / "remote_screen.png"
                local.parent.mkdir(exist_ok=True)
                sftp = client.open_sftp()
                sftp.get("/tmp/lumia_screen.png", str(local))
                sftp.close()
                print(f"已下载: {local}")
    finally:
        client.close()


if __name__ == "__main__":
    main()
