#!/usr/bin/env bash
# 烧录 LiberNovo 椅控到涂鸦 T5AI
# 用法：./flash.sh
# 出现 Waiting Reset 时按 RST；拨码接通 USB↔UART。

set -euo pipefail
FQBN=tuya_open:tuya_open:TUYA_T5AI_BOARD
SKETCH="$(cd "$(dirname "$0")" && pwd)"
PORT="${1:-/dev/ttyACM0}"
TYU="$HOME/.arduino15/packages/tuya_open/tools/tyutool/2.1.0/tyutool_cli"

echo "Compile..."
arduino-cli compile -b "$FQBN" "$SKETCH"
BIN=$(find "$HOME/.cache/arduino/sketches" -name 'chair-t5ai.ino_QIO.bin' 2>/dev/null | head -1)
echo "BIN=$BIN"
echo "PORT=$PORT"
echo ">>> 出现 Waiting Reset 时请按 RST <<<"
chmod +x "$TYU"
exec "$TYU" -n write --device T5 --port "$PORT" --baud 921600 --start 0x000000 --file "$BIN" --tqdm
