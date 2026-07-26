#!/usr/bin/env bash
# 远程启动辅助脚本：由 scripts/deploy_ssh.py 经 SSH 调用。
# 单独成文件的原因：若把这些命令直接塞进 ssh 命令行，pkill 的模式会
# 匹配到携带命令文本的外层 shell 自身导致自杀。
set -uo pipefail

export DISPLAY=:0 XAUTHORITY="$HOME/.Xauthority" XDG_SESSION_TYPE=x11
# 测试机访问 pypi 官方源极慢，强制走清华镜像
export PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

cd "$(dirname "${BASH_SOURCE[0]}")/.."

# 清理旧实例（本脚本 cmdline 是 bash .../remote_start.sh，不会被匹配到）
pkill -f 'main\.py --debug' 2>/dev/null
pkill -f 'run\.sh --debug' 2>/dev/null
pkill -f 'pip install' 2>/dev/null
sleep 1

nohup bash deploy/run.sh --debug > nohup.log 2>&1 &
sleep 8
echo "=== nohup.log 尾部 ==="
tail -30 nohup.log
