# 开发任务书 · 不可见（Invisible）

> 给你这份文档时，假定你**还不了解**本项目。  
> 你只负责「用户看不见但必须正确的部分」：本机服务、策略、软硬互联。  
> 另一人负责界面与提醒长什么样，见 [`DEV_VISIBLE.md`](DEV_VISIBLE.md)。

---

## 1. 项目一句话（够用即可）

**Lumia**：通宵写代码时，到点提醒睡觉/喝水/吃饭/久坐；灵感盒把话丢进电脑；椅子可联动；睡前成绩上墙。

你的工作：保证**状态算对、API 稳、硬件能通**。  
你**不要**写 Qt 全屏窗、侧边条样式——那些归可视化同事。

---

## 2. 你怎么跑起来

后端 **OS 无关**（Linux / Windows 同一套 FastAPI）。桌面提醒与用屏采集归可视化同事。

### Linux

```bash
cd lumia
pip install -r requirements.txt
./scripts/run.sh
```

### Windows

```bat
cd lumia
pip install -r requirements.txt
scripts\run.bat
```

或：`set PYTHONPATH=.&& python -m uvicorn server.main:app --host 127.0.0.1 --port 8787`

健康检查：浏览器打开 `http://127.0.0.1:8787/api/health`。

完整带 UI 的启动由对方用 `run-desktop.sh` / `run-desktop.bat`；你单独开发用 `run.sh` / `run.bat` 即可。

环境变量：`.env.example` → `.env`。

**跨平台约束（你必须遵守）：**

- API 只听 `127.0.0.1`/`0.0.0.0:8787`，不要写死 Linux 路径当唯一数据目录（已有 `Path` 相对仓库根则保持）。  
- 不要在 server 里判断 niri/Win32；用屏差异由对方 `FocusSource` 消化，你只收 `POST /api/usage`。  
- 固件仍走局域网 HTTP；Windows 笔记本联调时把文档里的 `PC_IP` 写成对方可达地址即可。

---

## 3. 你要开发的事情（按优先级）

### P0 — BodyNudge 升级状态机（核心）

规则（写进状态发给前端，**不要**在前端再算一遍档位）：

| 该类型已延后次数 `snooze_count` | `escalation` | `intensity` |
|--------------------------------|--------------|-------------|
| 0 | 0 | `soft` |
| 1 | 1 | `edge` |
| ≥2 | 2 | `fullscreen` |

类型：`sleep` | `water` | `meal` | `sit`  

行为：

- **trigger / 到点 fire**：写入 `active_nudge`（必须含 PROTOCOL 字段），WS 广播。  
- **snooze**：清当前 nudge、count+1、设 `snooze_until`；**下次** fire 抬档。  
- **ack**：清 nudge，该类型 count 归零。  
- 睡觉时段：暂停 water/meal/sit；灵感 AI 可整理但 **silent、不吵人**。  
- 久坐 fire 时可顺带 `chair_stretch`。

**主文件：** `server/main.py`（`fire_nudge`、`nudge_loop`、ack/snooze/trigger）。  
**文案来源：** `assets/characters/<id>/meta.yaml` 的 `lines.<type>.<intensity>`。

**自测（curl）：**

```bash
# 第一次 → soft
curl -s -X POST http://127.0.0.1:8787/api/nudge/trigger \
  -H 'Content-Type: application/json' -d '{"type":"sleep"}'

curl -s http://127.0.0.1:8787/api/state | python3 -m json.tool | head

# 延后
curl -s -X POST http://127.0.0.1:8787/api/nudge/snooze \
  -H 'Content-Type: application/json' -d '{"type":"sleep","minutes":1}'

# 再 trigger → 应为 edge；再 snooze + trigger → fullscreen
```

（若有 snooze 冷却，联调时可把间隔调短或直接连续 trigger 的 Demo 路径。）

`active_nudge` **至少**包含：

```json
{
  "type": "sleep",
  "intensity": "edge",
  "escalation": 1,
  "character": "jiege",
  "message": "再不睡觉就让我康康！",
  "snooze_count": 1,
  "snooze_remaining": 1,
  "at": 1710000000.0
}
```

字段以 [`PROTOCOL.md`](PROTOCOL.md) 为准。

### P0 — 硬件 HTTP 入口可用

| 硬件 | 固件目录 | 你的 API |
|------|----------|----------|
| 灵感盒 | `firmware/capture-puck/` | `POST /api/capture/audio`（及文本兜底 `/api/capture/text`） |
| 久坐传感 | `firmware/sit-sensor/` | `POST /api/sit` |
| 椅继电器 | `firmware/chair-relay/` | `POST /api/chair/stretch`；`chair_mode=simulate\|relay\|protocol` |

要求：

- 无真机时：`simulate` + `scripts/sim_sit.py` / 文本 capture 能演示闭环。  
- 固件**只**打 HTTP，不直连桌面进程。  
- ASR/DeepSeek 无 Key 时要有本地降级（现有启发式可保留并加强）。

