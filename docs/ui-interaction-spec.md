# GigBuddy UI 交互与视觉规格（v0.1.8）

> 状态：**FROZEN**（2026-08-05，规格 v0.1.8）。本次修订固定 view tab 键位、SearchBar 轨道宽度和 Type-only 表头过滤；本版本作为 v0.1 实现与验收基线。
> 本文件定义 GigBuddy v0.1 的目标 UI 行为与视觉语义，是交互验收的设计基准。
> 实现、测试或历史需求与本文冲突时，应先确认是否变更设计；未变更设计时，冲突视为实现缺陷。
> 需求来源：`.remember/backlog.md`、`.remember/feature-baseline.md` 中 REQ-001~041 的最终口径。
> REQ-032 多 Slot 属于下一阶段，不在 v0.1 范围内。
> 冻结后不得随实现临时改写目标；确需变更时，必须先由用户明确解冻、记录变更理由并升级规格版本，再修改实现和验收。

## 1. 设计原则

### 1.1 工作台，而非展示页

- 第一屏就是完整工作区，不增加欢迎页、功能介绍卡或装饰性区域。
- 信息密度可以高，但层级必须稳定：主面板、当前焦点、当前选择和当前状态一眼可分。
- 高频动作优先键盘和直接点击；低频、危险或需要确认的动作进入模态。
- 状态变化不得造成控件、标题、提示动作或光标位置跳动。

### 1.2 输入等价

- 键盘、鼠标和右下角 action token 是同一动作的不同入口，结果必须一致。
- 单字母键按实际大小写显示：`g` 与 `G` 是两个不同键位；普通动作词统一小写。
- 特殊键统一写作 `enter`、`esc`、`space`、`ctrl+s`、`ctrl+z`。
- 可点击 token 必须有 hover 反馈，但 hover 不得改变占位尺寸。
- 双击只承载稳定、明确的快捷动作，不与单击的选择语义冲突。

### 1.3 底部提示带

所有面板和模态的底部只使用一个右下角提示带，不再划分“左状态区”和“右动作区”。提示带是一个连续的、右对齐的 token 序列：变化内容排在左边，稳定动作排在最右边。

```text
                                  12/80 · ↓ more · enter detail
              3 sel · installing 2/3 · i install · u uninstall · esc back
```

- 整个提示带锚定在面板内边框的右下角；状态变化只从动作后缀的左侧增长、收缩或消失，稳定动作仍贴右。
- 状态可包含数量、选择、来源、query、加载、进度、dirty、播放或错误；普通状态文字不可点击。明确列出的 action token 才可点击。
- 宽度按当前 widget 的实际 `region` 计算。稳定 action 先尝试完整显示，空间不足时缩写说明词或隐藏低优先级完整 token；动态状态使用剩余宽度省略。`esc`、当前主要确认和当前页面核心动作必须保留。
- action token 必须整体显示或整体隐藏，禁止把 `l loop` 渲染成 `l l…` 这类半截 token；键盘、点击和双击入口仍保持同一动作语义。
- 状态为空时直接显示动作后缀，不插入左侧占位，也不把动作单独改成左对齐。

## 2. 视觉系统

### 2.1 视觉方向

GigBuddy 是深色、克制、面向重复操作的音频工作台。视觉联想来自音箱面板和暖色指示灯，但不使用拟物旋钮、纹理、渐变光斑或装饰性动画。

- 默认主题保持深暖色、低亮度；其他主题可以使用不同色相，但必须保持相同层级和状态语义。
- 品牌强调色只用于焦点、主层级和可交互位置，不能铺满页面。
- active/success 绿、bypass/error 红、idle/empty 灰是跨主题固定状态色；warning 和其他界面颜色由当前主题提供。
- 颜色不是唯一状态信号；必须同时使用文字、符号或边框。

### 2.2 颜色语义

界面结构色全部来自当前主题：

| Theme Token | 唯一职责 | 禁止用途 |
|---|---|---|
| `$background` | 页面底色、反色文字底 | 大面积选中态 |
| `$surface` | Header、Footer、面板内部底色 | 状态提示 |
| `$surface-hover` | 行 hover 和局部可点击区域 | 当前选择或 active 状态 |
| `$primary` | 当前页面边框、主标题、当前光标 | warning/error |
| `$accent` | 编辑光标、输入焦点、可执行强调 | 普通正文 |
| `$secondary` | 非当前页面光标、滚动条、次级边界 | 成功状态 |
| `$warning` | 部分完成、需注意、接近削波 | 普通提示 |
| `$text` | 正文和主要数据 | disabled 文本 |
| `$text-muted` | 标签、单位、说明、非主信息 | 错误和关键状态 |
| `$text-disabled` | 不可用、未下载、占位 | 可点击动作 |

三个状态色是唯一允许跨主题硬编码的颜色：

| Fixed Token | 固定值 | 语义 | 使用范围 |
|---|---:|---|---|
| `$success` | `#8fb573` | active、success | 工作节点、已下载、已验证、正常电平 |
| `$error` | `#d96a55` | bypass、error、danger | bypass、失败、危险操作、削波 |
| `$state-idle` | `#8a817a` | idle、empty、unavailable | 空节点、未加载、不可用状态符号 |

- 组件不得写裸颜色值，只引用 theme token 或上述三个 fixed token。
- theme token 可以通过主题系统的 blend、lighten、darken、opacity 等能力派生；派生仍须保持原 token 的语义，不为单个组件另造 palette。
- 每个主题必须完整提供 Theme Token；切换主题只改变这些 token 的解析值，不改变 Fixed Token、组件规则或状态含义。
- 普通文字与背景的对比度至少 4.5:1；大号或 bold 短标签至少 3:1；焦点边框、状态符号和交互控件至少 3:1。
- `$success`、`$warning`、`$error`、`$state-idle` 在当前主题内必须彼此可区分，也必须同时配合符号或文字；不得只用色相表达状态。

### 2.3 字体与文本层级

终端字体由用户环境决定，规格不指定字体家族；通过字重、大小写、颜色和位置建立层级。

| 层级 | 样式 | 用途 |
|---|---|---|
| 产品标题 | bold + `$primary` | Header 中的 `GigBuddy` |
| 面板标题 | uppercase + bold；聚焦时 `$primary`，非聚焦时 `$text-muted` | `LIBRARY`、`TONE CHAIN`、`TONE DETAIL` |
| 内容标题 | bold + `$primary`，单行 marquee | tone、model、creator、preset 名称 |
| 区块标签 | uppercase + bold + `$text-muted` | `INPUT`、`AMP`、`CAB`、元信息字段名 |
| 主要数据 | `$text`；关键数值可用 `$accent` | 文件名、参数值、统计值 |
| 正文 | normal + `$text` | description、bio、note |
| 次要信息 | normal + `$text-muted` | id、架构、单位、路径摘要 |
| 状态文字 | 对应语义 token，并配合符号或文字 | `BYPASS`、`ACTIVE`、错误信息 |
| action token | normal + `$text-muted`；hover 时 bold `$background on $accent` | 右下角快捷动作 |

- 正文不用全大写；全大写仅用于短标签、面板名和短状态词。
- 不使用负 letter spacing；不依赖终端字体缩放。
- 长标题使用 marquee，长正文使用换行和滚动，不能粗暴裁掉关键信息。

### 2.4 焦点、选择与 hover

