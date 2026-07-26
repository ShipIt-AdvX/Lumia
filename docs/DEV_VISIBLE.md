# 开发任务书 · 可视化（Visible）

> 给你这份文档时，假定你**还不了解**本项目。  
> 你只负责「用户眼睛能看见的部分」。另一人负责后端与硬件，见同目录 [`DEV_INVISIBLE.md`](DEV_INVISIBLE.md)。  
> **桌面 UI 与用屏采集必须同时考虑 Linux 与 Windows**（macOS 可后置 stub）。

---

## 1. 项目一句话（够用即可）

**Lumia**：通宵写代码时，到点用角色提醒你睡觉/喝水/吃饭/起身；灵感先丢进盒子，成绩可以投到墙上。

你的工作：把提醒、主控、角色、桌宠/成就墙**做得能看、能演示**，并在 **Linux / Windows** 上都能启动。  
你**不要**改升级算法、数据库、固件——那些归另一人。

---

## 2. 你怎么跑起来

### Linux

```bash
cd lumia
# Arch/CachyOS：sudo pacman -S python-pyqt6
# 用屏本机默认走 niri（有 niri 命令时）

chmod +x scripts/run-desktop.sh
./scripts/run-desktop.sh
```

Wayland 下 Qt 默认 `QT_QPA_PLATFORM=wayland`（由 `desktop/app.py` 设置）。若托盘/置顶异常，可试：

```bash
QT_QPA_PLATFORM=xcb ./scripts/run-desktop.sh
```

### Windows

1. 安装 Python 3.11+，勾选 Add to PATH。  
2. 安装依赖：`pip install -r requirements.txt`  
3. 安装 **PyQt6**：`pip install PyQt6`（Windows 用 pip 即可，不必系统包）。  
4. 双击或在 `lumia` 目录执行：

```bat
scripts\run-desktop.bat
```

等价：`set PYTHONPATH=.&& python -m desktop`

强制指定用屏适配器（调试用）：

```bat
set LUMIA_FOCUS_SOURCE=win32
scripts\run-desktop.bat
```

### 共通

- 大脑地址默认 `http://127.0.0.1:8787`（桌面端会尝试自拉 uvicorn）。  
- 健康检查：浏览器打开该地址，或 `curl http://127.0.0.1:8787/api/health`（Windows 可用浏览器）。

---

## 3. 你要开发的事情（按优先级）

### P0 — 三级提醒（Linux / Windows 同一套 Qt）

产品规则（**显示**由你实现，**何时升到哪一档**由后端算好发给你）：

| 档位 `intensity` | 你要做出的样子 |
|------------------|----------------|
| `soft` | 不抢焦点：托盘通知（`QSystemTrayIcon.showMessage`，两台 OS 通用） |
| `edge` | 屏幕**侧边**角色条；几何用**屏宽比例**（已有 `EDGE_WIDTH_RATIO`），禁止写死「只在 niri 上能用」的 API |
| `fullscreen` | **全屏**角色 + 「去了 / 延后」 |

**改这些文件（保持跨平台，勿 `import` niri/win32）：**

- `desktop/nudge/qt_soft.py`
- `desktop/nudge/qt_edge.py`
- `desktop/nudge/qt_fullscreen.py`
- `desktop/nudge/qt_presenter.py`
- `desktop/nudge/media_surface.py`

**平台注意：**

| 点 | Linux | Windows |
|----|-------|---------|
| soft 通知 | 部分桌面要开「状态栏通知」权限 | 首次可能要允许托盘气泡 |
| edge 置顶 | Wayland 上层级偶发不如 X11；可接受，仍用 Qt 置顶窗 | 一般正常；注意多显示器取 `primaryScreen()` |
| fullscreen | `showFullScreen()` | 同左；部分游戏全屏独占时可能盖不住，Demo 用普通窗口场景即可 |
| 禁止 | 在 nudge UI 里调 `niri msg` | 在 nudge UI 里调 Win32 API |

**自测：** 两台 OS 各走一遍 soft → 延后 → edge → 再延后 → fullscreen → ack。

### P0 — Linux / Windows 用屏适配（FocusSource）

提醒 UI **不**绑 OS；**只有**前台窗口采集绑 OS。

