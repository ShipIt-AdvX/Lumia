#!/usr/bin/env bash
# Lumia 桌宠 Ubuntu 一键部署运行脚本
# 用法: bash deploy/run.sh [--debug]
# 支持环境变量 PIP_INDEX_URL 指定 pip 镜像源
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$PROJECT_ROOT/.venv"
cd "$PROJECT_ROOT"

info()  { echo -e "\033[32m[run.sh]\033[0m $*"; }
warn()  { echo -e "\033[33m[run.sh]\033[0m $*"; }
fail()  { echo -e "\033[31m[run.sh]\033[0m $*"; exit 1; }

# 1. 检查 python3 / venv / pip
command -v python3 >/dev/null 2>&1 || fail "未找到 python3，请先执行: sudo apt install python3"
python3 -c "import venv" 2>/dev/null || fail "缺少 venv 模块，请先执行: sudo apt install python3-venv"

# 2. 创建虚拟环境并安装依赖
if [ ! -f "$VENV/bin/python" ]; then
    info "创建虚拟环境 $VENV ..."
    python3 -m venv "$VENV"
fi
# 依赖已就绪时跳过 pip，加快二次启动
if "$VENV/bin/python" -c "import PyQt6, PIL" 2>/dev/null; then
    info "依赖已就绪，跳过安装"
else
    info "安装/校验 Python 依赖 ..."
    "$VENV/bin/pip" install --disable-pip-version-check -q -r requirements.txt \
        || fail "pip 安装失败。若网络不佳可设置镜像: export PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple"
fi

# 3. 检查 PyQt6 必需的系统库；缺 libxcb-cursor0 时尝试免 root 修复
#    （apt-get download 不需要 root，dpkg -x 解到项目目录后用 LD_LIBRARY_PATH 加载）
if ! ldconfig -p 2>/dev/null | grep -q "libxcb-cursor.so.0"; then
    LIBDIR="$PROJECT_ROOT/.libs"
    SOFILE=$(ls "$LIBDIR"/usr/lib/*/libxcb-cursor.so.0 2>/dev/null | head -1 || true)
    if [ -z "$SOFILE" ]; then
        warn "系统缺少 libxcb-cursor0，尝试免 root 下载解压到 $LIBDIR ..."
        mkdir -p "$LIBDIR"
        ( cd "$LIBDIR" && apt-get download libxcb-cursor0 2>/dev/null \
            && dpkg -x libxcb-cursor0_*.deb . && rm -f libxcb-cursor0_*.deb ) \
            || warn "免 root 获取失败，若启动报 xcb 错误请执行: sudo apt install libxcb-cursor0"
        SOFILE=$(ls "$LIBDIR"/usr/lib/*/libxcb-cursor.so.0 2>/dev/null | head -1 || true)
    fi
    if [ -n "$SOFILE" ]; then
        export LD_LIBRARY_PATH="$(dirname "$SOFILE")${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
        info "已加载项目级 libxcb-cursor0: $SOFILE"
    fi
fi

# 4. 素材缺失时自动生成
if [ ! -f "assets/sprites/meta.json" ]; then
    info "素材未生成，运行贴图管线 ..."
    "$VENV/bin/python" scripts/build_cat_sprites.py
fi

# 5. Wayland 会话走 XWayland（main.py 内也有兜底检测）
if [ "${XDG_SESSION_TYPE:-}" = "wayland" ]; then
    export QT_QPA_PLATFORM=xcb
    info "Wayland 会话：已设置 QT_QPA_PLATFORM=xcb"
fi

info "启动 Lumia 桌宠 ..."
exec "$VENV/bin/python" main.py "$@"