| 状态 | 边框 | 内容 | 光标/选中元素 |
|---|---|---|---|
| 当前面板 | `$primary` | opacity 1 | `$primary`，最亮 |
| 非当前面板 | quiet surface | opacity 0.8 | `$secondary`，仍可定位 |
| 模态 | `$accent` | opacity 1，底层 dim | 光标恒 `$primary` |
| hover | 不改边框尺寸 | 局部 `$surface-hover` 或 token 反色 | 不改变实际选择 |
| disabled | quiet border | `$text-disabled` | 不接受点击或键盘动作 |

- hover、focus、selected、active 是四种不同状态，不能用同一背景表达。
- 鼠标移过一行只显示 hover；单击才改变焦点或选择。
- 键盘焦点始终可见，不能只靠边框判断焦点落在哪一行。
- 打开模态后底层降暗，但当前模态的选择光标不能降级。

### 2.5 状态符号

| 符号/文字 | 含义 |
|---|---|
| `$success` + `●` | 节点已加载并工作 |
| `$error` + `● BYPASS` | 内容保留，但节点直通 |
| `$state-idle` + `○` | 节点为空或不可用 |
| `▶` | live chain 当前使用的文件 |
| `▷` | bypass 时保留的恢复候选文件，当前不参与处理 |
| `[ ]` / `[x]` | 批量操作的未选/已选 |
| `✓` | 已下载、已验证或已完成；旁边必须有上下文 |
| `◐` | 部分下载或部分完成 |

仅批量安装、卸载、删除场景显示 `[ ]`/`[x]`。单选浏览不显示复选框。

### 2.6 Pane 标签与统一搜索栏

- 包含多个同级内容视图的 Pane 使用一行 view tab strip，不使用左右箭头切换同级页面，不为每个内容视图创建新的 Pane。统一标题格式为 `PANENAME  TAG1  TAG2`：Pane name 使用 uppercase + bold，TAG 使用普通 `$text-muted`，各项只用空格分隔，不显示 `·` 或其他分隔符。
- TAG 是真实交互控件，不是装饰文字。鼠标 hover 时以 `$surface-hover` 或主题 accent 背景点亮但不改变尺寸；选中 TAG 使用 bold + 高亮背景，并在 `NO_COLOR` 下使用 reverse + bold。点击 TAG 立即激活对应 view tab；disabled TAG 使用 `$text-disabled`，不接受点击。
- view tab strip 不使用独立边框、圆角胶囊或嵌套卡片；Pane name 和 TAG 保持同一行、同一行高。Pane name 不可点击，只有 TAG 有命中区。active TAG 的高亮不能只依赖颜色，必须同时使用字重、反色或可见标记。
- 可搜索的 Pane 在 view tab strip 下方固定一行 SearchBar，逻辑结构只有 `query field + sort select`，视觉示例为 `SEARCH <query> · SORT <sort>`。query 和 sort 同行显示，不能拆成上下两行或两个独立面板。
- SearchBar 使用背景层级区分整体、query focus 和 sort focus：普通状态使用 `$surface`，焦点或打开状态使用 `$surface-hover`/`$accent`；不使用输入框边框、外围框、圆角胶囊或阴影。光标和当前值仍必须清楚可见。
- SearchBar 宽度固定为 Pane content region，内容不能参与轨道尺寸计算。full/standard 使用 `query: minmax(16, 1fr)` + `sort: 24 cells`，compact 使用 `query: minmax(10, 1fr)` + `sort: 18 cells`；标签和分隔点占固定宽度。`<query>` 表示预留的固定 query 区域，不是随文字增长的容器。编辑时长 query 在区域内水平滚动，未编辑时 cell-width-safe 省略；sort 始终贴右且不移动。
- `tab/shift+tab` 永远只按视觉顺序做元素焦点前进/回退，view tab strip 作为一个 focus stop，不逐个 TAG 消耗焦点。当前 Pane 非文本编辑状态下，`[` 激活前一个 view tab，`]` 激活后一个 view tab；首尾不循环，到边界 no-op。鼠标点击 TAG 仍直接激活。
- `/` 聚焦当前 view tab 的 query field；`tab` 从 query 进入 sort，再进入 Type 表头和结果表，`shift+tab` 反向。`enter` 提交 query 或激活当前 sort。编辑中的第一次 `esc` 只关闭编辑并保留已提交 query；SearchBar 未激活时再次 `esc` 才清除 query。sort select 点击或 `enter` 打开选项，选择后立即应用并关闭。
- 结果表头只提供 Type/gear 过滤，不提供 Author 或其他列过滤。Type 表头保持固定列宽和固定标签；active 时用背景和可见状态标记高亮，不把完整类型值拼进表头造成列宽变化。点击 Type 打开紧凑的单选菜单，包含 `ALL` 和当前数据中的全部非空原生类型；选择后立即应用并关闭，`ALL` 清除过滤。服务端新增类型必须自动出现，不维护本地闭合集合。
- Type 过滤菜单不改变 SearchBar、表格起始行和 Pane 底部提示带的高度；过滤变化只更新结果 rows 和动态状态。Author 保留为显示列，并继续支持 `@author` query，但没有表头过滤菜单。

## 3. 全局交互规则

### 3.1 导航和返回

- `↑/↓` 浏览当前列表或树，不改变页面范围外的状态。
- `enter` 执行当前行的主要动作。
- `esc` 按层级返回：先取消编辑或清选择，再清搜索，再关闭当前次级页面/模态。
- 焦点离开面板时保留其光标和视口；重新进入不能跳回第一行。
- 异步响应必须校验当前 tone、creator、query 或页面身份，晚到结果不能覆盖新状态。

### 3.2 批量选择

- `space` 切换当前行，`a` 在当前可操作集合内全选/全不选。
- 点击 `[ ]`/`[x]` 与 `space` 等效；点击行的其他区域只移动光标。
- 选择计数放在提示带的动态前缀；全选时 action token 从 `a all` 变为 `a none`，动作后缀仍贴右。
- 列表重载、query 改变或操作完成后清除不再有效的选择和二次确认状态。

### 3.3 通知和错误

- 信息、warning、error 必须同时通过文字和语义色表达。
- Header 通知为单行 overlay，位于标题左侧，不推动标题、不遮挡 Library。
- 长通知 marquee；通知消失后布局不变化。
- 可恢复错误保留当前有效内容，并提供 `r retry`；不能先清空页面再显示失败。
- 危险操作说明结果和不可逆性，不只显示 `$error` 边框。

### 3.4 Undo/redo

- `ctrl+z` undo，`ctrl+shift+z` redo。
- 仅追踪 preset 应用产生的链快照，上限 50。
- 快照域是 `model/ir/gain/master/quality`；`input` 不进入快照。
- 新 preset 应用后清空 redo；无历史时按键无副作用。

### 3.5 主界面入口

| 入口 | 动作 | 目标 |
|---|---|---|
| LOCAL tone 行 `enter`/双击 | 按 tone 的 gear 选择目标 | 打开 AMP Picker 或 CAB Picker |
| TONE3000 tone 行 `enter`/双击 | 查看并管理远程 pack | 打开 Pack Install |
| TOP CREATORS 行 `enter`/双击 | 搜索该作者 | 激活 Library 的 TONE3000 view tab 并执行 `@author` 搜索 |
| AMP/CAB 节点单击 | 查看节点 pack | DetailPane 的 AMP/CAB Pack |
| INPUT 节点双击或 INPUT 行 `enter` | 选择输入源 | InputSource |
| 主界面全局键 `p` | 浏览 preset | Preset Load |
| Presets 面板 `n` | 新建 preset | Preset Save As |
| LEVEL 的 AUDIO SETTINGS | 配置设备和延迟 | Audio Settings |
| LEVEL 的 MUTE | 切换 master mute | 留在主界面，只改变 MUTE 状态 |

### 3.6 全局快捷键

