# LiberNovo 椅控（ESP32 继电器/开漏并联）

并联清闲椅扶手键：空闲高阻，点动时 GPIO **拉低** 模拟按键。  
电脑 / OrangePi：`LUMIA_CHAIR_MODE=relay`，`LUMIA_CHAIR_RELAY_URL=http://<ESP_IP>:8790/stretch`。

## 板型

目标板：**Waveshare ESP32-S3-Nano**（ESP32-S3R8，兼容 Arduino Nano ESP32）。

- 芯片 / 引脚：按 **Arduino Nano ESP32** 选板即可  
- USB：本板是 Espressif `303a:1001`（不是官方 Arduino `2341:0070`）  
- 烧录：用 **esptool**（`platformio.ini` 已设 `upload_protocol = esptool`），不要用官方 DFU  

```bash
cd firmware/chair-relay
pio run -t upload -e esp32_s3_nano
```

## 接线（你忘了的那套）

| ESP 丝印 | GPIO | 作用 | 接到哪里 |
|----------|------|------|----------|
| **A7** | 14 | 默认「拉伸」 | 椅对应按键信号端（按下会接地的那根） |
| **A6** | 13 | 备用键 2 | 同上 |
| **A5** | 12 | 备用键 3 | 同上 |
| **A4** | 11 | 备用键 4 | 同上 |
| **GND** | — | 共地 | 椅 GND / 按键地 |

要点：

- **并联、不剪断**原厂线：ESP 脚 ↔ 键信号，GND ↔ 地。
- 空闲 = `INPUT`（悬空），不抢原厂键；点动 / 网页按住测 = `OUTPUT LOW`。
- 电压请确认椅键侧是 **≤3.3V 逻辑**；若是 5V 或未知，中间加光耦/NMOS，不要直驱。
- **经典 ESP32** 上 Arduino 的 A6/A7 常是 GPIO34/35，**只能输入**。若编译板型是经典 ESP32，在 `config.h` 里改成可输出脚，或换 **ESP32-S3**（Waveshare 多数 A4–A6 为 GPIO5–7）。

可选：复制 `config.h.example` → `config.h` 覆盖引脚。

## 配网流程

1. 上电读 NVS；有 WiFi 就先连。
2. 连上后请求 `http://<桌宠IP>:<端口>/api/health`。
3. **WiFi 失败或桌宠不可达** → 开 SoftAP：
   - SSID：`LiberNovo`（无密码）
   - 固定页： **http://192.168.4.1/**
4. 手机连上 LiberNovo，浏览器打开上面地址：
   - 填 WiFi、桌宠 IP（OrangePi）、端口（默认 8787）
   - 选哪个脚当「拉伸」
   - **按住测 A7–A4**（按住拉低，松开悬空）
5. 保存后重启；WiFi + 桌宠都正常后会自动关 AP。之后周期探活，挂了会再开 AP。

STA 下也可打开 `http://<ESP局域网IP>/` 改配置。  
mDNS（若支持）：`libernovo-chair.local`。

## 烧录

Arduino IDE / `arduino-cli`：

- 板型：你的 Waveshare ESP32 / ESP32-S3
- 打开 `firmware/chair-relay/chair-relay.ino`
- 串口 115200 看日志

## OrangePi / Lumia 侧

```bash
# .env
LUMIA_CHAIR_MODE=relay
LUMIA_CHAIR_RELAY_URL=http://192.168.x.x:8790/stretch
```

手动测：

```bash
curl -X POST http://<ESP_IP>:8790/stretch
```
