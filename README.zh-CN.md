# GigBuddy 🎸 — 一站式 NAM 音色管理器

[![macOS](https://img.shields.io/badge/platform-macOS%20only-000000.svg)](https://github.com/ytxing/gigbuddy)
[![NAM A2](https://img.shields.io/badge/NAM-A2%20architecture-e59a3c.svg)](https://www.tone3000.com/blog/introducing-neural-amp-modeler-nam-architecture-2-a2)
[![Release](https://img.shields.io/github/v/release/ytxing/gigbuddy)](https://github.com/ytxing/gigbuddy/releases)
[![License](https://img.shields.io/github/license/ytxing/gigbuddy)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.12-3776AB.svg)](https://www.python.org)
[![Stars](https://img.shields.io/github/stars/ytxing/gigbuddy)](https://github.com/ytxing/gigbuddy)

*找到音色 · 即刻演奏*

*v1.1.0 · 2026-08-10*

在终端粘贴一行命令，即可完成下载、安装与初始化：

```
curl -sSL https://raw.githubusercontent.com/ytxing/gigbuddy/v1.1.0/scripts/install.sh | bash
```

![GigBuddy 安装](docs/screenshots/gigbuddy.gif)

GigBuddy 是一个基于 [NAM（Neural Amp Modeler）](https://www.tone3000.com/guides/nam-a2-the-complete-guide)
的实时音色工作台。它完整支持 **A2 架构**——目前还原度最高的音箱捕获
技术，在 TONE3000 的 1000 人[盲听测试](https://www.tone3000.com/guides/nam-a2-the-complete-guide#amp-modeler-blind-listening-test)
中表现优于 Neural DSP、ToneX 与 Line 6 Proxy。

整个工作台由三部分组成：浏览 TONE3000 音色库的界面、实时运行的 NAM
引擎，以及存放音色与 preset 的 SQLite 本地库。外部 AI 也可以通过稳定的
命令行接口直接操作这一切。

使用流程很直接：找到一个音色，用干音或自己的吉他试听，把音色链调整到
满意，然后存下来。下载的声音都存在本地，引擎随时响应，不依赖网络。

**开源立场**：纯 API 数据源，不依赖任何本地音色库，核心代码全部采用
MIT 许可。

## 为什么选 GigBuddy

**找音色不再翻网站。** 整个 TONE3000 目录都可以在应用内直接搜索——按
关键词、作者、标签、设备、类型或热度筛选，看中即可试听，不用去网站上
一页页翻、一个个下载。

**音色可以并排对比。** 同一段干音试听不同捕获，在链里换模型、换箱体
即时 A/B，凭耳朵决定，不靠记忆。

**拿自己的吉他试。** 接上乐器直接弹，音色效果当场可听；也可以循环播放
干音录音，腾出手来从容筛选。

**音色链像效果器板一样灵活。** 最多六个 Slot 的有序链，可放
[NAM](https://www.tone3000.com/guides/neural-amp-modeler#what-is-nam)
捕获或[箱体 IR](https://www.tone3000.com/guides/neural-amp-modeler#what-s-the-difference-between-nam-and-ir-s)。
添加、移除、重排、bypass、恢复随时进行，引擎全程不停声。

**存下整套设备，而不只是设置。** preset 保存整条链（模型引用、参数、
备注），一键调回，可覆盖当前 preset，也可另存新名。

**一站式管理。** 音色、模型、文件、下载状态、preset 集中在一个可搜索的
本地库里，安装、卸载、批量选择、编辑都不必离开工作台。

**AI 也能帮你搭音色。** GigBuddy 为 AI 助手内置了 skill——说一句"最多人
喜欢的 Fender 音箱"或"下载最多的贝斯过载"，它就能帮你搜索筛选、搭建或
优化 preset。

**界面可以换成你的风格。** 内置六套灵感源自吉他音箱的主题，按 `t` 键在
orange-tolex、tweed-brass、diamond-noir、blackface-silver、
british-green、surf-cream 之间循环。

![浏览 TONE3000 — 实时搜索、热度排序、类型过滤](docs/screenshots/tone3000-browse.png)

## 主要特性

**音色链全面升级。** 原来固定的 AMP/CAB 两段，现在是最多六个 Slot 的
有序链，每个 Slot 可放任意受支持的 NAM 模型或 `.wav` IR，信号路径完全
按你的搭建方式排列。任意 Slot 都能单独关闭、随时重开，链的其余部分照常
播放；重启应用后设置保持原样。

**每个 Slot 都能单独控电平。** 每个 Slot 都有 -24 到 +24 dB 的输入与输出
trim。引擎驱动的 `CAL` 可以为 NAM Slot 推荐输出 trim；如果推荐值超出安全
范围，界面会明确显示已截断，并把最终结果随 preset 保存。

**每个用户使用自己的 TONE3000 账号。** 通过系统浏览器完成 OAuth 2.0 +
PKCE 登录。顶栏显示当前登录状态，远程库需要认证时直接提供登录按钮，TUI
和 CLI 都支持退出登录并清除本地会话。

**实时操作不会被晚到的任务打乱。** 远程模型加载、音色包刷新、preset
切换与干音播放都移到 Textual 事件循环之外；网络或引擎回复晚到时，焦点、
选择和用户最后一次操作仍保持在原位置。

**本地库记住你的工作。** 导入的音色、模型信息、本地文件、下载状态与
preset 统一存放，随时可搜索。LOCAL、TONE3000、TOP CREATORS 三个视图
共用同一套实时搜索、排序、类型过滤与音色包安装流程。已弃用的 **A1**
架构在下载、浏览、显示各环节全部过滤，库里只会出现 A2 与 IR 文件。

**内置 20 套精选音色。** 采用品牌 + 型号 + 箱体的命名（Fender、Vox、
Marshall、Ampeg、Gallien-Krueger、Hartke、Darkglass），全部来自高下载量、
认证作者的捕获。经典过载单块（Ibanez TS9 / TS808、JHS Morning Glory、
Boss BD-2 / DS-1 / TB-2w）和法兹链（Big Muff → Marshall Major、Fuzz
Face → Plexi、ToneBender → Plexi）也带着经典旋钮设置载入 preset 槽；
可选音色塑形 IR 默认预载但保持关闭，想用随时开。

**练习与试听很方便。** INPUT 行可以播放、暂停、停止、循环干音吉他贝斯
录音，键盘（`space`/`s`/`l`）和行上的 STOP / LOOP / PLAY 按钮都支持。
AUDIO 面板把电平、静音、设备、缓冲、采样率、延迟控制在手边。

**切换音色无咔哒。** 引擎以等功率曲线交叉淡化新旧链，bypass、恢复、
换模型都不会在长音上出现咔哒或信号跌落。

**界面各就各位。** 焦点、选择、安装、preset 编辑、破坏性操作都留在
各自窗格内，互不干扰。

## 开始使用

上面的一行安装会把 GigBuddy 放进 `~/.local/share/gigbuddy`，并把
`gigbuddy` 命令链接到 `~/.local/bin`。直接运行 `gigbuddy`（不带参数）
打开 TUI，带子命令（`tone`、`chain`、`preset`）则走 CLI。交互式安装时
可以选择安装位置：`.` 表示当前目录，也可输入任意路径——想让自己的项目
文件夹内直接可用捆绑的 agent skill 时很方便；设置 `GIGBUDDY_HOME`
可跳过询问。卸载同样一行完成：

```
curl -sSL https://raw.githubusercontent.com/ytxing/gigbuddy/v1.1.0/scripts/uninstall.sh | bash
```

独立卸载脚本会删除本地安装、生成的运行时文件和持久化的 TONE3000 会话。
如果只想删除运行时、保留下载的音色、本地数据和登录状态，可使用
`--keep-data` 参数：

```
curl -sSL https://raw.githubusercontent.com/ytxing/gigbuddy/v1.1.0/scripts/uninstall.sh | bash -s -- --keep-data
```

从源码检出开始：

```
# 创建 Python 环境、本地音色库、内置 preset、官方干音与实时引擎。
./install.sh

# 暂不编译引擎、仅浏览界面时，可加该参数。
./install.sh --no-engine --starter-dry

# 启动 GigBuddy。
.venv/bin/python -m tui
```

默认安装会准备内置 preset 目录所需的全部模型，以及 34 个官方 TONE3000
干音 WAV。重复运行安全：已有数据库记录与非空文件会直接复用。
`--starter-dry` 只下载前十个常用吉他样本。

不接音频后端、仅查看界面：

```
.venv/bin/python -m tui --no-engine
```

原生引擎目前面向 macOS：PortAudio 19.7.0 直接从源码编译进安装目录，无需
任何包管理器，使用系统编译器和 CoreAudio 框架。缺少这些工具时，TUI 与
音色库在 `--no-engine` 下依然可用。

如需彻底清空本地数据、Python 环境与构建产物、重新开始，运行一行卸载：

```
./uninstall.sh
```

## 第一次上手

1. 打开 **PRESETS** 加载一个内置音色，或打开 **TONE3000** 搜索
   `super reverb`、`vox ac30`、`darkglass` 这类声音。如果远程视图提示认证，
   点击顶栏的 `log in`，或运行 `gigbuddy tone login`，然后在浏览器完成
   TONE3000 登录。
2. 聚焦 **INPUT**，按 `enter` 选择一段干音吉他贝斯录音，用 `space`
   播放、`s` 停止、`l` 循环。
3. 选中一个 Slot，在它的音色包视图里挑选想要的模型或 IR，按 `enter`
   载入。
4. 用 `↑`/`↓` 对比不同版本，`alt+↑`/`alt+↓` 移动阶段；对激活阶段按
   `enter` 即可 bypass 或恢复。
5. 在 **PRESETS** 里保存：`s` 更新当前 preset，`n` 另存新名并附备注。

全局命令面板（`ctrl+p`）可以聚焦 Presets、打开音频设置、切换主题或查找
主要命令，不必记忆快捷键。

## 寻找与保留音色

GigBuddy 提供三种开始方式：

- **LOCAL**：本地下载的音色库。保留完整 TONE3000 元数据，可查看已安装
  内容，支持音色或模型粒度的安装/卸载。
- **TONE3000**：公共目录的实时搜索，提供热度结果、排序、类型过滤与
  音色包级模型选择。
- **TOP CREATORS**：官方创作者排行榜，可查看创作者主页并直接进入该
  创作者的全部音色。

搜索语法可简单可精确：

```
super reverb @tone3000
author:tone3000 tag:clean super reverb
make:"Two Rock Traditional Clean" @coretonecaptures
tag:"edge of breakup" marshall
```

先搜索，再导入 TONE3000 返回的真实 ID：

```
bin/gigbuddy tone search "fender super reverb"
bin/gigbuddy tone import <tone-id>
bin/gigbuddy tone list
bin/gigbuddy tone show <tone-id>
```

导入操作是幂等的。文件存放在 `data/tones/`，元数据在 `data/gigbuddy.db`
中随时可查。NAM 捕获使用 `.nam`，箱体及其他 IR 资产使用 `.wav`。

## 搭建自己的设备

每个捕获都建立在 [NAM（Neural Amp Modeler）](https://www.tone3000.com/guides/neural-amp-modeler#what-is-nam)
之上。NAM 是目前社区里还原度最高的音箱捕获技术——每个音色都是真实音箱
经真实麦克风录制，是捕获而非近似模拟。GigBuddy 围绕 TONE3000 的下一代
[A2 架构](https://www.tone3000.com/blog/introducing-neural-amp-modeler-nam-architecture-2-a2)
打造，TONE3000 称之为"历史上最准确、最好听的音箱建模技术"，并在 1000
人[盲听测试](https://www.tone3000.com/blog/introducing-neural-amp-modeler-nam-architecture-2-a2#amp-modeler-blind-listening-test)
中领先 Neural DSP、ToneX 与 Line 6 Proxy。NAM 捕获与箱体 IR 在设备中
角色不同，[区别详见此处](https://www.tone3000.com/guides/neural-amp-modeler#what-s-the-difference-between-nam-and-ir-s)。

信号链刻意保持简单：

```
INPUT → gain → Slot 1 → Slot 2 → … → Slot 6 → master → OUTPUT
```

空 Slot 是安全的直通。Slot 顺序即信号顺序，同一模型可按需重复使用以
叠加阶段。gain、master 与 NAM quality 是链级控制，可实时调节；mute
是实时输出控制，不会抹除已保存的 master 设置。

preset 保存的是整套设备——链、模型引用、参数与备注，一键调回，支持
覆盖当前 preset 或另存新名。TUI 与 CLI 共用同一套 preset：存储稳定的
模型引用，加载时再解析本地文件路径，所以整理音色库不会弄坏已保存的
设备。旧的扁平 `model`/`ir` preset 仍可读取，使用时会自动规范化为
Slot 格式。

## 可选：自动化

GigBuddy 完全可以在 TUI 里操作。需要脚本或外部代理驱动时，CLI 暴露了
同样的音色库、链与 preset 操作：

```
gigbuddy tone search "marshall plexi" --json
gigbuddy tone import <tone-id>
gigbuddy tone login
gigbuddy tone logout
gigbuddy preset list
gigbuddy preset load <name>
gigbuddy chain get
gigbuddy chain set '{"slots": [], "gain": 1.0, "master": 1.0}'
```

CLI 与 TUI 共用本地数据库和 `data/live_chain.json`。引擎监视该文件，
链一变即热切换，无需重启。内置的代理工作流位于
`.agent/skills/gigbuddy/SKILL.md`，`.claude/skills/gigbuddy` 保留为兼容入口。
它先查本地数据，只使用真实 TONE3000 音色 ID 和导入后确认过的文件路径，
并拒绝不支持的 A1 或非引擎格式。

### Agent skill

这份 skill 会把自然语言的吉他或贝斯需求转成可追溯的搜索、Model 选择、
导入、chain 更新或 preset 操作。它完整记录代理可用的 CLI，区分 Tone 音色包
与其中的具体 Model，连接到 chain 前检查本地文件，并把创作者元数据与推断
分开报告。远程操作使用当前用户自己的 OAuth 会话，并遵守公开的 TONE3000
请求与下载边界；不支持共享凭据、镜像目录或后台批量下载。完整工作流见
`.agent/skills/gigbuddy/SKILL.md`。

## TONE3000 集成说明

GigBuddy 以桌面客户端方式连接 TONE3000。每个用户都通过官方 OAuth 2.0 +
PKCE 流程登录自己的 TONE3000 账号；应用不需要服务器 Secret Key，也不共用
一个账号。Access Token 与 Refresh Token 存在本机用户配置目录中，并使用
严格的文件权限；点击 `log out` 会删除持久化会话。

集成遵循 [TONE3000 API 文档](https://www.tone3000.com/api) 和
[API 使用条款](https://www.tone3000.com/api/terms)：

- 请求使用 Bearer Token，登录过期时刷新会话；请求之间至少间隔 0.6 秒，
  对应官方默认的每分钟 100 次限制，服务返回 HTTP 429 时遵守
  `Retry-After`；
- 远程列表使用有界分页；模型文件只在用户明确导入、从 Slot 选择或执行
  用户主动请求的 starter bootstrap 时下载，不在后台镜像整个目录；
- 本地库保留创作者名称、音色元数据和来源平台，下载文件仍受创作者选择的
  许可约束；
- 桌面客户端不会替用户代理、汇聚或向其他用户发布某个 TONE3000 账号的
  音色库。若要基于 GigBuddy 构建托管服务或商业产品，请先阅读当前 API
  条款并确认适用范围。

TONE3000 的 API 政策与 endpoint 范围可能变化。OAuth 流程、免费层范围、
速率限制、署名和商业要求，以官方文档为准。当前条款特别区分了免费非商业
集成与完整 API/商业集成；发布衍生集成前，应重新核对条款列出的 OAuth prompt
流程和有界列表 endpoint 范围。

## 实用信息

- **推荐使用 truecolor 终端以获得最佳显示效果** —— iTerm2、Kitty、
  WezTerm、Alacritty、Warp、Ghostty 都能完整呈现吉他箱体主题；macOS
  自带的 Terminal.app 只支持 256 色，会自动降级到兼容主题。想强制使用
  完整主题，运行 `TEXTUAL_COLOR_SYSTEM=truecolor gigbuddy`。
- `--no-engine` 是浏览编辑模式；实时音频与电平遥测需要原生引擎和可用
  的音频设备。
- TONE3000 搜索、创作者视图、模型详情和下载需要网络与有效登录；LOCAL
  本地库和已保存的 preset 仍可离线使用。
- 核心代码采用 MIT 许可。运行时使用 NeuralAudio、NAM Core、RTNeural、
  Eigen、PortAudio、Textual，各自许可见[依赖](#依赖)。

## 后续规划

- 本地 VST3 效果器与效果器板阶段
- 更平滑的交叉淡化切换
- 渲染与参考音色的自动对比评估
- 更多音频输出方式

## 依赖

版本固定（v1.1.0）。升级时需同步更新 `requirements.txt` 与 `install.sh`
中的 NeuralAudio commit。

**Python 运行时**（`requirements.txt`）：

| 包 | 版本 | 许可 | 作用 |
|---|---|---|---|
| textual | 8.2.8 | MIT | TUI 框架 |
| numpy | 2.5.1 | BSD-3-Clause | 离线渲染 |

**原生引擎**（由 `install.sh` 获取，仓库内固定版本）：

| 组件 | 版本 | 许可 | 作用 |
|---|---|---|---|
| NeuralAudio | commit `49100f9` | MIT | NAM 推理运行时 |
| NAM Core | （NeuralAudio 子模块） | MIT | 音箱模型 DSP |
| RTNeural | （NeuralAudio 子模块） | BSD-3-Clause | 神经网络推理 |
| Eigen | 3.4.0 | MPL-2.0 | 线性代数（已为 NAM 修补） |
| math_approx | （NeuralAudio 依赖） | MIT | 快速数学 |
| PortAudio | 19.7.0（源码编译） | MIT-like | 音频 I/O |

**工具链**：Xcode Command Line Tools（clang++/git/make）、uv（缺失时自动
下载，负责 Python 3.12 与虚拟环境）。

## 已知限制

- **创作者粉丝/关注数不显示。** TONE3000 公共 API 未提供关注数据，主页
  只显示官方可查的统计。
- **首次搜索、创作者视图与下载需要联网。** 音色导入后，文件与元数据
  即可在本地使用。
- **TOP CREATORS 统计来自服务端。** 音色、下载、收藏与模型数取自
  TONE3000 官方排行榜，之后不会刷新。

## 许可

MIT。依赖：NeuralAudio（MIT）、NAM Core（MIT）、RTNeural（BSD-3）、
Eigen（MPL-2）、math_approx（MIT）、PortAudio（MIT-like）、Textual（MIT）。
固定版本完整清单见[依赖](#依赖)。

## 延伸阅读

- [交互指南](docs/ui-interaction-spec-v0.2.md)
- [音色链协议](docs/adr/0001-slots-chain-protocol.md)
- [音色库结构](docs/library-schema.md)