| 键位 | 动作 | 生效边界 |
|---|---|---|
| `/` | 聚焦当前 view tab 的 query field | 主界面；编辑参数或模态输入时不截获 |
| `ctrl+p` | 打开 command palette | 所有非确认中的页面；palette 内再次按键不叠加打开 |
| `t` | 切换下一个主题 | 主界面；不改变语义色和当前焦点 |
| `p` | 打开 Preset Load | 主界面；输入框编辑时输入字符 `p` |
| `ctrl+s` | 保存 active preset | 无 active preset 时进入 Save As；覆盖需要二次确认 |
| `ctrl+shift+s` | 打开 Preset Save As | 主界面和 Presets 面板 |
| `ctrl+z` / `ctrl+shift+z` | undo / redo preset 应用 | 参数编辑和文本输入时不截获 |
| `space` / `s` / `l` | 播放暂停 / 停止 / 循环 | 表格聚焦时 `space` 优先用于选择；输入源非 file 时显示 no-op 原因 |
| `ctrl+c` | 复制或退出 | 有文本选择时复制并清选择；无选择时 1.5s 内按两次退出 |

Command palette 提供 Search、参数增减、Preset Load、Save、Save As、Audio Settings、Next Theme 和 Quit。palette 的 Quit 立即退出，不使用双击确认；危险数据操作不放入 palette。

### 3.7 焦点顺序与返回

- 首次进入主界面，焦点落在 LOCAL 表第一条可用数据行；LOCAL 为空时落在 Library 搜索/结果区域。
- 主面板遍历顺序为 Library → Presets → Chain → Detail → LEVEL；`shift+tab` 反向。
- 面板内部先按视觉顺序遍历筛选器、搜索框、表格和动作控件；Tab 不得把焦点送入隐藏控件。
- 当前 view tab 的 query field 按 `tab` 进入 sort 和结果表；结果表按 `shift+tab` 返回 SearchBar。
- view tab strip 整体只占一个 focus stop；`tab/shift+tab` 不在 TAG 之间移动，也不激活 TAG。焦点不在文本输入或模态编辑时，使用 `[`/`]` 切换前后 view tab。
- 关闭模态后恢复打开该模态的控件和原视口；来源控件已卸载时回到所属主面板的首个可用控件。
- command palette 关闭后恢复原焦点；切主题、通知、后台刷新不得改变焦点。
- 模态内 Tab 循环，不允许焦点穿透到底层页面。

### 3.8 成功 mutation 后的统一刷新与视角保持

本规范把会改变共享数据源的成功操作定义为 mutation。安装、卸载、导入、删除、移入/移出 trash、preset 保存/重命名/删除以及其他产生持久状态变化的操作，都必须在提交完成后发布一次 `MutationCommitted`。查询、分页、verified 查询、播放控制、参数预览和没有实际状态变化的 no-op 不属于 mutation。

- `MutationCommitted` 只能在持久提交成功后发布；失败、取消、阻塞和未产生变化的操作不得发布。部分成功只发布一个事件，并且只携带实际成功的对象 key、操作类型和提交 revision。
- App 只提供一个 mutation refresh coordinator。一个提交对应一个刷新周期；同一事件循环内重复到达的同一提交事件合并，所有已注册页面实例（包括当前未激活但仍保留的页面）每周期最多执行一次 `reconcile_after_mutation(event)`。轮询读到相同 fingerprint/revision 不得再触发可见刷新。
- 刷新前必须为每个页面实例保存 `ViewAnchor`：`screen_id`、active App tab、active `view_tab_id`、focused widget、cursor row key、cursor column、first visible row key、行内偏移、`scroll_x`/`scroll_y`、仍有效的 selection keys、confirmation state 和 Detail context key。row key 必须是稳定业务身份，不得使用 cursor index；例如 `local:<tone_id>`、`tone:<tone_id>`、`creator:<username>`、`m<model_id>` 和 `preset:<preset_id>`。
- 页面先按 mutation 的影响范围做增量 reconcile，再按稳定 row key 恢复 `ViewAnchor`。不得先清空并重建整个表来“恢复”位置；不得因为刷新切 tab、push screen、自动打开 Picker、把焦点送到隐藏控件或清空 DetailPane。
- 当前 row 被删除时，优先选择原视觉位置的下一行；没有下一行时选择上一行；两者都不存在才进入明确 Empty。剩余 selection 保留，已删除 key 才清除；受影响且已失效的 confirmation 必须显式取消，不能继续确认陈旧目标。
- Detail context 仍有效时保持原对象和模式；对象已删除时按同一行选择规则切换到可用对象，过渡期间保留最后有效内容并显示明确的 removed/unavailable 状态，不显示空白 DetailPane。
- 操作页面是否结束由该页面已有的成功状态机决定；mutation refresh 本身不得导航。关闭操作页面时，必须恢复其来源页面保存的 screen、tab、focus、cursor 和 viewport。

各页面的 `reconcile_after_mutation()` 至少遵循以下范围：

| 页面 | reconcile 规则 |
|---|---|
| LOCAL | 刷新 SQLite rows 和受影响 tone 的下载状态，按 `local:<tone_id>` 恢复光标、选择和视口。 |
| TONE3000 | 只更新受影响 tone/model 的下载状态，保留 query、排序、已加载页、光标和视口；不得因本地安装/卸载重新排序远程结果。 |
| TOP CREATORS | 本地安装/卸载不重新请求、重排或替换 creator 排行；按 `creator:<username>` 保留当前作者和视口。verified cache 的变化只能原位更新勾选标记。 |
| DetailPane / Pack | 刷新模型 rows 和安装状态，按 `m<model_id>` 恢复光标、Description/Selection 模式和视口；不得自动打开新的 TonePicker。 |
| ChainPanel / Presets | 同步受影响文件、引用和 active/dirty 状态，保留当前对象、焦点、target、selection 和视口；无关页面不得被切换或清空。 |

### 3.9 Pane view tab、SearchBar 和表头过滤

当同一 Pane 存在多个同级内容（例如 Library 的 LOCAL、TONE3000、TOP CREATORS，或 DetailPane 的 Description、Pack）时，必须在同一 Pane 内使用 view tab strip。view tab 是内容切换协议，不是新的 screen，也不是底部 action token。

- 每个 view tab 有稳定 `view_tab_id`、显示 TAG 和独立状态。激活 view tab 只替换该 Pane 的内容，不 push screen、不切换 App 主 tab、不改变其他 Pane 的焦点。
- 鼠标点击 TAG 激活对应 view tab。`tab/shift+tab` 只做普通元素焦点遍历，view tab strip 是一个 focus stop；当前 Pane 非文本编辑状态下使用 `[`/`]` 激活前后 TAG。`←/→` 不再承担 Description/Selection 或其他同级 view 的切换语义，也不在提示带中显示为 view 切换 action。
- 每个 view tab 独立保留 query、sort、Type filter、cursor row key、selection、Detail context 和 viewport；切回时恢复该 tab 的状态。切换 tab 不触发全局 mutation refresh，也不清除其他 tab 的已提交 query。
- SearchBar 是所有可搜索列表的固定一行：`query field + sort select`。query 和 sort 必须使用固定轨道；只有 Type/gear 可以通过结果表头过滤，Author 只能显示或通过 query 搜索。
- Type 过滤是即时的、可恢复的局部状态：点击 Type 表头打开 `ALL + 当前原生类型` 单选菜单，选择后立即过滤；`ALL` 恢复不过滤。菜单关闭不改变当前 view tab、query、sort、cursor 或视口，除非结果中已不存在该 row。
- TOP CREATORS 选中作者后，激活 Library 的 TONE3000 view tab 并提交 `@作者名` query；不得通过左右移动或打开新的 TONE3000 screen 完成跳转。

