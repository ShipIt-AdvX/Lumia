# Lumia 地瓜派桌宠（Minecraft 花斑猫）

电脑端 Electron 猫（`electron/pet.html`）**不要改**。本目录是地瓜派上的 PyQt6 桌宠。

## 导演行为（连电脑大脑）

轮询 `GET {brain_url}/api/pet/state`：

| action | 行为 |
|--------|------|
| `sleep` | 强制睡觉；气泡催睡；点击会生气 |
| `meal` | 停下来看你，气泡催吃饭 |
| `sit_away` | 加快走动并缩小（走远）；电脑侧可抓鼠标 |
| `idle` | 正常待机 |

配置 `~/.config/lumia-pet/config.json`：

```json
{
  "walking_enabled": true,
  "autostart": true,
  "clean_mode": true,
  "brain_url": "http://10.31.114.110:8787"
}
```

## 启动

```bash
cd ~/lumia-desktopPet
.venv/bin/python main.py --debug
```
