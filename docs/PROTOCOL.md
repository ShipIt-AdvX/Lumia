# Lumia 职责契约与接口协议

## 三端（禁止混称）

| 名称 | 职责 | 不做 |
|------|------|------|
| **灵感盒** | 按钮+麦克风录音上传 | 不显示内容、不跑 AI |
| **有屏桌宠** | 渲染状态快照白名单；守夜副本 | 不录音、不做成就墙 |
| **电脑本地** | 真相源、ASR、AI、BodyNudge、成就墙、椅控；**桌面守护**负责分级提醒与用屏采集 | 网页主控仅为备用 |

## 桌面守护（可移植）

- **升级逻辑 / 角色包 / HTTP·WS 契约**：与 OS 无关（FastAPI + `assets/characters`）。
- **三级显示**：`soft` → `edge` → `fullscreen`（桌面端 `NudgePresenter`）。
- **用屏**：`FocusSource` 适配器——Linux/`niri`、Windows/`win32`（ctypes）；无适配器时 `null` 降级。上报统一 `POST /api/usage`，服务端不区分 OS。
- 启动：Linux `./scripts/run-desktop.sh`；Windows `scripts\run-desktop.bat`。

## 桌宠显示白名单

`face` | `focus` | `next_boundary` | `seated_minutes` | `idea_count` | `todo_count` | `keepalive`

## BodyNudge 类型与强度

类型：`sleep` | `water` | `meal` | `sit`

强度 `intensity`（由该类型 `snooze_count` 决定，`escalation = min(count, 2)`）：

| escalation | intensity | 语义 |
|------------|-----------|------|
| 0 | `soft` | 不抢焦点的短提示 |
| 1 | `edge` | 屏幕侧边角色条 |
| 2 | `fullscreen` | 全屏角色，尽量拦住键盘 |

睡觉时段：暂停 `water` / `meal` / `sit`；AI 深化静默（零通知）。

`active_nudge` 示例：

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

## HTTP API（电脑 :8787）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查 |
| GET | `/api/state` | 全局状态（含 pet 快照、usage、active_nudge） |
| POST | `/api/focus` | `{"title":"..."}` |
| POST | `/api/capture/text` | `{"text":"..."}` 灵感文本 |
| POST | `/api/capture/audio` | multipart `file` 音频 |
| GET | `/api/ideas` | 灵感/待办列表 |
| POST | `/api/ideas/{id}/confirm` | 采纳 AI 深化 |
| POST | `/api/ideas/{id}/discard` | 丢弃 |
| POST | `/api/sit` | `{"seated":true,"pressure":0.8}` |
| POST | `/api/nudge/ack` | `{"type":"water"}` — 清零该类型升级 |
| POST | `/api/nudge/snooze` | `{"type":"sit","minutes":10}` — 下次抬档 |
| POST | `/api/nudge/trigger` | 强制触发（Demo）`{"type":"sleep"}` |
| POST | `/api/usage` | 桌面用屏上报 `{"app_id","title","seconds","category"}` |
| GET | `/api/characters` | 角色包列表 |
| POST | `/api/chair/stretch` | 触发椅拉伸（协议或模拟） |
| GET | `/api/wall` | 成就墙数据 |
| POST | `/api/wall/refresh` | 重新生成 DeepSeek 句 |
| POST | `/api/settings` | 更新设置（含 `character`） |
| GET | `/api/keepalive/snapshot` | 桌宠守夜副本 |
| WS | `/ws` | 实时状态推送 |

### 用屏上报

```http
POST /api/usage
{"app_id":"cursor","title":"…","seconds":2.0,"category":"coding"}
```

`category`: `coding` | `other` | `idle`。有桌面上报后，状态里的 usage 按当日 `usage_events` 聚合。

## 角色包

```
assets/characters/<id>/
  meta.yaml
  edge/<type>.png
  fullscreen/<type>.png
```

`meta.yaml` 的 `lines.<type>.<intensity>` 提供文案。

## 灵感盒固件约定（涂鸦 T5AI + 电脑讯飞）

1. 按住外接键（默认 **P20↔GND**）录音，松开或超时结束。  
2. `POST http://{PC_IP}:8787/api/capture/audio`，multipart 字段名 **`file`**（16 kHz WAV）。  
3. 电脑侧用讯飞 ASR（`LUMIA_ASR_MODE=xfyun` + `LUMIA_XFYUN_*`）；上传成功后板端清空录音缓冲。  
4. 可选 LED：录音中亮。

## 久坐传感约定

周期性或状态变化时：

```http
POST /api/sit
{"seated": true, "pressure": 0.0-1.0}
```

`seated=false` 时清除连续坐姿计时。

## 椅控约定

```http
POST /api/chair/stretch
{"source": "sit_nudge"}
```

实现优先级：官方协议 → 继电器并联扶手键 → 仅日志模拟（`chair.mode=simulate`）。