## 4. LibraryPanel

### 4.1 LOCAL

- 列：Sel、Title、Type、DL、Fav、Arch、Files、Up、Author。
- Author 显示 `@作者名`；未知作者显示明确占位，不伪造作者。官网确认过的作者写入本地正向缓存，所有作者展示入口复用该缓存并显示 `✓`；未确认作者不得猜测或显示勾。
- 聚焦行立即联动 DetailPane 的 Tone Description，不抢走 Library 焦点。
- `enter` 打开当前 tone；双击与 `enter` 等效。
- 搜索支持 `@author`、`#tag`、`author:`、`tag:`、`make:"..."`。

提示状态（整条提示带均右对齐）：

| 状态 | 提示带 |
|---|---|
| 无选择 | `{page state} · a all · space select · d uninstall · enter open` |
| 有选择 | `{n} sel · {page state} · a all/none · space select · d uninstall · enter open · esc clear` |
| 加载更多 | `loading… · a all/none · space select · d uninstall · enter open` |

### 4.2 TONE3000

- 聚焦行联动 Remote Tone Description。
- `enter`/双击打开 pack 详情；不能把 model id 当 tone id，也不能从搜索结果直接安装整包。
- 未下载文件使用 `$state-idle` 空心状态符号、主题提供的 `$text-disabled` 文本和斜体 `(not downloaded)`，不使用连字符文案。
- 缓存键为 `(query, type, sort, author)`，FIFO 上限 20。
- 启动预取默认搜索一次；新 query、未命中组合、加载更多或 `r` 强制刷新才访问网络。

提示状态（整条提示带均右对齐）：

| 状态 | 提示带 |
|---|---|
| 加载中 | `loading… · enter detail` |
| 可继续 | `{loaded}/{total} · ↓ more · enter detail` |
| 完成 | `{loaded}/{total} · all loaded · enter detail` |
| 失败 | `{loaded}/{total} · load failed · r retry · enter detail` |

### 4.3 TOP CREATORS

- 列：Rank、Creator、Tones、Downloads、Fav、Models。
- SORT：Most Tones（默认）、Most Downloads、Most Favorites、Most Models；Select 宽 26，与 TONE3000 筛选条一致。
- 聚焦作者行联动 Creator Detail。
- `enter`/双击作者行：激活 Library 的 TONE3000 view tab，填入 `@作者名` 并触发真实搜索。
- 数据源固定为官网 `/top-creators` 使用的 `user_public_counts` 排行榜视图；Tones、Downloads、Favorites、Models 均使用服务端聚合值。
- 首次渲染后的已有行数字不得由后台精化改写。加载更多只按接口顺序追加下一页未见 creator；不得清表、重排已有行或主动设置滚动位置，光标和视口由原位追加自然保持。

提示状态（整条提示带均右对齐）：

| 状态 | 提示带 |
|---|---|
| 加载中 | `loading… · enter search` |
| 可继续 | `{count} · ↓ more · enter search` |
| 完成 | `{count} · all loaded · enter search` |
| 失败 | `{count} · load failed · r retry · enter search` |

## 5. ChainPanel

### 5.1 结构

v0.1 固定为 INPUT、AMP、CAB 三行：

- INPUT 是链头，不是处理槽，不能删除或 bypass。
- AMP 对应 `model`，CAB 对应 `ir`。
- AMP/CAB 可以为空；空与 bypass 是不同状态。
- 多 Slot、槽位移动和 `slots[]` 不属于 v0.1。

### 5.2 节点操作

| 操作 | INPUT | AMP/CAB |
|---|---|---|
| 单击 | 聚焦，保留当前 detail 上下文 | 聚焦并在 DetailPane 显示对应 pack |
| 双击 | 打开 InputSource | 切换 bypass/恢复，并保持 pack detail |
| `↑/↓` | 无换 model 行为 | 切换同 pack 的前/后 model |
| 右侧箭头 | 无 | 与 `↑/↓` 等效 |
| `d` | 无 | 删除内容，节点进入 `$state-idle` 空态 |

- 双击 AMP/CAB 节点切换 bypass/恢复；Local/AMP/CAB Pack 中重复选择当前 tone 是等价快捷入口。
- Local/AMP/CAB Pack 文件行的 `enter`/双击遵循同一规则：选择其他文件时加载该文件；重复选择当前 `▶` 文件时进入 bypass；重复选择 bypass 状态下的 `▷` 文件时恢复处理。
- bypass 保留节点标题和 pack 上下文，状态显示 `$error` + `● BYPASS`；恢复后回到 `$success` + `●`。
- v0.1 bypass 恢复候选只保存在当前 TUI 进程内；`live_chain.json` 中对应键写为 null。
- 在 bypass 状态加载其他文件会写入新路径、退出 bypass 并立即恢复处理。
- TUI 重启或外部配置把对应键改为 null 时，没有进程内恢复候选，节点按 Empty 显示。

### 5.3 参数控制

| 参数 | 减少 | 增加 | 基础步长 | 值域 |
|---|---|---|---:|---:|
| gain | `g` | `G` | 0.10 | 0–10 |
| master | `m` | `M` | 0.05 | 0–10 |
| quality | `q` | `Q` | 0.05 | 0–1 |

- 键盘短按：变化一个基础步长。
- 单击对应减/加 token：变化一个基础步长，与键盘一致。
- 长按 350ms 后开始连续变化，每 100ms 一个基础步长；不做二段加速。
- 松开、移出控件或失去鼠标捕获时立即停止。
- 到达上下限后停止，不累积隐藏增量。
- 单击数值进入精确编辑；最多两位小数。
- `enter` 应用，`esc` 取消，失焦取消；取消不能写入配置。
- 点击中间 `·` 将该参数恢复为协议默认值（gain/master/quality 均为 `1.0`）；需要设置为 `0` 时使用减小键或精确编辑。
- 编辑态显示闪烁插入光标；退出后不能残留光标或占位反色。

参数提示示例：

```text
GAIN 1.00  g · G    MASTER 1.00  m · M    QUALITY 1.00  q · Q
```

### 5.4 INPUT 和面板提示

- INPUT 显示文件名，不加 `Dry` 前缀；行尾 PLAY 块与箭头列右缘对齐。
- PLAY 块可点击，hover 不能覆盖其状态色。
- ChainPanel 固定动作：`d delete · space play/pause · s stop · l loop`。
- `d` 只作用于当前聚焦 AMP/CAB；没有有效目标时提示，不静默删除其他节点。

## 6. DetailPane

DetailPane 是当前焦点对象的上下文页，不是独立导航源。Library、Chain、Creators、Presets 中谁获得实际焦点，DetailPane 就跟随谁；后台刷新不得抢占。

### 6.1 对应关系

