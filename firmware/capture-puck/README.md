# 灵感盒 · 涂鸦 T5AI + 电脑讯飞 ASR

按住 **P20↔GND** 录音 → 松开上传 WAV → 电脑 `POST /api/capture/audio` → **讯飞**转写 → 板端清空缓冲。

## 接线

| 用途 | 针脚 |
|------|------|
| 按钮 | P20 ↔ GND |
| LED | **P9**（录音常亮 → 发送慢闪 → 成功快闪） |
| 麦 | 板载，无需接线 |

## 配网

与椅控相同流程：

1. 手机连 **LumiaPuck**，密码 **12345678**（2.4G）
2. 打开 **http://192.168.4.1/**
3. 填 WiFi SSID/密码 + 桌宠/电脑 IP（端口默认 8787）→ 保存并应用

WiFi 与桌宠都正常后 AP 会自动关掉；连不上会再开热点。

可选：`config.h` 里的 `WIFI_*` / `LUMIA_*` 仅作首次种子。

## 电脑侧

```
LUMIA_ASR_MODE=xfyun
LUMIA_XFYUN_APP_ID=...
LUMIA_XFYUN_API_KEY=...
LUMIA_XFYUN_API_SECRET=...
```

## 烧录

```bash
./flash.sh          # 出现 Waiting Reset 时按 RST
# 或：./flash.sh /dev/ttyACM0
```
