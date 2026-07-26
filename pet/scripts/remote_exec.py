"""在 Ubuntu 测试机上执行任意 shell 命令（调试辅助）。

用法：
    set LUMIA_SSH_PASS=xxx
    python scripts/remote_exec.py --host 1.2.3.4 --user sunrise "uname -a"
"""

from __future__ import annotations

import argparse
import os
import sys

import paramiko


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("cmd")
    args = parser.parse_args()

    password = os.environ.get("LUMIA_SSH_PASS")
    if not password:
        sys.exit("请通过环境变量 LUMIA_SSH_PASS 提供 SSH 密码")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(args.host, username=args.user, password=password, timeout=15)
    try:
        _, stdout, stderr = client.exec_command(args.cmd, timeout=args.timeout)
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        code = stdout.channel.recv_exit_status()
        if out.strip():
            print(out.rstrip())
        if err.strip():
            print("[stderr]", err.rstrip())
        sys.exit(code)
    finally:
        client.close()


if __name__ == "__main__":
    main()
