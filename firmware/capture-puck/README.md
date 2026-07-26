# 灵感盒 · 涂鸦 T5AI + 电脑讯飞 ASR（实时流）

开机连上 **WiFi + 桌宠** 后自动持续录音，PCM 分片实时上传到电脑；**板端不落盘**，避免占满空间。断电/断连后服务端空闲约 8 秒收尾：拼 WAV → 讯飞识别 → 写入灵感。

## 接线

| 用途 | 针脚 |
|------|------|
| 按钮 | P20 ↔ GND（短按：有电时强制结束本段并开新会话） |
| LED | **P9**（流式常亮） |
| 麦 | 板载，无需接线 |

## 配网

1. 手机连 **LumiaPuck**，密码 **12345678**（2.4G）
2. 打开 **http://192.168.4.1/**
3. 填 WiFi SSID/密码 + 桌宠/电脑 IP（端口默认 8787）→ 保存并应用

WiFi 与桌宠都正常后 AP 会自动关掉并开始流式录音。

## 电脑侧 API

- `POST /api/capture/stream/start` → `{session}`
- `POST /api/capture/stream/chunk?session=...` body=原始 PCM
- `POST /api/capture/stream/end?session=...`（可选；断电靠空闲超时）
- 旧接口 `POST /api/capture/audio`（整段 WAV）仍保留
- 文本兜底 `POST /api/capture/text` `{"text":"..."}`
- 列表 / 采纳 / 丢弃：`GET /api/ideas`、`POST /api/ideas/{id}/confirm|discard`

收尾链路：拼 WAV → 讯飞 ASR → DeepSeek 深化（可无 Key 启发式）→ `idea_captured` 事件（睡觉时段静默入库）。

环境变量：

```
LUMIA_ASR_MODE=xfyun
LUMIA_XFYUN_APP_ID=...
LUMIA_XFYUN_API_KEY=...
LUMIA_XFYUN_API_SECRET=...
LUMIA_DEEPSEEK_API_KEY=...
```

## 烧录

```bash
./flash.sh          # 出现 Waiting Reset 时按 RST
# 或：./flash.sh /dev/ttyACM0
```