### P1 — 用屏入库

- `POST /api/usage`：收桌面上报，写入 `usage_events`。  
- `/api/state` 与 `/api/wall` 的 usage **按日聚合**；有桌面上报后不要再用「服务在跑就当 coding」盖掉真数据。  
- **不要**按 OS 写两套 API：Linux（niri）与 Windows（win32）上报同一 JSON 形状（见 PROTOCOL）。

可视化同事只负责采集；你负责存和汇总。

### P1 — AI / 成就墙数据

- `ai_deepen`、`asr_transcribe`、`ai_wall_quote`  
- `/api/wall`、`/api/wall/refresh`、`/api/keepalive/snapshot`  
- 桌宠相关字段保持 PROTOCOL 白名单稳定，避免对方前端白屏。

### P2 — 设置与角色列表 API

- `/api/settings`、`/api/characters`  
- `character` 切换后，下次 nudge 的 `character`/`message` 必须跟着变。

---

## 4. 你不要碰的东西

| 禁止 | 原因 |
|------|------|
| `desktop/nudge/qt_*.py`、主控样式 | 可视化负责 |
| 新建 PyQt 窗口 | 同上 |
| 在 server 里写死「侧边条宽 300px」这类 UI | 你只提供 intensity + 文案 + 角色 id |
| 绕过 HTTP 让固件直连桌面 | 破坏边界与跨平台 |

对方缺图时：你保证 `message`/`character` 正确即可；占位绘制是对方的事。

---

## 5. 怎么和另一人串联

对方文档：[`DEV_VISIBLE.md`](DEV_VISIBLE.md)。  
共同契约：[`PROTOCOL.md`](PROTOCOL.md)。

### 你们之间的数据线

```text
你：fire_nudge → active_nudge + WS broadcast
        ↓
对方：按 intensity 画 soft / edge / fullscreen
        ↓
对方：ack / snooze HTTP
        ↓
你：更新 snooze_count，下次抬档或清零
```

```text
对方：POST /api/usage（app_id, seconds, category）
        ↓
你：入库 + 聚合进 state / wall
```

```text
固件 → 你的 /api/capture|/api/sit|/api/chair
        ↓（sit 到点）
你：fire_nudge(sit) → 对方画提醒（并可你侧触发椅 stretch）
```

### 联调约定（请遵守）

1. **改接口先改 PROTOCOL**  
   加字段、改语义、改路径，先更新 `PROTOCOL.md`，再通知对方，再写代码。

2. **联调检查清单**

   | 步骤 | 你验证 | 对方验证 |
   |------|--------|----------|
   | trigger sleep | state 里 intensity=soft | soft UI |
   | snooze → trigger | intensity=edge，杰哥文案 | 侧边条 |
   | 再 snooze → trigger | intensity=fullscreen | 全屏 |
   | ack | active_nudge 清空 | 窗关闭 |
   | settings.character | 下次 nudge.character 变 | 图/角色变 |
   | sim_sit / 真传感 | seated_minutes、坐提醒 | 若有 nudge 则能显示 |
   | usage 上报 | state.usage 变 | 主控数字变（Linux 与 Windows 各测一轮） |
| 对方无 FocusSource | 不崩，可走网页 Demo | UI 不崩，标明用屏不可用 |

3. **并行方式**  
   - 你可先用 curl/网页备用主控把状态机和硬件跑通。  
   - 对方可先做假数据 UI；但 **Demo 前必须以你的真实 WS 为准**。  
   - 阈值（久坐分钟、喝水间隔）Demo 前由你改短或提供 Demo 专用 settings。

4. **卡住时怎么喊人**

   - 「curl 看 intensity 已是 edge，界面仍是 soft」→ **可视化**  
   - 「界面一直 soft，curl 也是 soft，但已 snooze 过」→ **你**  
   - 「盒子录了音，列表没有灵感」→ **你**（ASR/ingest）  
   - 「有灵感但列表样式丑」→ **可视化**

5. **新功能**  
   - API/硬件/策略 → 你，`modules/manifest.py` 里 `owner=invisible`。  
   - 纯显示 → 对方。  
   - 跨边界 → 先 PROTOCOL，再并行。

---

## 6. 交付标准（你这一侧做完的定义）

- [ ] 仅用 curl 可验证 soft → edge → fullscreen 全链路  
- [ ] 无 Key 时灵感捕获与墙上句子仍有降级输出  
- [ ] sit / capture / chair 在 simulate 下可演示；真机协议文档与路径清晰  
- [ ] usage 有桌面上报时按日聚合正确（与对方 Win/Linux 适配器无关）  
- [ ] `active_nudge` 字段与 PROTOCOL 一致，WS 能推到桌面  
- [ ] 未提交 Qt UI 改动；未把 niri/Win32 细节写进 server  

更短的分工总览：[`TEAM.md`](TEAM.md)。