| 文件 | 作用 |
|------|------|
| `desktop/platform/base.py` | `FocusInfo` / `FocusSource` 协议（不要改语义） |
| `desktop/platform/linux_niri.py` | Linux + niri（已有） |
| `desktop/platform/win32.py` | Windows `GetForegroundWindow`（已有 ctypes 实现） |
| `desktop/platform/null.py` | 无适配器时桌面仍能开，usage 记 idle |
| `desktop/platform/__init__.py` | `create_focus_source()` 按 OS / `LUMIA_FOCUS_SOURCE` 选择 |
| `desktop/platform/categorize.py` | `app_id`→coding/other（**两边共用**，补 Windows 进程名） |
| `desktop/usage_tracker.py` | 只调 `FocusSource.poll()` 再 `POST /api/usage` |

**你要做的适配工作：**

1. **Linux**  
   - 保证有 niri 时 usage 真实。  
   - 无 niri 时：应落到 `NullFocusSource`，主控标明「用屏不可用」，**不能崩**。  
   - 若要支持其它桌面（Hyprland / KDE），**新建** `platform/linux_xxx.py` 并在 `__init__.py` 注册，不要把逻辑写进 `qt_edge.py`。

2. **Windows**  
   - 验证 `Win32FocusSource`：切 Cursor / 浏览器时 `app_id`、category 合理。  
   - 在 `categorize.py` 补常见进程 stem（如 `cursor`、`code`、`windowsterminal`）。  
   - soft/edge/fullscreen 在 Win10/11 目测通过。

3. **自测命令**

```bash
# Linux
LUMIA_FOCUS_SOURCE=niri ./scripts/run-desktop.sh
LUMIA_FOCUS_SOURCE=null ./scripts/run-desktop.sh   # 应能开 UI

# Windows (cmd)
set LUMIA_FOCUS_SOURCE=win32
scripts\run-desktop.bat
```

### P0 — 角色看得见

- `assets/characters/<id>/`：`meta.yaml` + `edge/` + `fullscreen/` 媒体  
- `desktop/character_loader.py`  
- 路径用 `pathlib`，避免 `/` 写死导致 Windows 出问题（已有则保持）。

### P1 — 主控窗

- `desktop/main_window.py`、`desktop/app.py`  
- 启动脚本：Linux `scripts/run-desktop.sh`；Windows `scripts/run-desktop.bat`  
- 主控显示当前 `FocusSource` 名称（便于联调）。

### P2 — 投屏视觉

- `server/static/pet/`、`server/static/wall/`（浏览器，天然跨 OS）

---

## 4. 你不要碰的东西

| 禁止 | 原因 |
|------|------|
| `server/main.py` 里 `fire_nudge` / snooze | 升级规则归不可见 |
| `firmware/` | 硬件归不可见 |
| 提醒 UI 里直接调 niri / Win32 | 只用 `FocusSource` |
| 假设「用户一定是 Linux」写死路径或 shell | 双端都要能跑 |

---

## 5. 怎么和另一人串联

对方：[`DEV_INVISIBLE.md`](DEV_INVISIBLE.md)。契约：[`PROTOCOL.md`](PROTOCOL.md)。

### 数据线（与 OS 无关）

```text
不可见 active_nudge (intensity…)  --WS-->  你的 Qt Presenter
你的 ack/snooze                 --HTTP-->  不可见抬档/清零
你的 FocusSource                --POST /api/usage-->  不可见入库
```

后端**不需要**知道你在用 niri 还是 Win32；只收 `app_id` / `category` / `seconds`。

### 联调检查清单（Linux 与 Windows 各做一轮更佳）

| 步骤 | 你 | 不可见 |
|------|----|--------|
| trigger sleep | soft UI | intensity=soft |
| snooze→trigger | edge | intensity=edge |
| 再 snooze→trigger | fullscreen | fullscreen |
| ack | 窗关 | active_nudge 空 |
| 切 IDE/浏览器 | 主控 usage 变 | state.usage 变 |
| 无 FocusSource | UI 不崩 | 仍可用手动/网页 Demo |

### 卡住找谁

- intensity 不对 → **不可见**  
- intensity 对但 Win/Linux 某一端没画出来 → **你**  
- Win 上报了 usage，墙数字不变 → **不可见**  
- 只有 Linux 有 usage、Win 没有 → **你**（FocusSource）

---

## 6. 交付标准

- [ ] Linux 与 Windows 均可启动桌面端并演示 soft→edge→fullscreen  
- [ ] Linux（niri）与 Windows（win32）用屏能随前台变化（或明确 null 降级）  
- [ ] nudge UI 代码无 OS 专用分支（或仅有必要的 Qt 行为差异且有注释）  
- [ ] `jiege` 睡觉视觉可演示；ack/snooze 正确  
- [ ] 未改后端升级逻辑与固件  

总览：[`TEAM.md`](TEAM.md)。
