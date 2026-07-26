# 分工入口

不了解项目的同学：**只读自己那一份任务书**。

| 角色 | 文档 | 一句话 |
|------|------|--------|
| 可视化（看得见的提醒/主控/角色） | **[DEV_VISIBLE.md](DEV_VISIBLE.md)** | soft→侧边→全屏；**Linux + Windows** 桌面与用屏适配 |
| 不可见（服务/策略/硬件） | **[DEV_INVISIBLE.md](DEV_INVISIBLE.md)** | 升级状态机与软硬 API（OS 无关） |

两人如何对接：各自文档第 **§5**（契约 [PROTOCOL.md](PROTOCOL.md)）。  
平台边界：UI/FocusSource → 可视化；API 形状不变 → 不可见。

```bash
cd lumia && PYTHONPATH=. python -m modules.list
```
