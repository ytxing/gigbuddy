# Web UI 方案资料核查

核查日期：2026-08-09（以本日访问到的上游仓库、PyPI 元数据和官方文档为准）。

本文只记录可以回溯到一手资料的事实，并把基于事实的判断和仍需在本仓库验证的事项分开。`textual-serve`、`textual-web` 和 `ttyd` 都能把终端内容显示在浏览器里，但这不等于已经有了原生 Web UI 或浏览器音频链路。

## 结论先行

- `textual-serve` 与本仓库的 `textual==8.2.8` 在依赖解析层面兼容：`textual-serve==1.1.3` 声明 `textual>=0.66.0`，且当前虚拟环境的无写入 `pip install --dry-run` 可以同时解析两者。隔离 target 中用当前仓库入口和 `--no-engine` 建立 WebSocket 会话也已收到 Textual 二进制帧；这仍不等于真实音频引擎和浏览器视觉行为已验收。
- `textual-serve` 的真实模式是“本地 HTTP 页面 + WebSocket + 每个浏览器连接一个 Textual 子进程”，不是把现有 TUI 直接变成 DOM 控件，也不是远程 shell。它适合做临时浏览器镜像或内部试验，不适合直接承担本仓库的原生 Web UI 和低延迟音频输出。
- `textual-web` 仍未在 GitHub 上归档，但 PyPI 最新版 `0.8.0` 仍锁定 `textual<0.44.0`，README 仍标为 beta，且上游最近一次提交和 PyPI 发布都在 2024-08-30。对于本仓库的 Textual 8.2.8，不应选它。
- `ttyd` 是仍在维护的通用 Web 终端，不是 Textual UI 框架。它适合作为远程诊断、开发机命令或 TUI 的访问入口，不适合作为产品级原生 Web UI。
- 如果目标是原生 Web UI，推荐优先评估 `FastAPI + WebSocket + React/Vite`；如果目标是最快做 Python-only 原型，`NiceGUI` 是更直接的候选。两者都需要把 C++ 引擎封装在服务端边界之后，浏览器不能直接调用 macOS CoreAudio 或本地引擎。

## 1. textual-serve 与 Textual 8.2.8

### 已证实事实