| 来源/状态 | 页面 | 内容 | 功能 |
|---|---|---|---|
| 无有效选择 | Empty | 无标题/摘要背景；空态文字 | 无动作 |
| LOCAL tone 聚焦 | Tone Description | tone 标题、tone/model id、摘要、description、作者认证 | 阅读/复制；通过 Detail view tab 进入 Local Pack |
| LOCAL tone Selection | Local Pack | pack 文件、架构、大小、下载状态、`▶` 当前文件 | 浏览；`space/a` 多选；`enter`/双击加载其他文件或切换当前 tone 的 bypass/恢复；`i/u` 管理文件 |
| TONE3000 tone 聚焦 | Remote Description | 远程元信息和 description | 阅读/复制；通过 Detail view tab 进入 Remote Pack |
| TONE3000 Selection | Remote Pack | 远程文件、架构、下载状态 | `space/a` 多选；`i` 安装；`u` 卸载已下载文件；`enter`/双击打开 Pack Install |
| AMP 节点聚焦 | AMP Pack | 当前 AMP 所属 pack，`▶` 当前文件 | `enter`/双击加载其他 AMP 文件；重复选择当前 tone 切换 bypass；`i/u` 管理文件 |
| CAB 节点聚焦 | CAB Pack | 当前 CAB 所属 pack，`▶` 当前文件 | `enter`/双击加载其他 CAB 文件；重复选择当前 tone 切换 bypass；`i/u` 管理文件 |
| AMP/CAB bypass | 原 Pack | 保留 pack 和光标；恢复候选用 `▷`，不显示 `▶`；节点另显 `BYPASS` | 重复选择 `▷` 恢复；选择其他文件则加载并恢复；节点双击同样切换 bypass/恢复 |
| AMP/CAB 为空 | Empty | 不残留旧 tone 或 pack | 从 Library 选择内容后建立节点 |
| INPUT 聚焦 | 保留当前 DetailPane | INPUT 状态仍在 ChainPanel 显示 | 双击 INPUT 打开 InputSource；不让输入源覆盖 tone detail |
| TOP CREATORS 聚焦 | Creator Detail | `@作者`、verified、完整多行 bio | 阅读/复制；搜索动作仍由 creators 行执行 |
| PRESETS 行聚焦 | Preset Detail | preset 名、active/dirty、AMP、CAB、参数、note | 预览；加载/编辑/删除仍由 Presets 面板执行 |
| 解析/preset 错误 | Error Detail | 明确错误文字 | 返回来源修正，不提供隐式动作 |

### 6.2 Description 与 Selection

- Tone Description 和 Pack Selection 是同一 Detail context 下的 view tabs；点击 TAG 或使用 `[/]` 激活。`tab/shift+tab` 只做焦点遍历，`←/→` 不切换这两个 view；模式切换不能造成布局跳变。
- tone 没有可用模型时留在 Description，并显示明确提示。
- `esc` 返回进入 Selection 前的来源控件；从 Description 没有更深层时不清空 tone。
- 标题行为 `Tone title · TONE #xx · MODEL #xx`，超长时 marquee。
- pack 表不显示独立 TONE id 列。

### 6.3 Pack 文件操作

- `▶` 只表示 live chain 当前参与处理的文件；光标表示当前浏览行，两者可以短暂分离。
- bypass 时没有参与处理的文件，因此不显示 `▶`；进程内恢复候选改用 `▷`。
- 当链文件因 `↑/↓`、箭头按钮或外部配置变化时，光标同步到新的 `▶` 行并保证可见。
- Local/AMP/CAB Pack 中，`enter`/双击文件行选择其他文件时加载该文件；重复选择当前 `▶` 文件时进入 bypass，重复选择 bypass 状态下的 `▷` 文件时恢复处理。
- Remote Pack 中，`enter`/双击打开 Pack Install，不直接加载远程文件。
- ChainPanel 节点双击与 Pack 中重复选择当前 tone 是两条等价的 bypass/恢复入口；两者必须产生相同状态和持久化结果。
- `[ ]`/`[x]` 仅用于 `i/u` 的批量目标，不改变 `▶`。
- 未勾选时，`i/u` 回退作用于光标行；有勾选时作用于勾选集合。
- 活动链文件不能卸载；preset 引用需要第二次 `u` 确认。

### 6.4 异步一致性

- remote models、creator bio、verified 状态都必须携带对象身份守卫。verified 成功结果写入本地正向缓存，后续 LOCAL、TONE3000、TOP CREATORS、Detail、metadata 和安装预览统一读取该缓存。
- 用户切换 tone/creator 后，旧响应只可写缓存，不可覆盖当前 DetailPane。
- 加载中保留已有有效内容；失败显示可恢复状态，不把 DetailPane 清成 Empty。

## 7. Presets

- 列：Sel、Preset、AMP、CAB、Note。
- `enter` 加载，`n` 新建，`ctrl+s` 保存，`r` 重命名，`e` 编辑 note，`space` 选择，`a` 全选/全不选，`d` 删除，`esc` 清选择。
- 聚焦行联动 Preset Detail；加载才改变 live chain。
- preset 加载保留当前 `input`。
- v0.1 preset 快照是扁平 `model/ir/gain/master/quality`；不声称已经支持 `slots[]`。

提示状态（整条提示带均右对齐）：

| 状态 | 提示带 |
|---|---|
| 无选择 | `active/dirty · n new · ctrl+s save · r rename · e note · a all · d delete · enter load` |
| 有选择 | `{n} sel · n new · ctrl+s save · r rename · e note · a all/none · d delete · enter load · esc clear` |

## 8. 模态与次级屏幕

### 8.1 通用视觉和行为

- 模态背景 dim，ModalBox 使用 `$panel` 底色和 `$accent` 圆角边框。
- 标题为 uppercase + bold；普通确认使用 `$accent`，危险确认使用 `$error`。
- 首个可操作控件自动获得焦点；焦点顺序按视觉顺序。
- `esc` 取消/关闭；双重 dismiss 必须幂等。
- 模态底部 action token 全部可点击并有局部 hover。
- Picker 内的 model preview 属于模态内部详情区，不是主工作区 DetailPane；移动 Picker 光标不得改写主 DetailPane。

### 8.2 动作矩阵

| 页面 | 键位与动作 | 单提示带示例 |
|---|---|---|
| InputSource | `enter` 选择；`space` 播放/暂停；`s` 停止；`l` 循环；`d` 下载；`esc` 关闭 | `space play/pause · s stop · l loop · d download · enter select · esc close` |
| Pack Install | `space` 选择；`a` 全选；`enter/i` 安装；`u` 卸载；`esc` 取消 | `a all/none · i install · u uninstall · esc cancel` |
| Local Uninstall | `u` 确认；`esc` 取消 | `u uninstall · esc cancel` |
| AMP/CAB Picker | `↑/↓` 浏览；`←/→` 折叠/展开；`enter` 选择；`esc` 返回 | `enter pick · esc back` |
| Preset Load | `enter` 加载；`esc` 取消 | `enter load · esc cancel` |
| Preset Save As | `enter` 保存；`esc` 取消 | `enter save · esc cancel` |
| Preset Rename | `enter` 重命名；`esc` 取消 | `enter rename · esc cancel` |
| Preset Note | `enter` 保存；空值清除；`esc` 取消 | `enter save · empty clears · esc cancel` |
| Preset Delete | `enter` 删除；`esc` 取消 | `enter delete · esc cancel`，标题和警告使用 `$error` |
| Audio Settings | picker 改变即应用并重启引擎；`enter/esc` 关闭 | `enter close · esc close` |

### 8.3 InputSource 状态机

| 状态 | 内容与可操作项 | 完成/退出 |
|---|---|---|
| instrument | `✓ Instrument`；播放键不可执行并说明需先选 dry file | `enter` 选择后关闭 |
| dry files available | 文件列表、当前文件 `✓`、缺失数量和 download all | `enter` 选择文件后立即 playing + loop，并关闭 |
| empty | `(no dry inputs)` + download all | `d` 或 download 行进入 downloading |
| downloading | 保留树；状态显示 `done/total + filename`；禁用重复下载 | 结束后刷新树，保留当前光标身份 |
| download partial/error | 已完成文件仍可选；状态显示失败原因 | `d` 重试缺失项；`esc` 可关闭 |
| playing/paused/stopped | 状态行显示状态、位置、loop 和文件名 | `space/s/l` 原地更新，不关闭 |

