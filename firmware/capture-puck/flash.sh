#!/usr/bin/env bash
# 烧录灵感盒固件到涂鸦 T5AI-Board
# 用法：./flash.sh
# 当出现 "Waiting Reset" 时，按一下板上 RST 键。
# 确认板子 4 位拨码开关已接通 USB↔UART（两路串口都拨到通）。

set -euo pipefail
FQBN=tuya_open:tuya_open:TUYA_T5AI_BOARD
SKETCH="$(cd "$(dirname "$0")" && pwd)"
PORT="${1:-/dev/ttyACM0}"
BIN=$(find "$HOME/.cache/arduino/sketches" -name 'capture-puck.ino_QIO.bin' 2>/dev/null | head -1)
TYU="$HOME/.arduino15/packages/tuya_open/tools/tyutool/2.1.0/tyutool_cli"

echo "Compile..."
arduino-cli compile -b "$FQBN" "$SKETCH"
BIN=$(find "$HOME/.cache/arduino/sketches" -name 'capture-puck.ino_QIO.bin' 2>/dev/null | head -1)
echo "BIN=$BIN"
echo "PORT=$PORT"
echo ">>> 出现 Waiting Reset 时请按 RST <<<"
chmod +x "$TYU"
exec "$TYU" -n write --device T5 --port "$PORT" --baud 921600 --start 0x000000 --file "$BIN" --tqdm
