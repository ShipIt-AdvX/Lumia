# LiberNovo 椅控 — 涂鸦 T5AI

与 ESP32 版相同：配网 AP `LiberNovo`（密码 `12345678`）、桌宠探活、开漏点动拉伸。

## 接线

| 脚 | 作用 |
|----|------|
| **P7** | 默认拉伸（可在网页改） |
| **P6 / P5 / P4** | 另外三个键 |
| **GND** | 与椅共地 |

空闲 = 高阻；点动/网页按住 = 拉低。

## 烧录

```bash
cd firmware/chair-t5ai
./flash.sh          # Waiting Reset 时按 RST
```

## 配网

1. 手机连 **LiberNovo**，密码 **12345678**（2.4G）
2. 打开 **http://192.168.4.1/**
3. 填 WiFi + 桌宠 IP，测 P7–P4

Lumia：`LUMIA_CHAIR_MODE=relay`，`LUMIA_CHAIR_RELAY_URL=http://<板IP>:8790/stretch`