- 下载 worker 关闭页面后可以继续写文件和缓存，但不得访问已卸载 widget，也不得重开页面或抢焦点。
- 同名 exclusive 下载启动时取消旧 UI 更新；已落盘文件保留，新任务重新计算 missing。

### 8.4 AMP/CAB Picker 状态机

| 状态 | 内容与动作 | 返回结果 |
|---|---|---|
| local ready | 本地 tone/model 树 + 内部 preview | `enter` 选择并关闭 |
| local empty | 明确显示无本地候选，搜索框仍可用 | 输入 query 进入 remote loading；`esc` 返回 |
| CAB none | CAB Picker 首项显示 `CAB — (none)` | `enter` 清空 CAB 并关闭 |
| remote loading | 保留 query，结果区显示 `loading…` | query 身份变化则旧结果 cache-only |
| remote ready | 远程 tone 列表 + description preview + 下载状态 | `enter` 下载/选择；成功后关闭 |
| remote empty | `no results`，不显示可选空行 | 修改 query 或 `esc` |
| remote error | 保留旧有效结果；显示失败原因 | `r`/重新提交 query 重试 |
| importing | 显示文件级进度；禁用重复确认 | 成功写入库并选择；失败留在当前页 |

Picker 内部 preview 只显示 description 和当前候选信息，不改变主 DetailPane。搜索框 `↓` 进入结果树，结果树 `←` 回搜索框或上级节点。

### 8.5 Pack Install 状态机

| 状态 | 选择和动作 | 状态结束后的处理 |
|---|---|---|
| loading | 元信息保留，表格显示 loading，`i/u` disabled | 成功进入 ready；失败进入 load error |
| load error | 显示具体失败；不制造空模型行 | `r retry` 或 `esc` |
| ready | 未下载文件默认 `[x]`；已下载文件默认 `[ ]` + `✓ downloaded` | `space/a` 修改集合 |
| empty selection | `i` disabled；`u` 只在光标行为已下载文件时有效 | 选择变化后恢复动作 |
| installing | 显示 `done/total + filename` 和 ProgressBar；冻结本次目标集合 | 成功刷新下载标记并清已完成选择；失败保留未完成选择 |
| uninstall blocked | 活动链或库外路径阻止操作，使用 error 文案 | 留在 ready，要求先切换链 |
| uninstall confirm | preset 引用列表摘要 + `press u again` | 选择变化或离开页面立即取消确认 |
| uninstalling | 冻结本次目标集合并显示进度 | 成功刷新标记；元数据保留 |

- `enter` 与 `i` 都执行 install；提示条以 `i install` 为稳定短 token，键盘 `enter` 仍有效。
- 安装和卸载不能同时运行；运行中 `esc` 关闭 UI 不取消已经开始的文件写入，但后续 UI 更新必须通过 screen-alive guard。

### 8.6 Local Uninstall 状态机

| 状态 | 页面行为 | 后续动作 |
|---|---|---|
| plan ready | 显示 pack 数、文件数、总大小；说明文件移入 `data/.trash`，metadata/preset 保留 | `u`/`enter` 确认，`esc` 取消 |
| blocked active | 列出活动链引用并显示 error | 禁止卸载；要求先切换 AMP/CAB |
| blocked outside | 列出不受管理的路径并显示 error | 禁止卸载；不得移动库外文件 |
| preset referenced | warning 显示引用 preset 名称 | 第一次 `u`/`enter` 建立二次确认；再次确认继续 |
| uninstalling | 显示 `uninstalling…`，冻结目标和确认动作 | 完成前不能重复启动 |
| failed | 保留计划和选择，显示具体错误 | `u` 重试或 `esc` 取消 |
| success | 发布删除数量和 trash 路径，关闭页面 | LOCAL 刷新并清除已删除 tone 的选择 |

- 打开页面前必须至少有一个有效 LOCAL 选择；否则 `d` 只显示 `select local packs first`，不打开空确认框。
- 确认期间目标按 tone id 冻结；列表后台变化不能扩大删除集合。
- 执行前重新计算 active、outside 和 preset 引用，不能只信任打开页面时的旧 plan。
- 删除使用可恢复 trash，不直接永久删除；若 trash 移动部分失败，返回逐项结果并只刷新成功项。

### 8.7 Preset 次级状态

| 页面/状态 | 规则 |
|---|---|
| Preset Load empty | 显示 `no presets · ctrl+s to save`；`enter` no-op，`esc` 返回 |
| Preset Load invalid | 保留 preset 名并在内部详情显示解析错误；不能加载 |
| Save As empty name | 不提交；输入框保持焦点，显示 `name required` |
| Save As new name | `enter` 保存并关闭 |
| Save As duplicate | 第一次 `enter` 显示 warning 和 `enter again to overwrite`；名称变化或 `esc` 取消确认 |
| Rename empty | 不提交，显示 `name required` |
| Rename conflict/invalid | 保留输入并显示具体错误；修正后可重试 |
| Note save | 空字符串表示清除 note；成功后关闭 |
| Delete confirm | error 边框、数量、名称摘要和 `cannot be undone`；`enter` 删除 |
| Delete stale | 目标已不存在时显示 `presets no longer exist · esc close`，不报成功 |

校验错误属于提示带动态前缀或正文状态，不得覆盖最右侧固定确认和返回 token。

### 8.8 Audio Settings 状态机

| 状态 | 行为 |
|---|---|
| enumerating | picker 保留当前会话值并 disabled；显示 `detecting devices…` |
| ready | System Default + 枚举设备；buffer/sample rate 始终可选 |
| enumeration failed | 保留 System Default 和当前值；warning 说明设备列表不可用 |
| applying managed engine | picker 变化立即重启引擎；显示 restarting，成功后显示实际 IN/OUT/buffer/rate |
| applying external engine | 记录当前会话设置，不尝试重启；明确显示 `external engine not restarted` |
| restart failed | 保留选择值并显示 error；不得宣称已生效，提供 retry 或重新选择 |

`enter/esc` 都只关闭页面，不承担 apply 或 rollback；关闭后焦点回到 LEVEL 的 AUDIO SETTINGS。

### 8.9 异步任务契约

所有设备枚举、远程搜索、creator/verified 查询、下载、导入、卸载和引擎重启遵循同一生命周期：

1. 启动时记录 `request_generation`、业务 identity 和 screen instance。
2. 同一 worker group 的新请求使旧 generation 失效；旧任务可完成不可取消的 I/O，但不能提交当前 UI。
3. 提交 UI 前同时检查 generation 最新、identity 仍匹配、screen/widget 仍 mounted。
4. identity 已切换但结果仍有效时只写 cache；页面已关闭时只提交持久数据和应用级通知。
5. 用户取消只取消尚未开始的 UI/网络工作；已经开始的原子文件写入、安装或卸载不得留下半个文件。
6. error 保留上一个有效内容，给出可恢复动作；没有有效内容才进入明确 Empty/Error。
7. 成功后只清理本次目标对应的 selection、progress 和二次确认，不清理用户后来建立的新状态。
8. 安装、卸载、导入、删除等成功 mutation 在原子提交完成后发布 `MutationCommitted`；刷新由第 3.8 节的 coordinator 统一调度，失败、取消和阻塞路径不刷新。

引擎重启是独占任务：新设置合并到最新 desired config；旧重启完成后若 desired config 已变化，不能把旧值显示为 active。

## 9. Audio Settings 与 LEVEL

