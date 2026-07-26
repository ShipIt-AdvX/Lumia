# DesktopDaemon 赛道材料（清闲）

## 1. 项目名称 + 一句话

**Lumia**：一只替开发者看守任务边界与睡眠/身体边界的本地精灵——灵感先入库、夜里静默整理，睡前把成绩投到墙上，到点用角色请来离开键盘；桌上伙伴守夜，椅子也能一起动。

## 2. 哪些能力和数据留在本地？

| 本地（电脑 + 香橙派守夜副本） | 云端（按需） |
|------------------------------|--------------|
| Focus / Idea / Todo、用屏粗统计、BodyNudge 策略、守夜快照 | 讯飞 ASR（可切端侧） |
| 成就墙结构化数据 | DeepSeek：深化灵感 + 一句收束语 |
| 压力传感状态、椅控指令下发 | — |

**为何这样切：** 通宵守护必须 24h 可讲清「数据在你桌上」；重模型按需，ARM 派只做仪表与守夜，不上大模型。

## 3. 技术方案简述

- **电脑大脑：** Python FastAPI + SQLite + WebSocket。  
- **桌面守护：** PyQt6（`python -m desktop`）— 托盘主控；BodyNudge 三档 `soft`→`edge`→`fullscreen`；用屏经可替换 `FocusSource`（本轮 niri）。升级逻辑与角色包与 OS 无关，便于后续 Win/Mac。  
- **灵感盒：** ESP32 按钮+麦 → `POST /api/capture/*`。  
- **有屏桌宠：** 浏览器打开 `/pet`（Orange Pi + HDMI）。  
- **久坐：** 椅下压力传感 → `POST /api/sit`。  
- **清闲椅：** `chair_mode=simulate|relay|protocol`；无 SDK 时继电器并联扶手拉伸键。  

ARM 取舍：派上零大模型；关机后读 `/api/keepalive/snapshot` 或静态守夜页。

## 蓝盒子（Hack the Rest）补充

休息被做成可执行状态：看见成就 → 角色送睡；睡点 AI 仍整理但不叫醒；喝水/吃饭/久坐同一边界引擎，进入睡觉时段自动停掉吵闹提醒。
