"""开机自启：Linux 下写 ~/.config/autostart/lumia-pet.desktop。

Windows 开发机上仅记录日志不实际生效（桌宠目标平台为 Ubuntu）。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from . import APP_NAME
from .config import config_dir

log = logging.getLogger("lumia.autostart")

DESKTOP_TEMPLATE = """[Desktop Entry]
Type=Application
Name=Lumia Desktop Pet
Comment=Minecraft cat desktop pet
Exec={exec_cmd}
Path={work_dir}
Terminal=false
X-GNOME-Autostart-enabled=true
"""


def _desktop_file() -> Path:
    return config_dir().parent / "autostart" / f"{APP_NAME}.desktop"


def set_autostart(enabled: bool) -> bool:
    """写入/移除 autostart desktop 文件。返回是否操作成功。"""
    if sys.platform == "win32":
        log.warning("Windows 开发环境不支持 .desktop 自启，仅在 Ubuntu 上生效")
        return True  # 允许配置项先行保存，换机后生效

    path = _desktop_file()
    try:
        if enabled:
            project_root = Path(__file__).resolve().parent.parent
            run_sh = project_root / "deploy" / "run.sh"
            if run_sh.exists():
                # 优先走 run.sh：它会处理项目级 libxcb-cursor0 的
                # LD_LIBRARY_PATH、素材生成与 Wayland 适配，直接跑
                # venv python 在缺库机器上会启动失败
                exec_cmd = f'bash "{run_sh}" --debug'
            else:
                venv_python = project_root / ".venv" / "bin" / "python"
                python = venv_python if venv_python.exists() else Path(sys.executable)
                exec_cmd = f'"{python}" "{project_root / "main.py"}"'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                DESKTOP_TEMPLATE.format(exec_cmd=exec_cmd, work_dir=project_root),
                encoding="utf-8",
            )
            log.info("已写入自启文件: %s", path)
        else:
            if path.exists():
                path.unlink()
                log.info("已移除自启文件: %s", path)
        return True
    except OSError as exc:
        log.error("自启配置失败: %s", exc)
        return False