- Audio Settings 包含 INPUT、OUTPUT、BUFFER、SAMPLE RATE 四个 picker。
- INPUT/OUTPUT 首项为 `System Default`，内部空值表示省略 `--in/--out`，由系统选择默认设备。
- 设备列表来自 `realtime_cli --list`。
- picker 变化立即发送配置并重启引擎；关闭动作不承担 apply 语义。
- 显示 block 对应的近似延迟和 sample rate，单位使用次要文本色。
- LEVEL 表显示输入/输出电平；正常为 success，接近削波为 warning，削波为 error。
- MUTE 是独立主控状态，不能与 AMP bypass 混为一谈。

### 9.1 运行状态视觉

| 状态 | 颜色与样式 | 必须同时显示 |
|---|---|---|
| loading | `$accent` 或 `$text-muted`，不占用 error 色 | `loading…` 或明确进度 |
| ACTIVE preset | `$success` + bold | `ACTIVE` |
| DIRTY preset | `$warning` + bold | `DIRTY`，与 `ACTIVE` 可并列 |
| MUTE idle | `$accent` 描边、背景透明 | `MUTE` |
| MUTED | `$error` 描边和文字、背景仍与 bypass 区分 | `MUTED` |
| AMP/CAB bypass | `$error` 状态灯和文字 | `● BYPASS` |

MUTED 作用于 master 输出，AMP/CAB bypass 作用于单个处理节点；两者即使共用 error 色，也必须依靠位置和完整文字区分。

## 10. Header 与全局布局

- 标题恒居中：`GigBuddy — Your one-stop NAM tone manager`。
- 通知位于 Header 行内左侧 overlay，最大宽度为标题左边缘减一列。
- 通知不推动标题，不换行，不遮挡 Library；超长内容 marquee。
- 主工作区保持 Library、Chain、Detail、Presets、LEVEL 的稳定尺寸关系。
- 动态文本、hover、选择框、状态灯和 loading 文案不得改变固定控件的宽高。
- 窄终端优先压缩提示带的动态状态和次级 action token，不允许主要内容互相覆盖或留下半截 token。

### 10.1 尺寸档位

| 终端尺寸 | 档位 | 布局要求 |
|---|---|---|
| ≥120×40 | full | 显示完整列名、状态、主要与次级 action token |
| 100–119×35+ | standard | 缩短次要列和状态文案；保持四主面板与 LEVEL |
| 80–99×32+ | compact | 隐藏最低优先级统计列；action token 使用短标签；正文滚动 |
| <80×32 | unsupported | 显示明确最小尺寸提示；不得让控件重叠或静默丢失主要动作 |

宽度按当前控件实际 region 判断，不按启动时 viewport 猜测。窗口跨档位变化时保留对象身份、焦点、光标和视口。

### 10.2 Action token 保留顺序

提示带始终按“动态状态前缀 · 稳定动作后缀”生成。稳定动作从右侧开始保留，动态状态不占用固定的左栏宽度。

| 表面 | 从高到低的保留优先级 |
|---|---|
| 通用模态 | `esc` > confirm (`enter/i/u`) > selection (`space/a`) > 次级动作 |
| LOCAL | `enter` > `d` > `space` > `a` > 分页状态 |
| TONE3000/Creators | `enter` > `r`（仅错误时） > 分页状态 |
| Chain | `space` > `s` > `l` > `d`；有 AMP/CAB 焦点时 `d` 提升到 confirm 后 |
| Detail Pack | `i/u` > selection count/progress |
| Presets | `enter` > `d` > `ctrl+s` > `n/r/e` > `space/a` |

短标签只缩写说明词，不改变键位：例如 `d uninstall` → `d del`，`space select` → `space`。不能把不同动作缩成同一个 token。压缩顺序不得切入 token 中间；若仍不足，只能从左到右隐藏低优先级完整 token。

### 10.3 无颜色模式

- 当前光标使用 reverse + bold；hover 只用 underline，不能与光标相同。
- view tab 的 active TAG 使用 reverse + bold；非 active TAG 保留可见文本，hover 使用 underline 或背景点亮。
- selected 始终依靠 `[x]`；active 文件依靠 `▶`；bypass 候选依靠 `▷`。
- 当前面板标题 bold，非当前面板标题 normal；模态以独立边框和标题区分。
- success、warning、error 必须保留 `✓`、`◐/warning`、`error/BYPASS/MUTED` 等文字或符号。
- disabled 项显示 `(unavailable)`、`(not downloaded)` 或同义文字，并拒绝动作。
- monochrome 渲染遇到无 style segment 也必须安全降级，不能崩溃。

## 11. 引擎与数据边界

- v0.1 `live_chain.json` 使用扁平键：`model/ir/gain/master/quality/input`。
- model、ir 和 input 文件路径落盘为项目根相对路径；读取时解析为绝对路径。
- 引擎通过 `--root` 或可执行文件位置确定项目根。
- 省略 `--in/--out` 表示系统默认设备。
- preset 和 undo/redo 不保存 `input`。
- `slots[]`、动态槽位、槽位重排属于下一阶段规格，不得作为 v0.1 已实现能力描述。

### 11.1 `live_chain.json`

| 字段 | 类型/默认 | 所有者与语义 |
|---|---|---|
| `model` | relative path 或 null | AMP 文件；null = 当前协议无 AMP 处理 |
| `ir` | relative path 或 null | CAB IR；null = 当前协议无 CAB 处理 |
| `gain` | number，默认 1.0 | 模型前增益，范围 0–10 |
| `master` | number，默认 1.0 | 输出增益，范围 0–10 |
| `quality` | number，默认 1.0 | A2 quality scale，范围 0–1 |
| `input` | object，默认 instrument | 输入源和 dry-file 播放状态 |

`input` 对象：

| 字段 | 合法值/默认 | 规则 |
|---|---|---|
| `source` | `instrument`（默认）/`file` | file 模式必须有有效 `file` |
| `file` | 项目根相对 WAV 路径 | 仅 source=file 有效；缺失时降级 stopped 并报告 |
| `state` | `playing`/`paused`/`stopped`（默认） | instrument 模式忽略 |
| `loop` | bool，默认 false | instrument 模式忽略 |

- 写入采用同目录临时文件 + rename，读者不得观察半份 JSON。
- 文件缺失或 JSON 损坏时，TUI 降级到空链 + instrument + stopped，并显示 warning；不得覆盖坏文件直到用户产生有效写入。
- TUI 内部把 model/ir 路径解析为绝对路径用于比较，落盘时重新转为项目根相对路径。
- 外部写入合法配置时 UI 在下一次轮询同步；非法范围 clamp 并 warning，未知字段保留但不参与 v0.1 UI。

### 11.2 `level.json`

| 字段 | 类型/默认 | UI 用途 |
|---|---|---|
| `in` | number，默认 0 | INPUT 电平 |
| `out` | number，默认 0 | OUTPUT 电平 |
| `play_state` | playing/paused/stopped，默认 stopped | InputSource 和 INPUT 行状态 |
| `play_pos` | 秒，默认 0 | dry-file 播放位置 |

- 引擎约每 0.1s 更新；缺失、损坏或过期时显示无活动电平和 stopped，不让 UI 崩溃。
- level 是只读观测，不得反写为配置；播放命令仍写 `live_chain.json.input`。

### 11.3 MUTE

- MUTE 通过 `master=0` 实现，并在 TUI 进程内保存最近一个大于 0 的 master 作为恢复值。
- MUTED 状态修改 gain/quality 不解除 mute；修改 master 为大于 0 的值立即解除 mute。
- 再次点击 MUTE 恢复保存值；没有保存值时恢复 1.0。
- TUI 重启后读取到 master=0 时显示 MUTED，但恢复候选不存在，下一次 unmute 使用 1.0。
- external engine 模式仍写配置和更新 UI，但必须提示该进程未由 GigBuddy 重启或管理。