1. [Textual 8.2.8 PyPI](https://pypi.org/project/textual/8.2.8/) 的发布元数据确认版本为 `8.2.8`，要求 Python `>=3.9,<4.0`。本仓库 [requirements.txt](../requirements.txt) pin 的也是 `textual==8.2.8`；当前 `.venv` 实际导入版本为 `8.2.8`。
2. [textual-serve 1.1.3 PyPI](https://pypi.org/project/textual-serve/1.1.3/) 的最新版本为 `1.1.3`，要求 Python `>=3.9`，依赖声明为 `textual>=0.66.0`，没有上限。当前仓库执行无写入的 `pip install --dry-run textual==8.2.8 textual-serve==1.1.3` 成功解析；解析器选择了 Textual 8.2.8，并报告只会安装 textual-serve 及其 aiohttp/Jinja2 依赖。
3. Textual 8.2.8 的上游 [web_driver.py](https://github.com/Textualize/textual/blob/v8.2.8/src/textual/drivers/web_driver.py) 仍提供 `WebDriver`。它通过 `TEXTUAL_DRIVER=textual.drivers.web_driver:WebDriver` 运行，并以 `D`（数据）、`M`（元数据）、`P`（二进制打包数据）和长度前缀传输输出；这与 [textual-serve 的 AppService](https://github.com/Textualize/textual-serve/blob/main/src/textual_serve/app_service.py) 读取的协议相符。
4. [textual-serve README](https://github.com/Textualize/textual-serve/blob/main/README.md) 的公开 API 是 `Server(command, host="localhost", port=8000, ...)`，然后调用 `serve()`。默认访问地址是本机 `http://localhost:8000`；`public_url` 只用于生成对外地址。
5. [textual-serve 的 Server 实现](https://github.com/Textualize/textual-serve/blob/main/src/textual_serve/server.py) 暴露根页面、`/ws` WebSocket、`/download/{key}` 下载端点和静态文件。每次浏览器连接 WebSocket 时，它创建一个新的 [AppService](https://github.com/Textualize/textual-serve/blob/main/src/textual_serve/app_service.py)，再通过 `asyncio.create_subprocess_shell(self.command, ...)` 启动一个 Textual 子进程。
6. 子进程环境由 `AppService._build_environment` 设置，包括 `TEXTUAL_DRIVER`、`TEXTUAL_FPS=60`、`TEXTUAL_COLOR_SYSTEM=truecolor`、`TERM_PROGRAM=textual`、初始 `COLUMNS`/`ROWS`。浏览器输入通过 WebSocket 发送为 `stdin`、`resize`、`focus`、`blur`、`ping` 等消息，服务端再封装成协议包写入子进程 stdin。
7. [textual-serve README](https://github.com/Textualize/textual-serve/blob/main/README.md) 明确说明它使用自定义协议，不是把 shell 直接暴露给浏览器；它也说明可以通过不同 CPU 同时运行多个 Textual app 实例。源码实现进一步表明，这个并发单位实际是每个 WebSocket 会话的子进程。

### 推断与建议

- **兼容性判断：依赖兼容和基本 WebSocket 会话已验证，完整运行兼容仍待验收。** 版本约束、当前 `WebDriver` 模块和协议格式都对得上；项目 `.venv` 没有安装这个依赖，但隔离 target 中已用当前仓库入口和 `--no-engine` 建立 WebSocket 会话并收到首帧。由于还没有做真实浏览器视觉回归，也没有在真实 CoreAudio 引擎上运行，因此仍应把它当作候选实验路径，而不是已验证生产依赖。
- 其运行边界不适合直接承载本仓库的音频：它把现有 TUI 的屏幕/键盘/鼠标和 Textual 文件交付映射到浏览器，源码没有本仓库 C++ 引擎的音频流、设备枚举或远程会话管理。`/download/{key}` 是文件下载交付，不是实时 PCM/Web Audio 通道。
- `command` 最终进入 `create_subprocess_shell`，所以只能传入固定、受信任的应用启动命令。README 的“不是 shell”描述的是浏览器协议边界，不是说任意不可信的 `command` 字符串都安全。`Server` 构造器也没有内建账号、权限或会话鉴权参数；不应直接把它暴露到公网。

### 待验证项

- 用真实浏览器验证首屏、键盘、鼠标、resize、退出和多标签页行为；当前只用 aiohttp WebSocket 客户端验证了协议首帧。
- 用本仓库的真实 `tui` 启动路径和真实 CoreAudio 引擎验证 `TEXTUAL_DRIVER` 注入是否与 `tui/app.py` 的启动参数、引擎锁和 `--no-engine` 行为冲突。
- 明确每个 WebSocket 子进程的资源上限、异常退出清理和多个用户并发时是否会争用同一个 `data/`、engine lock 或音频设备。

## 2. textual-web 与 ttyd

### textual-web：已证实事实

- [textual-web GitHub 仓库](https://github.com/Textualize/textual-web) 当前未标记为 archived，描述为“Run TUIs and terminals in your browser”。但仓库 README 自称项目处于 beta 阶段，README 的目标和示例仍围绕发布 Textual app/terminal 的公共 URL。
- [textual-web PyPI 0.8.0](https://pypi.org/project/textual-web/0.8.0/) 的依赖是 `textual>=0.43.0,<0.44.0`；最新文件上传时间为 2024-08-30。仓库 `pyproject.toml` 也保留同一上限，直接排除了本仓库的 Textual 8.2.8。
- README 明确写出终端模式当前只支持 macOS 和 Linux；README 还说明关闭浏览器标签会关闭 Textual app，sessions 属于未来计划。README 的 known problems 包括颜色较多的应用可能出现上游库问题，以及移动端体验不稳定。

### textual-web：判断

它的“未归档”不能当成“适配当前 Textual”。PyPI 的硬上限和 beta/旧发布状态足以构成当前仓库的排除依据。它可以作为历史背景或公共 URL 产品形态的参考，但不应在新 Web UI 方案中继续投入兼容性修复。

### ttyd：已证实事实

- [ttyd GitHub 仓库](https://github.com/tsl0922/ttyd) 当前未归档，仍有近期仓库活动；README 将其定义为“Share your terminal over the web”。最新 GitHub release 页面是 [1.7.7](https://github.com/tsl0922/ttyd/releases/tag/1.7.7)，发布日期为 2024-03-30。
- [ttyd README](https://github.com/tsl0922/ttyd/blob/main/README.md) 说明它基于 libuv 和 WebGL2，支持 CJK/IME、文件传输、SSL、自定义命令、基本认证等，并可跨 macOS、Linux、FreeBSD/OpenBSD、OpenWrt 和 Windows。
- ttyd 的 CLI 接受任意命令和参数；默认只读，`-W/--writable` 才允许浏览器向 TTY 写入；同时提供 `-c` basic auth、`-O` origin check、`-m` max clients、`-S` SSL 等选项。它传输的是终端会话，不是 Textual 的自定义应用协议或原生 DOM 状态。

### ttyd：判断

`ttyd` 适合“远程打开开发机终端/诊断命令/临时访问已有 TUI”。它不能替代原生 Web UI：控件语义、业务状态、权限模型、音频流和设备管理仍要自己实现；把本仓库 TUI 通过 ttyd 暴露出去也会继承终端尺寸、键盘映射和 WebGL terminal 的边界。

## 3. 原生 Web UI 组合的官方依据

### NiceGUI

**已证实事实。** [NiceGUI 上游 README](https://github.com/zauberzeug/nicegui/blob/main/README.md) 将其定义为“Python-based UI framework, which shows up in your web browser”，并列出文件上传、音频/视频、custom routes、per-user pages、键盘输入等能力。README 的架构段明确说明：后端基于 Python/FastAPI，底层是 Starlette/Uvicorn；前端使用 Vue/Quasar；浏览器初次加载后建立持续的 WebSocket，交互事件发送回 Python，后端再批量发送 UI 更新。README 还明确写出使用单个 uvicorn worker。

**推断与建议。** NiceGUI 是最快得到 Python-only 浏览器原型的候选，尤其适合先把 LOCAL/TONE3000、chain/preset、设备状态和 telemetry 做出来。它不是 React/Vite 组合，也不是“自动获得音频实时传输”；若后续需要复杂前端状态、细粒度音频时钟或团队已有 TypeScript 资产，React/Vite 会有更清晰的前后端边界。NiceGUI 的单 worker 事实也意味着多进程扩展、跨用户状态和 engine session 需要额外设计。

**来源：** [NiceGUI PyPI](https://pypi.org/project/nicegui/)、[NiceGUI 官方文档](https://nicegui.io/documentation/)、[NiceGUI 上游 README](https://github.com/zauberzeug/nicegui/blob/main/README.md)。

### FastAPI + WebSocket

**已证实事实。** [FastAPI 官方 WebSocket 文档](https://fastapi.tiangolo.com/advanced/websockets/) 明确支持 WebSocket route，并可在同一连接上接收/发送 text、binary 和 JSON；文档也展示了断开处理、依赖注入和多个浏览器客户端。官方文档建议生产前端使用 React、Vue 或 Angular 等现代框架，并提醒只把连接列表放在进程内存时只能在单进程运行，需要 Redis、PostgreSQL 等外部状态时再扩展。

**推断与建议。** FastAPI + WebSocket 最适合作为本仓库的服务端边界：HTTP 负责 library、chain、preset、设备能力和静态文件，WebSocket 负责 telemetry、engine state、控制确认和可选音频帧。协议要有 session id、revision/ack、断线重连、服务端权限和背压；不能把现有 `data/*.json` 文件直接当成多用户 Web session 的完整状态模型。

**来源：** [FastAPI 官方 WebSocket 文档](https://fastapi.tiangolo.com/advanced/websockets/)、[FastAPI 上游示例源码](https://github.com/fastapi/fastapi/blob/master/docs/en/docs/advanced/websockets.md)。

### React + Vite

**已证实事实。** [React 官方 README](https://github.com/facebook/react/blob/main/README.md) 将 React 定义为构建用户界面的 JavaScript library；[React 官方 Quick Start](https://react.dev/learn) 以组件、事件、状态和条件渲染为核心。 [Vite 官方指南](https://vite.dev/guide/) 将 Vite 定义为现代 Web 项目的构建工具，提供开发服务器、原生 ES module 增强和 HMR，并通过 build 命令输出生产静态资源；开发模式面向现代浏览器。

**推断与建议。** “React/Vite + FastAPI”是工程组合建议，不是 React 或 Vite 官方声称的唯一后端组合：Vite 负责前端开发/构建，React 负责组件和状态，FastAPI 负责 HTTP/WebSocket/API，部署时再由同一个反向代理处理静态资源和 `/ws`。该组合比把 TUI 投影到浏览器更适合本仓库的 library、chain editor、meter、音频播放和权限需求，但需要新增前端构建、API schema 和测试边界。

**来源：** [React 官方文档](https://react.dev/)、[React 上游仓库](https://github.com/facebook/react)、[Vite 官方指南](https://vite.dev/guide/)、[Vite 上游仓库](https://github.com/vitejs/vite)。

### 常用 Web UI 组件库比较

这些库都只能复用 Web 端的组件和交互实现，不能把 Textual 的 CSS 或 widget 直接转换为 DOM。对本项目而言，选择标准应是：表格和搜索效率、抽屉/弹窗、键盘焦点、主题可控性、拖拽排序和实时状态更新，而不是组件数量最多。

| 组合 | 一手资料确认的能力 | 对本项目的判断 |
| --- | --- | --- |
| **shadcn/ui + Radix UI + Tailwind** | [shadcn/ui 文档](https://ui.shadcn.com/docs)提供 Table、Dialog、Drawer、Tabs、Slider 等组件和 dashboard blocks；[Radix Primitives](https://www.radix-ui.com/primitives)提供可访问的无样式交互原语。shadcn/ui 将组件源码加入项目，而不是隐藏在一个黑盒 npm 包里。 | 最适合复用 TUI 的信息架构并重新做视觉语言。主题、密度、焦点态和 Chain 行为都能自己控制；代价是表格、拖拽、状态管理需要组合，首版开发量最大。 |
| **MUI + MUI X Data Grid** | [MUI Material](https://mui.com/material-ui/getting-started/)提供成熟的 Dialog、Drawer、Table、Tabs、Slider 和主题系统；[MUI X Data Grid](https://mui.com/x/react-data-grid/)提供排序、筛选、分页等数据表能力。 | 交付速度最快，适合先做库浏览和 preset CRUD。默认容易产生 Material/admin 味道，需要覆盖 theme 才能接近音频工作台；Data Grid 的高级能力要在实现时单独核对许可。 |
| **Mantine + TanStack Table + dnd-kit** | [Mantine](https://mantine.dev/)提供 Modal、Drawer、Tabs、Table、Slider、Form 和 hooks；[TanStack Table](https://tanstack.com/table/latest)提供无样式表格状态模型；[dnd-kit sortable](https://docs.dndkit.com/presets/sortable)提供可排序拖拽。 | 灵活度和开发速度平衡较好，适合本项目的深色密集工作台。它不像 MUI 那样自带完整 Data Grid，因此需要自己组合表格和 Chain 排序，但不会把视觉锁死。 |
| **Ant Design** | [Ant Design React](https://ant.design/docs/react/introduce)提供 Table、Form、Modal、Drawer、Tabs、Tree、Upload 等企业应用组件。 | 如果目标是管理后台，最快；但当前产品是音色库和音频控制工作台，Ant 默认视觉和较重的表格交互需要较多覆盖，不作为首选。 |
| **Vue + Naive UI / Element Plus** | [Naive UI](https://www.naiveui.com/en-US/os-theme)和 [Element Plus](https://element-plus.org/en-US/)都提供 Table、Dialog、Drawer、Form、Tabs、Slider 等通用组件。 | 技术上完全可行，但仓库当前没有前端工程。除非团队明确偏好 Vue，否则为了这个项目切换到 Vue 不会减少后端适配工作；React/Vite 的生态组合更直接。 |

### 建议的 Web 端组合

按“长期可维护的原生 Web UI”排序，我建议采用：

```text
React + Vite
  ├── shadcn/ui + Radix UI      # 可访问的基础交互和自定义主题
  ├── TanStack Query            # HTTP 数据、缓存、失效和重连
  ├── TanStack Table            # LOCAL/TONE3000 表格、排序、筛选、分页
  ├── dnd-kit                   # 6 个 Chain Slot 的拖拽排序
  ├── Lucide React              # 统一图标
  └── 原生 WebSocket             # 电平、runtime ack、下载进度
FastAPI + Pydantic + Uvicorn    # macOS 上的 API 和本地引擎适配层
```

这里的“组合”比选择一个大而全的 UI 框架更重要：shadcn/ui负责交互原语和基础外观，TanStack Table负责数据表状态，dnd-kit只负责可排序交互，FastAPI负责把现有 Python 能力封装成受控 API。若第一目标是尽快看到页面，可把第一行替换成 `React + Vite + MUI`，等功能稳定后再决定是否迁移视觉层；若第一目标是完全不引入 TypeScript，则用 NiceGUI 做原型，但不建议把它作为最终 Chain 编辑器的边界。

## 4. 浏览器音频与 macOS-only 本地引擎的边界

### 已证实事实

1. [MDN `<audio>` 文档](https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/audio) 说明浏览器可以用 `src`/`<source>` 选择音频资源，也可以把 `MediaStream` 作为流媒体目的地。[MDN Web Audio API 文档](https://developer.mozilla.org/en-US/docs/Web/API/Web_Audio_API) 说明浏览器可以把文件、媒体元素或 `MediaStream` 作为 source，经过 AudioContext/音频节点处理后输出。
2. [MDN autoplay 指南](https://developer.mozilla.org/en-US/docs/Web/Media/Guides/Autoplay) 说明有声媒体的自动播放经常被浏览器阻止；实际产品应在用户手势后启动或恢复音频，并处理 `play()` 失败/被阻止的状态。
3. 若要让浏览器采集麦克风或乐器，[MDN `getUserMedia()`](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia) 规定需要安全上下文和用户许可。浏览器页面不会自动获得访问服务端 Mac 音频设备的权限。
4. 本仓库 README 说明 native engine 当前以 macOS 为目标，PortAudio 通过系统编译器和 CoreAudio 构建；[cpp/realtime_cli.cpp](../cpp/realtime_cli.cpp) 的注释和代码说明输入可以是音频接口或本地 WAV，输出是监听设备，并支持 `--live`、`--managed`、`--level-file` 等本地文件通道。 [tui/live.py](../tui/live.py) 负责 `data/live_chain.json`、控制文件和 engine ready/prepare 等文件协议。

### 边界判断

- **浏览器不能直接调用本地 Mac 引擎。** 这是由两边 API 边界共同推出的工程结论：浏览器的音频源是 URL、媒体元素、MediaStream 或 Web Audio 节点；仓库引擎则是服务端/主机上的 C++ 进程和 CoreAudio 设备。中间必须有 HTTP/WebSocket/其他受控桥接，不能把 C++ 函数、`data/live_chain.json` 路径或 CoreAudio 设备句柄交给前端。
- **“远程控制 Mac 播放”与“浏览器播放处理后的声音”是两种产品语义。** 前者让浏览器发控制命令，声音仍从运行引擎的 Mac 物理设备输出，延迟和音频质量更接近现状；后者需要服务端把处理结果编码或分帧发送到浏览器，再由 `<audio>` 或 Web Audio 播放，必须处理编码格式、时钟、抖动、断线、背压和 autoplay。两者不能用同一个“音频支持”开关混写。
- **浏览器端实时 PCM 是推断方案，不是现有能力。** 可以用 WebSocket 传 binary 音频帧、在 AudioWorklet/Web Audio 中播放，也可以服务端生成浏览器可播放的分段媒体；但帧格式、采样率、声道、序号、时间戳、缓冲目标和丢帧策略都需要本仓库自行定义和测试。现有 `--level-file` 只提供电平 telemetry，不是音频流。
- **部署位置决定音频设备位置。** 如果 FastAPI/engine 在用户自己的 Mac 上，原生 Web UI 可以跨浏览器访问该 Mac，但声音默认仍在 Mac；如果服务部署在 Linux 或云主机，当前 CoreAudio/PortAudio 引擎不能直接复用，必须改造引擎后端或把引擎留在 Mac agent 上。

### 待验证项

- 先决定首版是“浏览器控制、Mac 输出”还是“浏览器接收音频”。建议首版选择前者，避免把低延迟 DSP、网络媒体传输和 UI 重构同时引入。
- 对浏览器播放路线，分别验证 Safari/Chrome/Firefox 的用户手势、音频格式、WebSocket binary 帧、AudioWorklet 调度、后台标签页行为和断线恢复。
- 对跨主机路线，测量控制往返延迟、引擎输出到浏览器的端到端延迟、网络抖动、丢包恢复，以及远程用户是否可以访问本地麦克风；不能用 TUI 的本地播放测试替代这些测试。
- 明确多用户模型：一个全局 Mac 引擎、每用户一个 engine 进程，或浏览器只控制当前本地会话。现有 engine lock 和共享 `data/` 文件协议更接近单主机、单活动会话，需要先做 session ownership 设计。

## 5. 对本仓库的简短建议

1. **不选 `textual-web`。** 当前依赖上限与 Textual 8.2.8 直接冲突。
2. **`textual-serve` 只作为短期验证工具。** 可以验证现有 TUI 是否能通过浏览器访问，但应限定 localhost/可信网络，并把结果标为 TUI mirror，不把它当原生 Web UI 或音频方案。
3. **原生 Web UI 首选 FastAPI + WebSocket + React/Vite + shadcn/ui 组合。** FastAPI 接管 library/chain/preset/engine session，WebSocket 接管状态、telemetry 和控制确认；React/Vite 接管组件化界面，shadcn/Radix、TanStack Table 和 dnd-kit分别解决基础交互、表格和 Chain 排序。
4. **如果要最快做出可用版本，选 React/Vite + MUI；如果坚持 Python-only，选 NiceGUI 做原型。** MUI 的交付速度更快，shadcn 的可控性更好；NiceGUI 要接受后端优先和单 worker 约束。
5. **首版音频建议“Mac 引擎播放，浏览器控制和显示”。** 先复用现有引擎和文件协议，通过服务端适配器做受控 API；浏览器音频流放到第二阶段，单独定义 binary 协议和延迟验收标准。

## 证据与验证记录

| 事项 | 证据 | 本次结论 |
| --- | --- | --- |
| Textual 版本 | [PyPI 8.2.8](https://pypi.org/project/textual/8.2.8/)、[上游 WebDriver](https://github.com/Textualize/textual/blob/v8.2.8/src/textual/drivers/web_driver.py) | 版本和 WebDriver 均存在 |
| textual-serve 版本/依赖 | [PyPI 1.1.3](https://pypi.org/project/textual-serve/1.1.3/)、[pyproject.toml](https://github.com/Textualize/textual-serve/blob/main/pyproject.toml) | `textual>=0.66.0`；解析接受 8.2.8 |
| textual-serve 运行模式 | [README](https://github.com/Textualize/textual-serve/blob/main/README.md)、[Server](https://github.com/Textualize/textual-serve/blob/main/src/textual_serve/server.py)、[AppService](https://github.com/Textualize/textual-serve/blob/main/src/textual_serve/app_service.py) | 本地 HTTP/WebSocket，每连接一个子进程 |
| textual-web 状态 | [GitHub](https://github.com/Textualize/textual-web)、[PyPI 0.8.0](https://pypi.org/project/textual-web/0.8.0/)、[README](https://github.com/Textualize/textual-web/blob/main/README.md) | 未归档但 beta/旧依赖，不能用于 Textual 8.2.8 |
| ttyd 边界 | [GitHub README](https://github.com/tsl0922/ttyd/blob/main/README.md)、[release 1.7.7](https://github.com/tsl0922/ttyd/releases/tag/1.7.7) | 通用终端共享，不是原生 Web UI |
| NiceGUI | [上游 README](https://github.com/zauberzeug/nicegui/blob/main/README.md)、[文档](https://nicegui.io/documentation/)、[PyPI](https://pypi.org/project/nicegui/) | Python-only 原型候选，FastAPI/Vue/Quasar/WebSocket，单 worker |
| FastAPI WebSocket | [官方文档](https://fastapi.tiangolo.com/advanced/websockets/) | 支持 text/binary/JSON 与多客户端，但状态扩展需单独设计 |
| React/Vite | [React 文档](https://react.dev/learn)、[Vite 指南](https://vite.dev/guide/) | 适合原生组件化前端；与 FastAPI 的组合是工程建议 |
| 浏览器音频 | [MDN audio](https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/audio)、[Web Audio](https://developer.mozilla.org/en-US/docs/Web/API/Web_Audio_API)、[autoplay](https://developer.mozilla.org/en-US/docs/Web/Media/Guides/Autoplay)、[getUserMedia](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia) | 可播放 URL/媒体流/AudioContext，但有权限和 autoplay 边界 |
| 本仓库引擎 | [README](../README.md)、[realtime_cli.cpp](../cpp/realtime_cli.cpp)、[live.py](../tui/live.py) | macOS/CoreAudio 本地引擎；现有 telemetry/控制通道不是浏览器音频流 |
| 通用 Web UI 组件 | [shadcn/ui](https://ui.shadcn.com/docs)、[MUI](https://mui.com/material-ui/getting-started/)、[Mantine](https://mantine.dev/)、[Ant Design](https://ant.design/docs/react/introduce)、[Naive UI](https://www.naiveui.com/en-US/os-theme)、[Element Plus](https://element-plus.org/en-US/) | 都能提供原生 Web 控件；shadcn 可控性最好，MUI 交付最快，Mantine 平衡，Ant 更偏后台，Vue 方案只在团队偏好明确时采用 |

本次本地验证只读检查：`.venv` 中 Textual 导入版本为 `8.2.8`；`pip install --dry-run textual==8.2.8 textual-serve==1.1.3` 解析成功且未写入项目环境；在隔离 target 安装 `textual-serve==1.1.3` 后，以当前仓库的 `.venv/bin/python -m tui --no-engine` 作为命令启动服务，HTTP 根页面返回 `200`，WebSocket 连接收到 Textual 二进制帧。该探针未验证真实 CoreAudio 引擎、浏览器视觉回归、多标签页资源争用和公网安全，因此这些仍是实现前的待验证项。
