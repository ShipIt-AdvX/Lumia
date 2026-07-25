# electron/config.json 配置说明

Electron 外壳的全部可调参数。修改后需重启 Electron 生效。

## backend — 后端连接与拉起

| 键 | 默认值 | 说明 |
| --- | --- | --- |
| `baseUrl` | `http://127.0.0.1:8787` | 后端 API 地址，需与 `backend/config.json` 的端口一致 |
| `spawn` | `false` | `true` 时 Electron 启动会自动拉起后端进程；`false` 表示后端由用户手动运行 |
| `command` | `python` | `spawn=true` 时用于启动后端的命令 |
| `args` | `["run.py"]` | 启动后端命令的参数列表 |
| `cwd` | `../backend` | 启动后端时的工作目录（相对 `electron/` 目录） |

## popup — 事件弹窗与倒计时浮窗

| 键 | 默认值 | 说明 |
| --- | --- | --- |
| `width` | `340` | 弹窗与倒计时浮窗的宽度（px） |
| `height` | `150` | 弹窗初始高度（px），实际高度随内容自适应（64px ~ 工作区高度 60%） |
| `marginRight` | `20` | 弹窗距屏幕右缘的间距（px） |
| `marginBottom` | `20` | 弹窗距屏幕下缘的间距（px） |
| `autoDismissMs` | `12000` | 弹窗自动消失时间（毫秒） |

## float — 浮窗通用行为

| 键 | 默认值 | 说明 |
| --- | --- | --- |
| `marginTop` | `16` | 浮窗槽位距工作区顶部的间距（px） |
| `marginRight` | `16` | 浮窗距屏幕边缘的间距（px，左右侧通用） |
| `gap` | `12` | 多个浮窗纵向堆叠时的间隔（px） |
| `handleWidth` | `8` | 浮窗收进屏幕边缘后露出的把手宽度（px） |
| `titleHeight` | `40` | 浮窗竖直收起（手风琴）后仅保留的标题栏高度（px） |
| `autoRetractMs` | `5000` | 浮窗无操作后自动收起到屏幕边缘的等待时间（毫秒） |
| `animMs` | `260` | 浮窗滑入/滑出/收展等动画时长（毫秒） |
| `fps` | `60` | 浮窗动画帧率 |

## 顶层

| 键 | 默认值 | 说明 |
| --- | --- | --- |
| `pollIntervalMs` | `2000` | 向后端轮询事件（`/api/events/poll`）的间隔（毫秒） |