## 12. 验收规则

### 12.1 行为验收

- 每个 action token 和每个 view TAG 同时验证键盘路径、鼠标点击路径和 hover 命中。
- Library、DetailPane 等包含多个同级内容的 Pane 验证 `view_tab_id`、TAG 高亮、点击和 `[/]` 切换、view tab strip 单 focus stop、`tab/shift+tab` 只做焦点顺序，以及 `←/→` 不再切换 view。
- 每个可搜索列表验证一行 SearchBar 中同时存在 query 和 sort；验证背景焦点态、无边框样式、`/`、`tab`、`enter`、`esc` 和 sort 选择行为。
- 验证只有 Type/gear 过滤出现在结果表头，Author 和其他列不可过滤；点击 Type 表头打开动态原生类型的单选菜单，选中项高亮并即时过滤；过滤菜单不改变 SearchBar、表格起始位置或底部提示高度。
- 动态提示至少覆盖 idle、loading、selected、success、error、narrow-width 状态；每次状态变化都验证动态前缀在动作后缀左侧增长/收缩。
- 单击、双击、`enter` 必须分别验证，避免事件双触发或语义串线。
- Pack 文件选择必须覆盖四条路径：其他文件加载、当前 `▶` 文件进入 bypass、当前 `▷` 文件恢复、bypass 时选择其他文件并恢复；`enter` 与双击结果一致。
- DetailPane 按第 6.1 节逐个来源验证，包括晚到异步响应和焦点切换。
- 参数控制验证短按、单击、长按、移出、上下限、编辑确认和编辑取消。
- 安装、卸载、导入、删除和部分成功分别验证：成功项只发布一次 `MutationCommitted`，所有已注册页面各 reconcile 一次，失败/取消/阻塞不发布事件。
- 在 LOCAL、TONE3000、TOP CREATORS、DetailPane、ChainPanel 和 Presets 中分别验证 `ViewAnchor` 的 screen、App tab、`view_tab_id`、focus、稳定 row key、cursor column、first visible row、行内偏移、scroll、selection、confirmation 和 Detail 恢复；删除当前行覆盖“下一行优先、否则上一行”。
- 验证成功 mutation 不自动切 tab、push screen、打开 Picker、抢隐藏页面焦点或清空 Detail；关闭操作页面时来源页面的 anchor 仍恢复。

### 12.2 视觉验收

- 当前面板、非当前面板、模态三种焦点层级清楚。
- success/warning/error 除颜色外均有文字或符号。
- hover 不改变布局；动态状态变化不改变动作后缀的右边界和顺序。
- 空态无残留背景行；长标题可读，长正文可滚动。
- 在彩色终端和无颜色终端中都能打开所有页面；无颜色模式不能因样式缺失崩溃。

### 12.3 版本

- 规格版本：v0.1.8（2026-08-05）。
- v0.1.8 修订：`tab/shift+tab` 只做焦点遍历，view tab 使用 `[/]` 切换；SearchBar query/sort 使用固定轨道，长 query 不改变布局；表头只保留动态 Type 过滤，不提供 Author 过滤。
- v0.1.7 修订：统一多内容 Pane 的 view tab；标题行分隔样式后续修订为 `PANENAME  TAG1  TAG2`。统一 query + sort 单行 SearchBar；其 TAG 键盘行为和通用表头过滤范围已由 v0.1.8 收窄。
- v0.1.6 修订：统一成功 mutation 的 `MutationCommitted` 事件、单次刷新 coordinator、稳定 row key 和 `ViewAnchor` 恢复；失败、取消、阻塞和 no-op 不刷新，并补充各页面 reconcile 范围。
- v0.1.5 修订：底部提示统一为一个右下角提示带；动态状态排在左侧，稳定 action token 排在最右侧；窄宽度禁止半截 token，并按实际 `region` 重算。
- v0.1.4 修订：参数中间 `·` 恢复 gain/master/quality 的协议默认值 `1.0`；明确设置 `0` 改用减少键或精确编辑。
- v0.1.3 修订：红、绿、灰状态色固定；其他颜色跟随主题，并允许使用主题系统的语义派生色。
- v0.1.2 修订：颜色规范改为语义 token 并补充对比度验收；其中“全部颜色由主题解析”已由 v0.1.3 修正。
- v0.1.1 修订：Local/AMP/CAB Pack 中重复选择当前 tone 改为切换 bypass/恢复；Remote Pack 行为不变。
- CLI 版本：`gigbuddy --version` 输出 `gigbuddy 0.1.0`。
- 交互目标发生变化时，先更新本文和对应验收，再修改实现。

## 13. 实现迁移清单

本节记录“设计已确定、当前实现尚未追齐”的差异。它不是可选建议；完成实现后逐项删除。

| 设计目标 | 当前差异 | 需要同步的范围 |
|---|---|---|
| quality 使用 `q/Q`，基础步长 0.05 | 当前代码/README/旧测试使用 `u/U` 和 0.1 | app bindings、ChainParams、command palette、README、参数测试、提示 token |
| 鼠标单击使用参数基础步长 | 当前点击固定 0.01 | ChainParams press 逻辑和测试 |
| 长按 350ms 后每 100ms 重复基础步长 | 当前约 300ms/60ms，长按步长固定 0.1 | press timer、每参数步长、测试 |
| 所有特殊键和动作词按本文大小写 | 当前仍有 `Enter/Esc/Ctrl+S` 旧文案 | 全部 hint、README、snapshot 测试 |
| Audio Settings 显示 `enter close · esc close` | 当前显示 `Change apply · Esc close` | Audio Settings、click hit、hover、测试 |
| bypass pack 使用 `▷` 恢复候选、active 只用 `▶` | 当前 pack 标记未区分该状态 | DetailPane、bypass 测试、无颜色测试 |
| 重复选择 Local/AMP/CAB 当前 tone 切换 bypass/恢复 | 当前文件重复选择可能仍按加载或 no-op 处理 | Pack 行事件、节点状态、配置写入、键鼠等价测试 |
| 仅红、绿、灰状态色跨主题固定 | 当前 `$warning` 也被固定，且灰色空态没有 `$state-idle` token | theme 注册、状态灯、metadata、通知和主题测试 |
| <80×32 显示最小尺寸提示 | 当前仅局部缩写，无统一 unsupported 状态 | App layout、resize、截图/运行测试 |
| 无颜色模式安全且状态可辨 | 当前 Textual Tree monochrome 路径可能对 style=None 崩溃 | Tree render/依赖规避、NO_COLOR 测试 |
| 成功 mutation 后所有页面刷新且保持视角 | 安装、卸载、导入和删除仍由多个入口手动刷新，可能清表、重置 cursor/viewport 或自动导航 | MutationCommitted、refresh coordinator、ViewAnchor、各页面 reconcile、焦点/视口测试 |
| 多内容 Pane 统一使用 view tab | Library、DetailPane 等页面仍通过左右键或独立页面切换同级内容 | view tab strip、TAG hover/click/active、单 focus stop、`[/]`、tab-local state、导航测试 |
| SearchBar 与 Type 表头过滤统一 | query、sort、Type 过滤分散在不同控件或使用边框框选 | 固定轨道的单行 query+sort、背景焦点态、Type-only 表头菜单、Author 无过滤、窄屏布局测试 |

迁移期间，用户可见行为以本文为目标；测试若固化旧行为，应与实现一起更新，不能用旧测试否决已经确认的新设计。
