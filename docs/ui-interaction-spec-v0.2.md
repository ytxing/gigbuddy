# GigBuddy UI 交互与视觉规格（v0.2.14）

> 状态：**FROZEN**（2026-08-06，规格 v0.2.14）。本次修订固定 managed transaction 的 session、transaction、revision 和 runtime acknowledgement 身份，并补齐 rollback 语义；ChainPanel 的 Bypass 展示保持 v0.2.13 约定。
> 本文件是 GigBuddy v0.2 的目标 UI、交互、数据协议与验收基准，可独立于 v0.1 规格使用。
> v0.2 保留 v0.1 已确认的 Library、DetailPane、Preset、输入和视觉规则，并以动态多 Slot 链替代固定两节点链。
> 本版本包含音箱灵感主题候选；主题只替换语义 token，不改变状态含义、布局或交互契约。候选主题是否注册为可切换主题，属于第 14 节实现验收，不代表实现已经完成。
> 需求来源：`CONTEXT.md`、`docs/slots-design.md`、`docs/adr/0001-slots-chain-protocol.md`、`.remember/feature-baseline.md` 和冻结的 v0.1.7 UI 规格。
> 规范层级：本文是 v0.2 唯一规范来源；冻结后取代 `docs/slots-design.md` 的“唯一规格”声明和 ADR-0001 的提议状态。旧文件保留为设计来源，不再单独约束 v0.2 实现。
> 实现、测试或旧文档与本文冲突时，以本文为准；冲突视为实现或迁移缺陷。

## 1. 版本目标与边界

v0.2 将音色链定义为 Input、0–6 个有序 Slot 和链级参数。用户可以添加、删除、加载、bypass、恢复和重排 Slot；引擎严格按显示顺序处理。

v0.2 范围：

- Slot 内容限本地或从 TONE3000 安装的 `.nam` Model 与 `.wav` IR。
- 同一 Model 可以加载到多个 Slot，不去重、不警告。
- Slot 不预设任何 gear 类型；加载内容决定处理类型和显示标签。
- Preset 保存 Slots、顺序和链级参数；Input、播放状态、设备和 MUTE 不进入 Preset。
- 旧 `model/ir` live chain 与 Preset 只读兼容；v0.2 产生的新写入只使用 `slots[]`。

v0.2 不包含 VST3、插件参数、拖拽重排、并行/分支路由、稳定 Slot id、单 Slot 参数、自动排序或“合理链路”限制。未知格式不能执行，但不得导致链或 UI 崩溃。

### 1.1 v0.2 对旧稿冲突的裁决

| 冲突 | v0.2 裁决 | 理由 |
|---|---|---|
| `↑/↓` 同时被写成 Slot 导航和切 Model | 保留已确认的 `↑/↓` 切同 pack Model；Slot 行用 `tab/shift+tab` 聚焦 | 不改变既有 switch model 键位，也不让一个事件承担两个动作 |
| `d` 是清空内容还是删除 Slot | 删除数组项和整行 | Empty Slot 由 `+` 显式创建，避免删除后留下隐式结构 |
| Slot path 是绝对还是相对 | 落盘项目根相对，内存比较用绝对 | 延续已确认的 portable chain 行为 |
| null 是 Empty 还是 Bypass | 磁盘上是 Empty；Bypass 候选只存在当前 TUI 进程 | 保持 Slot 项只有 `path` 的最小协议 |
| Preset 是 path-only 还是 logical ref | Preset 保存 `model_id + path`，live protocol 仍 path-only | 延续文件改名后 Preset 可解析的现有能力 |

### 1.2 v0.2.2 gear 标签策略

TONE3000 的 `gear` 原生字段是唯一的分类来源，也是开放集合，不是 Slot 的固定类型。2026-08-05 的全量 API 校验共检查 10,083 条 tone，观察到 8 个值：`amp`、`amp-cab`、`cab`、`experimental`、`full-rig`、`outboard`、`pedal`、`space`。例如 `RR Rackman` 的原生 `gear` 是 `outboard`，不是独立的 `rackman` 类型。

数据身份、搜索、筛选、缓存键和精确匹配均使用服务端原生 `gear` token，保持其大小写、连字符和拼写。面向用户的 Slot 标签、Type 单元格、Type 菜单项和 Preset 摘要统一显示为 `UPPERCASE(native token)`：只转换字母大小写，不翻译、不缩写、不删除连字符，例如 `amp-cab` 显示为 `AMP-CAB`。显示转换不得反写数据，也不得改变过滤比较值。未来出现的新非空值必须自动按同一规则显示并可筛选，不得因为未列在本文而降级为 `SLOT`。

只有以下情况使用 `SLOT`：缺失 gear、空 gear、Tone 元数据无法解析，或本地文件没有可关联的 Tone。gear 标签不写入 `slots[]`，也不决定 `.nam` / `.wav` 的处理类型；处理类型仍只由扩展名派生。

### 1.3 v0.1.4 参数交互同步

v0.2 沿用 v0.1.4 的最新参数操作规则：点击 `gain`、`master` 或 `quality` 中间的 `·`，把该参数恢复为协议默认值 `1.0`；`·` 不再表示归零。需要明确设置为 `0` 时，必须使用减少键、减少 action token 或精确编辑。

该复位动作只修改所点击的链级参数，并沿用普通参数提交、dirty 和取消规则：不切换 MUTE、不解除 Slot bypass、不改变 target，也不写入 Input 或播放状态。MUTE 已开启时，复位 `master` 仍只改配置中的 `master=1.0`，不会自动 unmute。

### 1.4 v0.2.7 提示带修订

- 底部提示从“左状态 / 右动作”的概念模型改为一个右下角、右对齐的连续提示带。
- 动态状态、计数、来源、进度和错误排在左侧；条件动作（例如 `↓ more`）紧随动态状态；稳定 action token 排在最右侧，并保持右边界、顺序和点击命中区稳定。
- 状态、条件动作和稳定动作始终用 ` · ` 分隔；状态为空时不插入占位。`↓ more` 不是普通状态文字，必须有独立命中区并调用当前列表的下一页动作。
- 终端 resize 使用当前 widget 的 `region` 重新计算，并为 Textual 边框左右边缘和两个角预留 6 格；稳定 action 先尝试完整显示，空间不足时缩写说明词或按完整 token 隐藏低优先级 action，动态状态使用剩余宽度省略。禁止任何半截 token。

### 1.5 v0.2.8 mutation 刷新修订

- 安装、卸载、导入、删除、移入/移出 trash、preset 保存/重命名/删除以及其他产生持久状态变化的操作，成功提交后统一发布一次 `MutationCommitted`。
- App 通过唯一 refresh coordinator 通知所有已注册页面实例；同一事件循环内重复事件合并为一个刷新周期，并按稳定 row key 恢复 screen、tab、focus、cursor、selection、confirmation、Detail context 和 viewport。
- 失败、取消、阻塞和 no-op 不发布 mutation 事件；mutation refresh 不自动切 tab、push screen、打开 Picker、抢隐藏页面焦点或清空 DetailPane。

### 1.6 v0.2.9 Pane tab 与搜索栏修订

- 多内容 Pane 使用 `PANENAME  TAG1 / TAG2` 的 view tab strip；TAG 之间用 `/` 分隔。TAG 可 hover、点击且 active 高亮；其键盘激活方式已由 v0.2.10 固定为 `[/]`，不再使用 `tab/shift+tab` 或 `←/→` 切换。
- 可搜索 Pane 使用同一行的 `query field + sort select + type select`；SearchBar 通过 `$surface`、`$surface-hover` 和 `$accent` 背景区分状态，不使用输入框边框或独立卡片。
- 枚举过滤放到 SearchBar 的 Type select；结果表头只显示信息，不提供点击过滤，Author 和其他列同样不提供过滤。

### 1.7 v0.2.10 键位、固定宽度与 Type 过滤修订

- `tab/shift+tab` 保持全局焦点前进/回退，不切换 view tab；当前 Pane 使用 `[`/`]` 切换前后 view tab，文本输入和模态编辑时不截获。
- SearchBar 的 query、sort 和适用时的 type 使用固定 grid tracks；长 query 只在自身区域水平滚动或省略，不改变 SearchBar、sort/type 或 Pane 宽度。
- SearchBar 的 Type select 提供动态 Type/gear 过滤，不提供 Author 或其他列过滤。Type 值从当前数据动态生成，服务端新增值无需修改本地闭合集合；结果表头不再是过滤命中区。

### 1.8 v0.2.11 原生 gear 展示修订

- gear 的存储、查询、缓存和过滤身份保持服务端原生 token；面向用户的 Slot 标签、Type 单元格、Type 菜单项和 Preset 摘要只做 uppercase 展示转换。
- uppercase 转换保留原始拼写和分隔符，不维护固定 Slot 名称表。新增原生 gear 自动得到对应大写标签，例如 `outboard` → `OUTBOARD`、`amp-cab` → `AMP-CAB`。

### 1.9 v0.2.12 Slot 状态与标题分隔修订

- view tab 标题不显示 `·`，Pane name 与 TAG、TAG 与 TAG 之间只保留空格；选中关系由 TAG 高亮表达。
- Slot fieldset 标题保持 `状态灯 + LABEL`；Target 等标题附加状态使用 ` - ` 分隔，例如 `PEDAL - TARGET`。Bypass 不进入标题，而是在第二行文件名后显示 `BYPASS`。
- 焦点只通过背景、边框或反色表达，不在内容前插入 `>`。未知扩展名或不支持的处理格式在写入前拒绝，不能创建 Unknown Slot。

### 1.10 v0.2.13 ChainPanel Bypass 展示修订

- Bypass Slot 的 fieldset 标题仍为红色状态灯加 uppercase 原生 gear，例如 `● AMP`，不得显示 `BYPASS - AMP`。
- 第一行继续显示 Tone/Model 主标题；第二行先显示文件名，再以空格分隔显示红色 bold `BYPASS`，即 `filename  BYPASS`。不使用三角标记，也不把 `BYPASS` 放到文件名前。

### 1.11 v0.2.14 managed transaction 身份与 rollback 修订

- 每次 managed candidate 都必须生成唯一的 `transaction_id`，并在 prepare、文件提交、runtime apply、runtime rejection 和 rollback acknowledgement 中保持同一身份；不同提交不得复用 transaction id。
- managed 提交在写 prepare request 前必须等待当前 engine session 的 `ready acknowledgement`；同一存活 session 后续提交可以复用该 ready 身份，不要求 engine 重发 ready。
- candidate prepare、原子 JSON 写入和 runtime apply 必须使用同一个非负 `revision`。revision 一旦用于候选提交，不得由 runtime 或 rollback 隐式改写。
- `level.json` 的 runtime telemetry 必须携带 `runtime_session_id`、`runtime_transaction_id`、`runtime_revision`、`runtime_status` 和单调的 `runtime_ack_seq`。TUI 只能接受属于当前 session、当前 transaction 且晚于提交前 acknowledgement 的 applied/rejected 结果。
- rollback 也是一次新的 runtime transaction：必须生成新的 transaction id，并等待新的 acknowledgement；不能把提交前或候选提交的旧 `applied` 状态当作 rollback 成功。
- 原始 `live_chain.json` 不存在时，rollback 先通过带 base revision 和新 transaction id 的临时零 Slot chain 让 runtime 回到旧状态，收到 applied acknowledgement 后再删除临时文件，恢复“原文件不存在”。runtime 不活跃、base revision 无效或 rollback acknowledgement 超时都必须显式报告 rollback 失败，不能静默跳过。

## 2. 领域模型

| 术语 | 规范定义 |
|---|---|
| Chain | 有序 `slots[]` + `gain/master/quality`；数组顺序就是 DSP 顺序 |
| Input | 链头独立输入源，不是 Slot，不参与 Slot 上限和重排 |
| Slot | 一个可加载处理文件的位置，本身无类型；数组下标是协议身份 |
| Model | Slot 的加载粒度，即一个 `.nam` 或 `.wav` 文件，不是整个 Tone |
| Tone / Pack | 一组元信息和一个或多个 Model 文件，用于浏览和选择 |
| Processing Type | 由扩展名派生：`.nam` = NAM，`.wav` = IR |
| Label | 由 Model 所属 Tone 的 gear 派生，仅用于显示 |
| Empty | Slot 存在且 `path: null`，没有恢复候选，不占 DSP |
| Bypass | 当前进程保留恢复候选，但该 Slot 暂不参与 DSP；不同于 Empty |
| Target Slot | DetailPane 加载 Model 时的目标 Slot；由最近一次有效 Slot 聚焦确定 |

标签文本按第 1.2 节显示为原生 `gear` token 的 uppercase 形式；只有 gear 元信息缺失或无法解析时显示 `SLOT`。标签不写入协议，也不限制文件放置位置。

## 3. 设计原则

### 3.1 工作台与稳定布局

- 第一屏是完整工作区，不增加欢迎页、功能介绍卡或装饰性区域。
- Input、Slot 列表、参数和提示条保持固定纵向顺序；增删 Slot 只改变 Slot 列表本身。
- 动态状态放左侧，固定动作靠右；状态变化不能让右侧 token 跳动。
- 后台刷新、主题切换、状态灯变化和长标题滚动不得抢焦点或改变控件尺寸。

### 3.2 输入等价

- 键盘、鼠标和可点击 action token 是同一动作的不同入口，结果必须一致。
- 单字母键按实际大小写显示；普通动作词和特殊键统一小写。
- 特殊键写作 `enter`、`esc`、`space`、`ctrl+enter`、`ctrl+z`；修饰键写作 `⌥↑`、`⌥↓`。
- 单击负责聚焦或一次动作；双击只承载明确的加载、打开或 bypass 语义。

### 3.3 不替用户判断链路

- 任意 gear 可以出现在任意 Slot，允许重复加载同一原生 gear 和非常规顺序。
- UI 可以显示派生标签；对合法 `.nam/.wav` 不自动移动、替换、合并或按 gear 拒绝，对未知扩展名或不支持的处理格式必须在写入 Slot 前拒绝。
- 重排只交换相邻数组项；不寻找“更合理”的位置。

### 3.4 临时表面与外部点击

- 统一术语为“临时表面收起”（`transient-surface dismissal`）：鼠标点击边界外称 `outside-click dismiss`，键盘或程序化焦点离开称 `blur-dismiss`。
- 临时表面的 inside boundary 包括触发它的 action、输入框、清除/确认 token 以及属于同一表面的结果或建议列表；点击这些区域不触发外部收起。
- 可安全收起的临时表面在外部点击或失焦时关闭编辑态、候选层和局部提示，并把焦点交给实际点击/聚焦的控件。关闭不等于清空：已经提交的 query 或筛选条件保留，未提交的编辑草稿按该控件的取消规则处理。
- Preset SearchBar 属于可安全收起的临时表面：点击 query 区域外的表格、DetailPane、其他 Pane、状态栏或空白工作区都关闭搜索编辑态；再次按 `/` 或点击 query 区域重新打开。点击 SearchBar 内部不关闭。
- 参数精确编辑同样接受 blur-dismiss，失焦取消未提交数值；搜索 query 则按键即时提交，因此失焦只关闭编辑态，不撤销过滤。
- 模态、确认、Pack Install、Uninstall 和 Preset Edit 是阻塞表面，外部点击不关闭、不提交、不丢弃；必须使用明确的 `esc`、取消或确认动作。完整 Pane 也不因点击其他 Pane 自动销毁，只按既定导航返回。

### 3.5 成功 mutation 后的统一刷新与视角保持

本规范把会改变共享数据源的成功操作定义为 mutation。安装、卸载、导入、删除、移入/移出 trash、preset 保存/重命名/删除以及其他产生持久状态变化的操作，都必须在提交完成后发布一次 `MutationCommitted`。查询、分页、verified 查询、播放控制、参数预览和没有实际状态变化的 no-op 不属于 mutation。

- `MutationCommitted` 只能在持久提交成功后发布；失败、取消、阻塞和未产生变化的操作不得发布。部分成功只发布一个事件，并且只携带实际成功的对象 key、操作类型和提交 revision。
- App 只提供一个 mutation refresh coordinator。一个提交对应一个刷新周期；只有带有相同提交 `revision` 的重复事件才合并。没有 revision 的不同事件必须按到达顺序分别 reconcile；同一事件对象重复投递可以合并。所有已注册页面实例（包括当前未激活但仍保留的页面）在每个刷新周期最多执行一次 `reconcile_after_mutation(event)`。轮询读到相同 fingerprint/revision 不得再触发可见刷新。
- 刷新前必须为每个页面实例保存 `ViewAnchor`：`screen_id`、active App tab、active `view_tab_id`、focused widget、cursor row key、cursor column、first visible row key、行内偏移、`scroll_x`/`scroll_y`、仍有效的 selection keys、confirmation state 和 Detail context key。row key 必须是稳定业务身份，不得使用 cursor index；例如 `local:<tone_id>`、`tone:<tone_id>`、`creator:<username>`、`m<model_id>`、`slot:<index>` 和 `preset:<preset_id>`。
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
| DetailPane / Pack | 刷新模型 rows 和安装状态，按 `m<model_id>` 恢复光标、Description/Selection 模式、target 和视口；不得自动打开新的 TonePicker。 |
| ChainPanel / Presets | 同步受影响文件、Slot、引用和 active/dirty 状态，保留当前对象、焦点、target、selection 和视口；无关页面不得被切换或清空。 |

### 3.6 Pane view tab、SearchBar 和 Type 过滤

当同一 Pane 存在多个同级内容（例如 Library 的 LOCAL、TONE3000、TOP CREATORS，或 DetailPane 的 Description、Pack）时，必须在同一 Pane 内使用 view tab strip。view tab 是内容切换协议，不是新的 screen，也不是底部 action token。

- 每个 view tab 有稳定 `view_tab_id`、显示 TAG 和独立状态。激活 view tab 只替换该 Pane 的内容，不 push screen、不切换 App 主 tab、不改变其他 Pane 的焦点。
- 鼠标点击 TAG 激活对应 view tab。view tab strip 整体只占一个 focus stop；`tab/shift+tab` 只按视觉顺序进入或离开该控件，不在 TAG 之间移动，也不激活 TAG。当前 Pane 不处于文本输入或模态编辑时，`[`/`]` 激活前后 TAG；导航首尾循环，`]` 从最后一个 TAG 回到第一个，`[` 从第一个 TAG 回到最后一个。`←/→` 不承担同级 view 切换语义。
- 每个 view tab 独立保留 query、sort、type filter、cursor row key、selection、Detail context 和 viewport；切回时恢复该 tab 的状态。切换 tab 不触发全局 mutation refresh，也不清除其他 tab 的已提交 query。
- SearchBar 是所有可搜索列表的固定一行：`query field + sort select + type select`。Library 的空 query 示例使用 `@tone3000 #clean author:tone3000 tag:clean make:"Fender Reverb"`，完整提示 `@作者`、`#标签`、`author:`、`tag:`、`make:` 和带空格值的引号写法；Preset 的空 query 示例使用 `name:clean note:live file:SVT id:101`，完整提示名称、备注、文件名和 Tone/Model ID 字段。SearchBar 宽度固定为 Pane content region，内容不能参与轨道尺寸计算：full/standard 使用固定 query 轨道 + `sort/type: 24 cells`，compact 使用固定 query 轨道 + `sort/type: 18 cells`；query、sort 和 type 只使用略有区别的背景区分，不使用下划线、外围框或分隔点。编辑时长 query 在自身轨道内水平滚动，未编辑时显示对应页面的完整示例提示；sort 和 type 始终贴右且不移动。
- Type select 是即时、可恢复的单选局部状态：选项为 `ALL + 当前数据中的非空原生类型`，选择项高亮并立即过滤，`ALL` 恢复不过滤。Type 选项只出现在 SearchBar，不出现在结果表头；切换不改变当前 view tab、query、sort、cursor 或视口，除非结果中已不存在该 row。
- TOP CREATORS 选中作者后，激活 Library 的 TONE3000 view tab 并提交 `@作者名` query；不得通过左右移动或打开新的 TONE3000 screen 完成跳转。

## 4. 视觉系统

### 4.1 主题与固定状态色

布局结构色由当前主题提供：`$background`、`$surface`、`$surface-hover`、`$primary`、`$accent`、`$secondary`、`$warning`、`$text`、`$text-muted` 和 `$text-disabled`。

只有三个跨主题固定状态色：

| Token | 固定值 | 语义 |
|---|---:|---|
| `$success` | `#8fb573` | active、success、正常电平 |
| `$error` | `#d96a55` | bypass、error、danger、削波 |
| `$state-idle` | `#8a817a` | idle、empty、unavailable |

- 组件不得写其他裸色值；可通过主题系统派生 blend、lighten、darken 和 opacity。
- 裸色只允许出现在固定状态色注册表和主题注册表；组件、Pane、提示条和 action token 只能引用语义 token，不得复制主题 hex。
- 普通文字对比度至少 4.5:1；bold 短标签、焦点边框、状态符号和控件至少 3:1。
- 颜色不是唯一信号；active、bypass、empty、warning 和 error 必须同时有符号或文字。

### 4.1.1 音箱灵感主题候选

这些色值是从参考图的箱体、面网、旋钮、金属件和标识中压缩出的近似色，不是对照片像素或品牌官方色票的承诺。纹理、木纹、布面和高光不直接进入 UI；每个主题只保留一组稳定的纯色源值。

| Theme ID | 视觉来源 | 适合的气质 |
|---|---|---|
| `orange-tolex` | 图 1：橙色箱体、米色面网、黑色控制面板 | 默认主题；暖、醒目、现场感强 |
| `tweed-brass` | 图 2/4：麦金色面板、旧黄布面、烟草棕木件 | 复古、柔和、长时间使用不刺眼 |
| `diamond-noir` | 图 3：黑色箱体、金色线条、褪色青绿与酒红菱格 | 深色、精致、强调层级和细节 |
| `blackface-silver` | 图 5：黑色边框、银灰面网、冷白金属件 | 中性、清晰、适合密集信息界面 |
| `british-green-oxblood` | 补充组合：深绿箱体、羊皮纸、金色和酒红 | 稳重、舞台感、低饱和复古 |
| `surf-cream-coral` | 补充组合：冲浪绿、奶油白、珊瑚橙和旧金 | 明亮、轻松、适合作为第二套浅暖主题 |

下表只定义 Textual `Theme` 的源 token。`$text` 使用 `$foreground`；`$surface-hover` 使用 `$panel-lighten-1`；`$text-muted` 和 `$text-disabled` 使用 `$foreground` 的相对透明度派生，不为每个主题再增加一套裸色。

| Theme ID | `$background` | `$surface` | `$panel` | `$boost` | `$foreground` | `$primary` | `$secondary` | `$accent` | `$warning` |
|---|---|---|---|---|---|---|---|---|---|
| `orange-tolex` | `#17110E` | `#241912` | `#312015` | `#492A18` | `#F4E5D0` | `#F07820` | `#A8774B` | `#FFB04A` | `#E0A33A` |
| `tweed-brass` | `#181510` | `#282118` | `#392C20` | `#4B3A27` | `#F4E5C4` | `#D2A65A` | `#9A7549` | `#EBC878` | `#D7923F` |
| `diamond-noir` | `#101315` | `#181C1F` | `#23292C` | `#31383B` | `#EFE9DC` | `#D7B65E` | `#789A9C` | `#B95F78` | `#D89A4A` |
| `blackface-silver` | `#111416` | `#1A1E21` | `#252A2D` | `#343B3E` | `#EFF0EB` | `#D4D8D4` | `#90999A` | `#9CC2C4` | `#D8A248` |
| `british-green-oxblood` | `#101612` | `#18221A` | `#253126` | `#334333` | `#EDE4D3` | `#D0AD68` | `#789176` | `#B85D5C` | `#D69A46` |
| `surf-cream-coral` | `#111719` | `#1C2927` | `#293A35` | `#385047` | `#F5EAD8` | `#95C3B1` | `#B9A98D` | `#E3795B` | `#D9AA52` |

实现和验收规则：

- `orange-tolex` 是 v0.2 的默认候选，因为它最直接承接图 1 的橙色箱体，同时保留深色工作台的长时间可读性。
- `$primary` 负责当前面板边框、标题和主要光标；`$accent` 负责编辑光标、hover 反色和可执行 action token；二者不能交换职责。
- `$secondary` 只能用于非当前面板光标、滚动条和安静边界；不能替代 `$success` 或 `$warning`。
- `$warning` 需要同时与 `$success`、`$error` 可辨，且不能只靠色相表达状态；状态文字和符号仍按 4.1 节执行。
- 每个主题至少验证正文对 `$background`、正文对 `$panel`、`$primary` 对 `$background`、`$accent` 对 `$background` 的对比度；失败时优先调亮文字或主题源色，不修改固定状态色。
- `british-green-oxblood` 和 `surf-cream-coral` 是补充设计组合，不表示仓库已经支持对应主题；主题注册、`t` 切换、截图和 `NO_COLOR` 验收完成前不得宣称已实现。

### 4.2 文本层级

| 层级 | 样式 | 用途 |
|---|---|---|
| 产品标题 | bold + `$primary` | Header 中 `GigBuddy` |
| 面板标题 | uppercase + bold；聚焦 `$primary`，非聚焦 `$text-muted` | 主面板 |
| 内容标题 | bold + `$primary`，单行 marquee | tone、model、creator、preset |
| Slot 序号 | `$text-muted`，固定两位 | `01`–`06`，帮助识别 DSP 顺序 |
| Slot 标签 | uppercase 原生 gear token + bold | `AMP`、`AMP-CAB`、`CAB`、`EXPERIMENTAL`、`FULL-RIG`、`OUTBOARD`、`PEDAL`、`SPACE`、`SLOT`；未来新值同样自动 uppercase |
| Tone Detail Type | uppercase 原生 gear token + bold 专属色 | `AMP` `$primary`、`AMP-CAB` `$accent`、`CAB` `$success`、`PEDAL` `$success`、`SPACE` `#6aa9e8`、`OUTBOARD` `#5bb6a8`；`SPACE` 属于 IR 文件处理类型，摘要徽标和 `Type` 行使用同一映射，未知值使用 muted |
| 正文/数据 | normal `$text` | description、文件名、参数 |
| 状态 | 对应语义 token + 符号或文字 | `BYPASS`、`NONE`、错误 |
| action token | `$text-muted`；hover 时主题反色 | 可点击快捷动作 |

正文不用全大写；全大写仅用于面板名、短标签和短状态。长标题 marquee，长正文换行并滚动；不使用负 letter spacing 或终端字体缩放。

### 4.3 焦点与选择

- hover、focus、selected、active 和 target 是不同状态，不能只靠同一种背景表达。
- 当前面板使用 `$primary` 边框；键盘光标使用最亮的 `$primary`；hover 使用 `$surface-hover`。
- Target Slot 除正常焦点样式外，在序号旁显示 `TARGET` 短标；焦点离开 ChainPanel 后仍可识别加载目标。
- 焦点行不显示字面量 `>`；`>` 不属于状态、target 或选择协议。
- Pack 光标表示浏览行；`▶` 表示正在处理的当前 Slot 文件；`▷` 表示 bypass 恢复候选。
- `[ ]/[x]` 只表示批量安装或卸载选择，不表示 Slot target 或 active。

### 4.4 无颜色模式

- 光标 reverse + bold，hover underline，Target Slot 显示 `TARGET`，不能只用边框区分。
- view tab 的 active TAG 使用 reverse + bold；非 active TAG 保留可见文本，hover 使用 underline 或背景点亮。
- active Slot 使用 `●`，当前 Pack 文件使用 `▶`；bypass Slot 使用 `● BYPASS`，仅其 Pack 恢复候选文件使用 `▷`；empty 使用 `○ NONE`。
- warning、error、MUTED 均保留文字；disabled 显示 `(unavailable)` 或 `(not downloaded)`。
- 所有页面和 0–6 Slot 状态在 `NO_COLOR` 下都必须可操作且不崩溃。

### 4.5 Pane 标签、统一 SearchBar 与二级表面样式

Pane 标题和 view tab 必须直接占用 Pane 外框的左上边框线，统一使用 `PANENAME  ──  TAG1 / ACTIVE TAG / TAG2`；不得在内容区另起标题行。Pane name uppercase + bold，Pane name 与 TAG 之间固定显示 `──`，TAG 为可交互文本，TAG 之间用 `/` 分隔，不显示 `·`。active TAG 只使用 underline，未选中 TAG 使用 dim 变暗，TAG hover 时 underline 或主题 accent 点亮但不改变尺寸。Pane name 不可点击，只有 TAG 有命中区。`[ / ] select tab` 不显示在顶边标题中，统一作为最右侧稳定 action token 放在 Pane 右下角提示带。TAG 不使用独立边框、圆角胶囊或嵌套卡片。

可搜索 Pane 在 view tab strip 下方固定一行 SearchBar，逻辑结构只有 `query field + sort select + type select`（适用时）。Library 示例为 `SEARCH: @tone3000 #clean author:tone3000 tag:clean make:"Fender Reverb"  SORT: Trending  TYPE: AMP`，Preset 示例为 `SEARCH: name:clean note:live file:SVT id:101  SORT: Updated`。query、sort 和 type 同行显示；普通状态使用 `$surface`，query focus、sort open 或 type open 使用略有区别的 `$surface-hover`/`$accent` 背景，不使用下划线、输入框外围框或阴影。SearchBar、表格起始位置和底部提示位置固定，搜索打开或关闭不能让下方表格跳动。

| SearchBar 状态 | 视觉 | 行为 |
|---|---|---|
| 未激活 | `$surface` 背景；显示 `SEARCH`、query 摘要和 `SORT`、当前 sort；不显示输入光标 | `/` 或点击 query 激活；当前 query/filter 保留 |
| query 激活/编辑 | query 区域 `$surface-hover` 或 `$accent` 背景；query 用 `$text`，保留光标和清除 token | 接收文字和退格；`enter` 提交；`tab` 进入 sort；第一次 `esc` 关闭编辑但保留已提交 query，未激活时再次 `esc` 才清除 query |
| sort 激活 | sort 区域 `$surface-hover` 或 `$accent` 背景；当前 sort 清楚可见 | 点击或 `enter` 打开选项；`↑/↓` 移动；`enter` 选择并立即应用；`esc` 关闭 |
| 有结果 | SearchBar 保持一行；数量放在表格动态状态或提示带，不挤压 query/sort | 结果即时过滤；不改变 active、dirty 或 live Chain |
| 无结果 | `$state-idle` + `no matching results`；保留 query | 不制造空行；修改 query 或过滤恢复 |
| 查询错误 | `$warning` + 具体原因；保留可修正 query | 不覆盖旧结果，修正或 `esc` 后恢复 |

- Type/gear 只放入 SearchBar 的 Type select，不位于搜索结果表头；Author 和其他列不得出现过滤菜单。Type select 是固定轨道的稳定控件；选项显示 uppercase 标签，但使用原生 token 精确匹配，未知原生值也必须自动出现。
- Type select 不改变 SearchBar、表格起始行和底部提示带高度；过滤变化只更新 rows 和动态 Type 状态，表头点击不触发过滤。
- SearchBar 输入框、sort 区域、清除 token 和同一搜索表面的建议列表属于同一 inside boundary；点击外部按第 3.4 节收起。
- 二级表面（Description、Pack Selection、Slot Warning、InputSource 等）沿用父 Pane 的 `$surface`、字号层级和固定行高，通过 view tab TAG 切换同一上下文，不再使用 `←/→` 切换；不使用卡片套卡片或独立背景色。
- 二级菜单/下拉菜单是触发控件旁的 transient surface：与触发控件对齐，使用 `$surface`、一层 `$primary` 边框和固定行高；当前行用 `$surface-hover` + `$primary` 光标，disabled 行用 `$text-disabled`，菜单展开不得改变父 Pane 尺寸。
- 二级菜单支持 `↑/↓` 移动、`enter` 选择、`esc` 关闭；单击菜单项等价于 `enter`，点击触发控件只打开或再次聚焦，点击菜单边界外按 `outside-click dismiss` 关闭。菜单选择是否立即写入由所属 Pane 规则决定，不能因为点击路径而改变键盘路径。
- 需要确认、编辑草稿或危险操作的表面升级为模态：底层 `$background` 降暗，模态使用同一主题 surface 和 `$primary` 边框；外部点击不关闭，必须显式 `esc`、取消或确认。
- Presets 不提供独立二级浏览菜单；Preset search 是主面板内的 SearchBar，Preset Detail 是右侧 DetailPane，不再增加第三个 Preset 浏览层。

### 4.6 滚动条

- 所有 `DataTable`、`Tree`、`ScrollableContainer` 和 `VerticalScroll` 使用同一套轴向尺寸：横向滚动条高度 1 个终端行，竖向滚动条宽度 1 个终端列。两轴都保持窄轨道，避免抢占密集 Pane 的内容空间。
- 实现必须使用 `scrollbar-size-horizontal: 1` 和 `scrollbar-size-vertical: 1` 等轴向属性，避免误读 `scrollbar-size` 的参数顺序；局部 Pane 不得另设尺寸。
- 滚动条轨道、滑块和 hover 色只引用主题 token；统一单格尺寸不得改变 Pane 的固定高度、底部提示位置或 action token 顺序。

## 5. 应用布局与全局导航

主布局保持 Header、Library、ChainPanel、DetailPane、Presets、InterfaceBar。宽屏不使用卡片嵌套；窄屏按面板优先级折叠，不横向滚动。

尺寸按下表从上到下匹配，命中第一行即停止；宽度和高度任一不足都会进入更保守的布局。

| 尺寸条件（`w` × `h`） | 行为 |
|---|---|
| `w ≥ 120 且 h ≥ 40` | 完整双列工作区，ChainPanel 可同时显示 6 个 Slot |
| `w ≥ 100 且 h ≥ 36` | 压缩次要列和说明，Slot 行信息仍完整 |
| `w ≥ 80 且 h ≥ 32` | 单列或分页面板；Target、序号、状态和主要动作必须保留 |
| 其他尺寸 | 显示最小尺寸提示，只保留退出和 resize 响应 |

全局键位：

| 键位 | 动作 | 约束 |
|---|---|---|
| `tab` / `shift+tab` | 面板和主要控件前后导航 | 模态内循环，不穿透 |
| `↑/↓` | 当前列表移动光标 | ChainPanel Slot 行例外：切同 pack Model；Slot 间聚焦使用 `tab/shift+tab` |
| `[` / `]` | 激活当前 Pane 的前一个 / 后一个 view tab | 文本输入和模态编辑时不截获；首尾 no-op；TAG 点击等价 |
| `/` | 聚焦当前来源搜索 | Presets 面板聚焦 Preset search；编辑或模态输入时不截获 |
| `ctrl+p` | command palette | 确认流程中不叠加 |
| `t` | 下一主题 | 不改变固定状态色和焦点 |
| `ctrl+z` / `ctrl+shift+z` | undo / redo chain 应用 | 覆盖 Slot 和参数，不覆盖 Input |
| `ctrl+c` | 复制或确认退出 | 有文本选择时复制并清选择；无选择时 1.5s 内连续按两次退出 |

Preset 不占用全局写入快捷键。任何页面需要打开 Preset Load/Save/Save As 时，通过点击 Presets 面板、面板内键位或 `ctrl+p` command palette 进入；`p`、`ctrl+s`、`ctrl+shift+s` 在全局均不绑定。

上下文快捷键：

| 作用域 | 键位 | 动作 |
|---|---|---|
| ChainPanel / InputSource | `space` / `s` / `l` | 播放暂停 / 停止 / 循环；instrument 模式显示不可用原因 |
| Input / InterfaceBar | `x` | 切换 MUTE；点击 `MUTE` token 与 `x` 等价 |
| 普通可多选表格 | `space` / `a` | 选择当前行 / 全选或全不选 |
| Presets 面板 | `/` | 聚焦本地 Preset search |
| Presets 面板 | `enter` / `s` / `n` / `e` / `r` / `d` / `space` / `a` | 加载 / 保存 active / 新建 / 编辑 / 重命名 / 删除 / 选择 / 全选 |
| Preset Edit 模态 | `enter` / `ctrl+enter` / `esc` | 保存 draft / 保存并加载 / 丢弃 |

关闭页面或模态后恢复来源控件和视口。后台刷新、外部配置、主题和通知不得抢焦点；来源已不存在时回到同面板最近的有效行。

## 6. Library

Library 保持 LOCAL、TONE3000、TOP CREATORS 等现有来源和筛选。所有列表满足：

- Library 外框左上边框线使用 `LIBRARY  ──  LOCAL / TONE3000 / TOP CREATORS` view tab strip；当前 active TAG 使用下划线，未选中 TAG 变暗，内容区不得重复占一行。DetailPane tone 详情使用 `TONE DETAIL  ──  DESCRIPTION / PACK`。点击 TAG 只切换内容，不 push screen；`[ / ] select tab` 固定显示在右下角统一提示带。
- 单击只聚焦并更新 DetailPane；`enter` 或双击执行主要动作。
- Library tone 的 `enter` 或双击直接把焦点送入 DetailPane 的 PACK view；不先经过 Description。
- 进入 PACK 后只尝试首个 Model 一次：已有本地文件则加载到当前 Target Slot；没有本地文件则在左上角通知未下载，不自动安装、不 push 二级页面。
- Description 仍由单击高亮和 Detail view tab 进入；Library Enter 不改变已有 Target Slot。
- SearchBar 一行包含当前 view tab 的固定轨道 query、sort 和 type；搜索、sort、Type filter、分页和缓存响应携带 query/page/type-filter 身份，晚到结果不能覆盖当前页面。
- `r` 刷新当前来源；刷新保留有效详情，失败时显示可恢复状态。
- `↓ more` 是条件 action token：位于动态状态之后、稳定动作之前；出现/消失不改变稳定动作后缀的右边界，点击它等价于当前列表按 `↓` 加载下一页。
- TOP CREATORS 聚焦展示 Creator Detail，不把 creator 错连到 tone。

Library 选择 Tone 不自动决定 Slot 类型或创建 Slot。首个已下载 Model 的自动尝试和 Pack 文件行的明确加载都写入当前 Target Slot；没有 Target Slot 时只提示，不改链。

| 来源 | 列/内容 | `enter` / 双击 | 稳定 action 后缀 |
|---|---|---|---|
| LOCAL | Sel、Title、Type、DL、Fav、Arch、Files、Up、Author | 直接打开 Local Pack；首个已下载 Model 自动尝试加载到 Target Slot | `a all/none · space select · d uninstall · enter open` |
| TONE3000 | 远程 tone、类型、统计、作者、下载状态 | 直接打开 Remote Pack；首个 Model 已下载则加载，否则左上角提示未下载 | `r refresh · enter open` |
| TOP CREATORS | Rank、Creator、Tones、Downloads、Fav、Models | 激活 TONE3000 view tab 并真实搜索 `@creator` | `r refresh · enter search` |

- Type/gear 筛选位于 SearchBar 的 Type select，不位于结果表头。筛选值从当前缓存或远程结果中的非空原生 `gear` 动态生成；不维护本地闭合集合。未知的新值直接作为选项，其标签按 uppercase 规则显示，并按原生 token 精确匹配。
- LOCAL 的 Sort 只有 `Title`、`Newest added`、`Oldest added`；Title 按标题升序，added 两项按本地 `imported_at` 的新旧排序，分页保持该顺序。

- LOCAL 搜索支持 `@author`、`#tag`、`author:`、`tag:`、`make:"..."`；Author 显示真实 `@作者名`，未知时显示明确占位。官网确认过的作者使用本地正向缓存并显示 `✓`，所有作者展示入口保持一致。
- TONE3000 缓存键为 `(view_tab_id, query, sort, type_filter, author)`，FIFO 上限 20；启动预取一次，之后只有新 query、未命中 Type 过滤组合、load more 或 `r` 访问网络。
- TOP CREATORS 使用官网 `/top-creators` 同源的 `user_public_counts` 排行榜；Most Tones、Most Downloads、Most Favorites、Most Models 均为服务端排序和聚合值。加载更多只按接口顺序追加未见 creator，不得改写或重排已有行，也不得主动设置滚动位置；光标和视口由原位追加自然保持。
- LOCAL 批量卸载使用 `[ ]/[x]`；活动 Chain 任一 Slot 使用的文件都阻塞卸载，Preset 引用需要二次确认，成功项移入可恢复 trash。

## 7. ChainPanel

### 7.1 结构

从上到下固定为：

1. INPUT 行。
2. 0–6 个 Slot 的动态列表。
3. Slot 添加入口；未满显示 `+ add slot`，满 6 个时显示 disabled `6/6 slots`。
4. `GAIN`、`MASTER`、`QUALITY` 参数行。
5. 右下角单提示带：动态状态前缀在左，稳定 action token 后缀在右。

INPUT 不是 Slot，不编号，不可删除、移动或 bypass。零 Slot 是合法空链；此时 INPUT 下方直接显示 `+ add slot`，DetailPane 不残留旧 pack。

Slot 行固定结构：

```text
01  ●  AMP        Tone title                     model.nam          [↑] [↓]
02  ●  CAB        Tone title                     cab.wav  BYPASS     [↑] [↓]
03  ○  NONE                                      empty
```

- 行序就是 DSP 顺序；序号始终根据当前数组下标重新生成。
- 文件名和标题截断不能挤掉序号、状态、标签或行尾按钮。
- `amp-cab` 是一个 Slot 的原生组合标签，显示为 `AMP-CAB`，不渲染成两个 Slot。
- 空 Slot 无 tone、pack 或 active 文件上下文。

ChainPanel 采用截图确定的 fieldset 式层级：最外层是一层 `TONE CHAIN` 主边框；INPUT 和每个已创建 Slot 各有一层紧凑分组边框，短标题嵌入分组上边线。普通格式为 `状态灯 + LABEL`；Target 等标题附加状态使用 ` - `，例如 `PEDAL - TARGET`，不使用 `·`。Bypass 不属于标题附加状态。INPUT 固定显示 `INPUT`；非空 Slot 的 `LABEL` 是 uppercase 原生 gear，Empty 显示 `SLOT` 并在内容区显示 `NONE`。标题不得使用固定 AMP/CAB/COMP/OD 枚举，也不得因未来新增 gear 改变组件结构。

TONE CHAIN 内不渲染 `▶` 或 `▷`：Active Slot 显示绿色 `● LABEL`；Bypass Slot 标题显示红色 `● LABEL`，第二行显示 `filename  BYPASS`，其中 `BYPASS` 为红色 bold；Empty Slot 显示 `○ SLOT` 和内容 `NONE`。`▶/▷` 仅属于 DetailPane 的 Pack 文件列表，用于区分当前处理文件和 bypass 恢复候选，不能出现在 ChainPanel 标题、内容行或右侧动作列。

- 分组标题使用 bold `$text-muted`，target/focus 时按第 4.3 节增强；状态灯与标题同行且不挤压边框。边框使用主题 surface/border token，只有当前 ChainPanel 外框使用 `$primary`，不能把每个 Slot 都画成高亮卡片。
- 每个 Slot 分组的内容区固定为两行：第一行显示主要 Tone/Model 信息，第二行显示原生标题或文件名等次要信息；缺失信息保留空白或明确状态，不用占位行改变高度。Model 切换仍使用右侧箭头列，内容截断不得推动该动作列。
- 每个 Slot 分组的右下角边框提示提供 `delete · tone · bypass/restore · move ↑ · ↓`；Empty Slot 隐藏不适用的 `bypass`，空间不足时按完整 token 整体压缩或隐藏，不显示半截动作。`tone` 聚焦 LOCAL，让用户为该 Slot 选择 Tone。`move ↑` 和 `↓` 是两个独立可点击动作。
- 0–6 个 Slot 使用同一分组组件和稳定间距；添加、删除、Loading、Bypass、Empty 或标签长度变化不得改变其他 Slot 的标题位置、内容起始列或动作列。窄屏可隐藏次要信息，但保留序号、状态灯、uppercase 标签和主要动作。焦点仅改变背景、边框或反色，不在内容行前增加 `>`。

### 7.2 Slot 状态机

| 状态 | 协议 | 行显示 | Pack 标记 | 可执行动作 |
|---|---|---|---|---|
| Active | `{path: <file>}` | `$success` + `●` | 当前文件 `▶` | 换文件、bypass、移动、删除 |
| Bypass | `{path: null}` + 进程内候选 | 红色 `● LABEL`；第二行 `filename  BYPASS`，`BYPASS` 为红色 bold | 候选文件 `▷` | 恢复、换文件、移动、删除 |
| Empty | `{path: null}`，无候选 | `$state-idle` + `○ NONE` | 无 pack | 加载、移动、删除 |
| Loading | 旧值尚有效 | 原状态 + `loading…` | 原标记保留 | 可继续导航，拒绝重复提交 |
| Error | 旧值保留 | 原状态 + error | 原标记保留 | 重试或选择其他文件 |

- Bypass 和 Empty 的落盘值相同，但 TUI 进程内状态不同；重启或合法外部写入 `path:null` 后没有候选，按 Empty 显示。
- bypass 候选按当前数组项随重排一起移动；删除该 Slot、任何整体 `slots[]` 替换或进程退出时清除对应候选。
- 加载或链重建失败必须保留修改前整个有效 Chain，不得留下半应用顺序。
- 仅 `.nam/.wav` 且处理格式受支持的普通文件可以进入 Slot。交互加载在写入前校验；不支持时显示错误并保持原 Slot、target、候选和 Chain 不变。

### 7.3 Target Slot

- 单击、`tab/shift+tab` 聚焦或重排后的当前 Slot 都将其设为 Target Slot。
- 焦点进入 Library、DetailPane 或 Presets 后，Target Slot 保留并显示 `TARGET`。
- Input、参数、creator、preset 或 remote row 不能覆盖 Target Slot。
- Target 被删除后，优先选择删除位置的新 Slot，其次前一个 Slot；已经没有 Slot 时清空 target。
- 重排时焦点和 target 跟随用户正在移动的内容到新行；写协议时仍只记录新数组顺序，不创建稳定 id。
- Pack 执行加载时没有 Target Slot：拒绝并提示 `add or select a target slot`；不得自动创建 Slot，也不得覆盖任意 Slot。
- 任何整体 `slots[]` 替换，包括外部写入、Preset load、undo 和 redo，都先清空 target 和全部 bypass 候选；不按旧下标或重复 path 猜测身份。用户重新聚焦 Slot 后再建立 target。

### 7.4 Slot 操作映射

| 操作 | 键盘 | 鼠标 | 结果 |
|---|---|---|---|
| 聚焦 | `tab/shift+tab` | 单击 Slot 行 | 在 Input、Slot、参数控件间导航；更新 target 和 DetailPane |
| 添加 | `+` | 点击 `+ add slot` | 末尾追加 Empty Slot 并聚焦 |
| 删除 | `d` | 点击 `delete` | 删除当前 Slot 行；不卸载 Library 文件 |
| 选择 Tone | 无固定快捷键 | 点击 `tone` | 聚焦 LOCAL，让用户选择并加载 Tone |
| 上移 | `⌥↑` | 点击 `move ↑` token | 与上方 Slot 对调，焦点跟随内容 |
| 下移 | `⌥↓` | 点击 `↓` token | 与下方 Slot 对调，焦点跟随内容 |
| 前一 Model | `↑` | 点击行尾 `↑` | 同 pack 前一已下载 Model |
| 后一 Model | `↓` | 点击行尾 `↓` | 同 pack 后一已下载 Model |
| bypass/恢复 | `enter` | 双击 | Active ↔ Bypass；Empty 的 `enter`/双击打开来源选择 |

- `↑/↓` 只在 Active 或 Bypass Slot 行切同 pack Model；Input、Empty Slot 和参数控件不截获它们。到边界不循环并显示原因；Bypass 时切到其他 Model 会加载该文件并恢复处理。
- Slot 间键盘导航只用 `tab/shift+tab`，不能让 `↑/↓` 同时移动 Slot 焦点。
- `⌥↑/⌥↓` 只交换相邻项，不跨 INPUT；首项上移、末项下移均 no-op + 明确提示。
- 不支持拖拽；拖动手势不得产生重排、加载或删除。
- `d` 删除 Slot 本身；清空后保留位置的需求通过添加 Empty Slot 表达，不提供隐式 unload。`delete` 只删除链上的 Slot，不删除本地文件。
- Active、Bypass 和 Empty 都可删除；删除最后一个 Slot 后进入合法的零 Slot 状态。
- `+` 达到 6 个后禁用；协议和引擎仍必须独立拒绝第 7 个 Slot。
- ChainPanel 主边框右下角提供 `save · clear all slots`。`+ add slot` 仍在 ChainPanel 内容区；`save` 打开 SAVE 二级菜单；`SAVE HERE` 覆盖 active Preset，必须显示 `"<name>" already exists. Overwrite it?` 并再次 `enter overwrite`；`SAVE AS NEW` 与 `Preset name:` 输入框同一行，名称冲突使用相同确认页。
- `clear all slots` 必须二次确认，显示 `Are you sure you want to clear all Slots?`；确认只清空当前链上的 Slot，不删除本地文件或 Preset。
- `tab/shift+tab` 在 ChainPanel 内按 `INPUT → Slot 01…Slot nn → + add slot → GAIN → MASTER → QUALITY` 顺序循环；反向键按逆序返回。动作 token 可点击，但不额外插入 Tab 顺序。

### 7.5 Pack 加载与 bypass

- Local Pack 中，来自任意原生 gear 的合法文件都可通过 `enter` 或双击加载到 Target Slot，并退出该 Slot 的 bypass。
- 重复选择 Target Slot 当前 `▶` 文件进入 bypass；重复选择其 `▷` 文件恢复。
- 同一文件存在于其他 Slot 不影响当前判断；“重复”只比较 Target Slot。
- Pack 不按 Target Slot 当前标签或处理类型过滤；用户可以把 pack 中任意合法 `.nam/.wav` 文件加载到任意 Slot。
- Remote Pack 中未安装文件的 `enter`/双击只打开 Pack Install，不创建、替换或 bypass Slot。
- 安装成功后仍留在 Remote Pack，但已安装行立即标记 `installed`，该行的 `enter`/双击改为加载到 Target Slot；安装失败保留原状态并提供 retry。安装过程本身不写 Chain。
- Pack 浏览行与 Target Slot active 行可以分离；后台 refresh 不得把用户浏览光标持续拉回 `▶/▷`。

### 7.6 参数

| 参数 | 减少 | 增加 | 键盘步长 | 鼠标步长 | 值域 |
|---|---|---|---:|---:|---:|
| gain | `g` | `G` | 0.10 | 0.50 | 0–10 |
| master | `m` | `M` | 0.05 | 0.50 | 0–10 |
| quality | `q` | `Q` | 0.05 | 0.50 | 0–1 |

键盘按键每次使用表中的键盘步长。鼠标短按或单击在按下时立即改变 `0.50`；继续按住时，前 `200ms` 只保留这一次变化；随后按住时间分段加速：`200–600ms` 每 `120ms`、`600–1200ms` 每 `80ms`、超过 `1200ms` 每 `60ms` 重复一个鼠标步长。重复调度使用单调按下时间，延迟帧不补发成跳跃；松开、移出、失去捕获或到边界立即停止，合成 click 不再追加一步。长按期间数值先在 UI 中立即更新，链写入在后台按最多每 `80ms` 合并提交，松开时强制提交最终值。单击数值进入最多两位小数的精确编辑，`enter` 应用、`esc` 或失焦取消。点击中间 `·` 恢复协议默认值（gain/master/quality 均为 `1.0`）；需要设置为 `0` 时使用减少键或精确编辑。参数属于 Chain，不随 Slot target 改变。

`quality` 是链级 NAM 质量因子：所有支持 quality scaling 的 `.nam` Slot 使用同一个当前值；`.wav` IR 忽略该参数。若某个 NAM 不支持 scaling，使用其默认质量，并在该 Slot 行追加 `quality unsupported` warning，不改变 Active/Bypass/Empty 的基础状态，也不阻塞其他 Slot。

ChainPanel 主边框稳定动作后缀为 `save · clear all slots`。每个 Slot 边框的稳定动作后缀为 `delete · tone · bypass/restore · move ↑ · ↓`；Model 切换仍由右侧 `↑/↓` 箭头列提供。窄屏先以完整 token 保留核心动作，再缩写说明词或隐藏低优先级 token；动态前缀只显示 `n/6 slots`、loading 或 error，不显示 target。

## 8. DetailPane 完整对应关系

DetailPane 跟随实际焦点对象；Target Slot 仅决定 Pack 加载位置，不强制 DetailPane 永远显示 target。

| 来源/状态 | 页面 | 内容 | 主要功能 |
|---|---|---|---|
| 无有效选择 | Empty | 空态文字，无残留背景 | 无动作 |
| LOCAL tone 聚焦 | Tone Description | 标题、tone/model id、description、作者认证 | 阅读/复制；Library `enter`/双击直接进入 Local Pack |
| LOCAL tone Selection | Local Pack | 文件、架构、大小、下载、Target Slot、`▶/▷` | 加载、切换当前文件 bypass、`i/u` |
| TONE3000 tone 聚焦 | Remote Description | 远程元信息和 description | 阅读/复制；Library `enter`/双击直接进入 Remote Pack |
| TONE3000 Selection | Remote Pack | 远程文件、架构、下载状态、`installed` 标记 | 首个已下载 Model 自动尝试；批量选择、安装、卸载；未安装文件进入 Pack Install，已安装文件可加载 |
| Active Slot 聚焦 | Slot Pack | Slot 序号、派生标签、所属 pack、`▶` 当前文件 | 加载其他文件、重复选择 bypass、`i/u` |
| Bypass Slot 聚焦 | Slot Pack | Slot 序号、`BYPASS`、原 pack、`▷` 候选 | 重复选择恢复；其他文件加载并恢复 |
| Empty Slot 聚焦 | Empty Slot | Slot 序号、`NONE`、Target 状态 | 引导从 Library 选 Tone；不残留旧 pack |
| INPUT 聚焦 | 保留当前 DetailPane | INPUT 状态仍在 ChainPanel | 双击打开 InputSource |
| TOP CREATORS 聚焦 | Creator Detail | 作者、verified、完整多行 bio 和统计 | 阅读/复制 |
| PRESETS 行聚焦 | Preset Detail | 名称、active/dirty、按序 Slots、参数、note | 预览；`enter` load、`s` save、`e` edit、`r` rename、`d` delete 由 Presets 面板执行 |
| 解析/应用错误 | Error Detail | 明确字段、Slot 序号和错误原因 | 返回来源修正，不做隐式修复 |

Tone Description 与 Pack Selection 是同一 Detail context 下的 view tabs；点击 TAG 或使用 `[/]` 激活。`tab/shift+tab` 只做焦点遍历，`←/→` 不切换这两个 view。Library `enter`/双击直接进入 Pack；没有可用 Model 时留在 PACK 并说明原因。Pack 表不显示独立 TONE id 列；标题超长 marquee，正文可滚动。

Pack 行中的 `▶` 和 `▷` 只针对 Target Slot。从 Library 打开的 Pack 显示 `target 04`；从 Slot 打开的 Slot Pack 中 viewing 与 target 必须相同，因为聚焦该 Slot 会同时更新 target。

### 8.1 Slot 状态 × Pane 总矩阵

这里的“Slot 种类”指运行时状态，不是任何 gear 类型。Slot 本身无固定类型：受支持的 `.nam` 派生 NAM，受支持的 `.wav` 派生 IR，其他格式拒绝进入 Slot；gear 只派生原生显示标签，缺失或无法解析时显示 `SLOT`。所有标签都使用同一套状态 × Pane 规则；标签只用于显示、搜索和筛选，不能改变加载、bypass、target 或写入语义。`Local Pack` 与 `Slot Pack` 是同一个 Pack Selection 表面；后者只表示从某个 Slot 进入时带有明确的 target 上下文，不是第二个独立 Pane。

| Pane | Active | Bypass | Empty |
|---|---|---|---|
| ChainPanel | 单击或 `tab` 聚焦并设为 target；`↑/↓` 切同 pack Model；`enter`/双击切为 Bypass；返回仍在当前行；替换、bypass、重排、添加或删除时写 Chain | 聚焦保留 target；`↑/↓` 选其他 Model 即恢复；`enter`/双击恢复；返回仍在当前行；落盘为 `path:null`，候选只留在进程内 | 聚焦设为 target；不可切 Model，也不能 bypass；`enter`/双击打开来源选择；返回仍在当前行；加载、添加或删除时写 Chain |
| Library | 选择 tone 只更新 viewing 和 DetailPane，不改 target；`enter`/双击打开对应 Local/Remote Pack，并只尝试首个已下载 Model；`esc` 回来源；不自动安装或创建 Slot | 与 Active 相同；不恢复候选、不改 bypass | 以当前 Empty target 打开对应 tone/Pack；未下载只提示，不安装、不创建 Slot |
| Local Pack / Slot Pack | target 明确为该 Slot；其他受支持文件 `enter`/双击替换并保持 Active；重复选择当前 `▶` 文件（`enter` 或双击）切 Bypass；`esc` 回来源；写新 `path` 或候选 | `▷` 文件 `enter`/双击恢复；其他受支持文件加载并清候选；`esc` 回来源；写新 `path` 或 `path:null` + 候选 | target 明确为该 Slot；受支持文件 `enter`/双击加载为 Active；无当前标记；`esc` 回来源；写新 `path`；未知格式直接拒绝且不写 Chain |
| Remote Pack | target 保留但不改；Library 进入时首个已下载文件自动尝试加载；未安装文件 `enter`/双击进入 Pack Install，已安装且受支持的文件 `enter`/双击加载；`x expand` 打开大 Pack 页面；`esc` 回来源；安装不写 Chain | 同 Active；已安装候选可直接加载并恢复 | 同 Active；安装不创建 Slot，已安装且受支持的文件可明确加载到现有 target |
| DetailPane | 显示 Slot Pack：序号、uppercase 派生标签、所属 pack、`▶`；点击 TAG 或 `[/]` 在 Description/Selection 间切换；`x expand` 打开大 Pack 页面；动作写入 target Slot | 显示 Slot Pack：`BYPASS` 和 `▷`；重复选择候选恢复，选择其他文件替换；返回保留 target | 显示 Empty Slot：`NONE` 和 target；只提供去 Library/Pack 的入口；不残留旧 pack |
| Presets / Preset Edit | 预览保留 `path/model_id`；`enter` 加载会整体替换 Slots 并清 target/候选；`e` 在 draft Slot 上编辑；保存写入 preset 快照 | 保存按 `path:null` 降级为 Empty，并在确认中明示；加载不能恢复旧候选；draft 内的 bypass 也是临时状态 | 保存和加载均为 Empty；`e` 可在 draft 中添加或加载文件；整体加载后必须重新聚焦 target；任一 Slot 格式不受支持时拒绝整个 Preset 应用 |
| 参数区 | `gain/master/quality` 只改 Chain 参数，不改 target、文件或状态；写入后标记 dirty | 参数变化不解除 bypass；只改 Chain 参数并标记 dirty | 参数可独立修改；不创建或删除 Slot；标记 dirty |
| Input / InterfaceBar | Input、播放、MUTE 与 Slot 独立；`InputSource` 返回原焦点；不改 Slot 或 preset dirty | 同 Active；不会恢复 Slot | 同 Active；零 Slot 仍可切换 Input |

矩阵的跨 Pane 不变量：

- 只有 ChainPanel 的 Slot 聚焦，以及从该 Slot 打开的 Local/Slot Pack，能够建立或更新 Target；Library、Remote Pack、参数区、Input、creator 和 Preset 行不能覆盖 Target。
- 只有 Local/Slot Pack 的明确文件加载、Slot 操作、参数/MUTE/Input 的明确提交、Preset 整体应用、undo/redo 或合法外部完整配置替换能改变 live state；Library view tab 选择、DetailPane view tab 切换和 Remote Pack 安装不写 Slot Chain。
- 单击只聚焦；对当前 `▶` 文件的重复选择才触发 bypass，其中鼠标入口必须是双击，键盘等价入口是 `enter`；对 `▷` 的重复选择恢复 Active。选择不同文件始终是替换，不是 bypass。
- `esc` 回到发起动作的来源 Pane，并恢复来源光标和视口。整体 `slots[]` 替换后 target 和全部 bypass 候选都清空，用户必须重新聚焦 Slot。
- Loading/Error 是上述四种状态的临时覆盖层，不是第五种 Slot 类型：Loading 保留旧状态并拒绝重复提交；Error 保留旧有效 Chain，允许 retry 或选择替代文件。

## 9. Presets、dirty 与撤销

- Preset `chain_json` 快照字段为 `slots[]`、`gain`、`master`、`quality`；不保存 Input、播放状态、设备、MUTE、焦点、target 或 bypass 进程内候选。Preset `note` 属于记录元数据，按 9 节单独保存。
- 保存时 Bypass Slot 按当前落盘 `{path:null}` 保存，因此再次加载为 Empty；UI 必须在保存确认中显示该结果，不声称保存 bypass 恢复候选。
- 加载 Preset 原子替换整个 Slots 数组和参数，保留 Input 与播放状态。
- Slot 添加、删除、重排、换文件、bypass/恢复和参数变化都会使 active preset dirty。
- `ctrl+z`/`ctrl+shift+z` 只撤销/重做 Preset 应用产生的完整 Chain 快照；手动增删、重排、换文件、bypass 和参数编辑不创建历史步骤。
- Preset 应用历史最多 50 步；应用新 Preset 后清空 redo；快照包含 Slots 和参数，不包含 bypass 候选、Input、MUTE、焦点或 target。
- undo/redo 不恢复 Library 光标、DetailPane 模式或 Input；应用后 target 已清空，必须重新聚焦 Slot。
- Preset Detail 按 DSP 顺序显示 `01–06`，包括 Empty；Slot 值显示紧凑 Model ID（例如 `#101`），不显示完整文件名。没有 Model ID 但仍有路径的历史记录显示 `UNKNOWN`，空 Slot 显示 `NONE`。
- 旧 preset 读取时转换为 Slots；首次保存或覆盖写新格式，不原地批量迁移数据库。

Presets 主面板：

- 面板标题下固定一行 Preset search 槽位，不打开新页面或模态；未激活时可收起为 `search presets…` 或当前 query 摘要，激活时展开输入框；高度和表格布局固定。
- `/` 或点击 search 槽位打开并聚焦输入框；输入后做大小写不敏感本地过滤，不访问网络、不改变 active、dirty 或 live Chain。普通词按 AND 匹配 Preset 名称、Note、文件名和 ID；字段限定使用 `name:...`、`note:...`、`file:...`、`id:...`，其中 `id:` 同时匹配 model ID 与 tone ID。SearchBar 固定为 `SEARCH: name:clean file:SVT  SORT: Updated`，排序还提供 `Name`。
- 点击 search 槽位以外的区域触发 `outside-click dismiss`，关闭输入框和候选层但保留 query/filter；再次 `/` 或点击槽位继续编辑。`enter` 或 `tab` 从 search 回到过滤后的表格首个可用行；`esc` 清空 query 并关闭 search。无匹配时显示 `no matching presets · esc clear`，不制造空行。
- 过滤导致当前光标或选择不可见时，按 preset name 清理无效 selection，并把光标放到第一个匹配项；清空 query 后按 name 恢复原选择和视口。
- 列：Sel、Preset、Slots、Note。Slots 按顺序显示紧凑 Model ID 摘要，例如 `#101 > #202 > #303`；Empty 显示 `NONE`，没有 ID 的历史路径显示 `UNKNOWN`，不显示完整文件名。
- `enter` 加载当前行，`s` 保存 active preset，`n` 新建/Save As，`e` 编辑当前 preset，`r` 重命名，`space` 选择，`a` 全选/全不选，`d` 删除，`esc` 清选择。`d` 在存在选中项时删除全部选中项；没有选中项时只删除当前行。
- `s` 只保存 active preset；当前没有 active preset 时打开 Save As。聚焦非 active 行不会让 `s` 静默覆盖它；要修改该行使用 `e`，要先应用它使用 `enter`。
- `e` 进入 Preset Edit draft：可修改 Slots、gain/master/quality 和 note；它不是只改备注，也不直接修改 live Chain。
- 聚焦只更新 Preset Detail；加载才改变 live Chain。加载或保存为 active 后清除 dirty；重命名保持 active；删除 active preset 清除 active 指针。
- 手动 Slot 或参数变化相对 active preset 不一致时显示 dirty；仅 Input、播放、设备、MUTE、焦点和 target 变化不产生 dirty。
- MUTED 时 dirty 比较使用配置中的 `master` 参数而不是 effective output 的 0；否则单纯 MUTE 会错误标记 dirty。
- 无选择提示：`n new · s save · r rename · e edit · a all · d delete · enter load`。
- 有选择时左侧显示 `{n} sel`，右侧增加 `a none` 和 `esc clear`；`d delete` 明确作用于 `{n} sel`；窄屏始终优先保留 `enter`、`s`、`d` 和 `esc`。

Preset 记录由 `name`、`note` 和 `chain_json` 组成；`chain_json` 只保存下面的音色链快照，`note` 是记录元数据，不进入 `live_chain.json`。`note` 为 UTF-8 字符串，默认空字符串；空白输入保存为空字符串，旧记录缺失 note 时按空字符串读取。

Preset 内部快照格式与 live protocol 分开：

```json
{
  "slots": [
    {"model_id": 101, "path": "data/tones/19/model.nam"},
    {"model_id": null, "path": null},
    {"model_id": 202, "path": "data/tones/60066/cab.wav"}
  ],
  "gain": 1.0,
  "master": 1.0,
  "quality": 1.0
}
```

- Library 文件保存 `model_id` 和 portable path；受管 tones 目录中没有 DB 行的合法文件保存 null id 和 path；Empty 两者都为 null。
- 加载时优先以 `model_id` 解析当前已安装路径，解析失败再检查保存的 path；仍不可用则整个 Preset 不加载并列出缺失 Slot。
- Preset 解析成功后转换为 live protocol 的 `{path}` 数组再原子写入；Preset 专用字段不得进入 `live_chain.json`。

Preset 状态规则（不包含二级浏览页）：

| 页面/状态 | 规则 |
|---|---|
| Load empty | 主面板显示 `no presets · n new · s save`；`enter` 无动作 |
| Load invalid | 主面板保留名称；DetailPane 显示解析错误，不可加载 |
| Load dirty confirm | 仅 live Chain dirty 时弹确认；`enter` 丢弃 live 改动并加载，`esc` 取消；不提供第二个 Preset 浏览列表 |
| Save As empty | 不提交，输入框保持焦点，显示 `name required` |
| Save As duplicate | 第一次 `enter` warning；第二次覆盖；名称变化取消确认 |
| Rename invalid/conflict | 保留输入并显示具体错误，修正后可重试 |
| Preset Edit note | note 只能在 Preset Edit draft 中修改；空字符串清除 note，`enter` 保存 draft，`esc` 丢弃 |
| Delete | 无选中项删除当前行；有选中项删除全部选中项；`$error` + `cannot be undone`；`enter` 二次确认并显示数量与名称摘要 |
| Delete stale | 执行前重新校验目标；不存在的项标记 stale，成功删除其余目标并报告部分结果，不报告 stale 为成功；成功项清除选择，stale 保持选中 |

Preset Edit draft：

- 打开时复制当前 Preset 快照；编辑期间所有 `+`、`d`、`⌥↑/⌥↓`、文件替换、参数步进和 note 输入只修改 draft，不写 live chain、数据库或 `live_chain.json`。
- draft Slot 使用 `tab/shift+tab` 聚焦、`↑/↓` 切同 pack Model、`⌥↑/⌥↓` 重排、`+` 添加、`d` 删除；最多 6 个，删除最后一个允许零 Slot。
- 文件替换从 draft 当前 Slot 进入 Pack Selection；重复选择当前 draft 文件仍切换 draft bypass/restore，关闭编辑时不影响 live 候选。
- `enter` 保存 draft 到当前 Preset；`ctrl+enter` 保存并加载该 Preset；`esc` 丢弃 draft。保存失败保留编辑内容和焦点。
- 当前 Preset 被编辑后，active/dirty 依据保存后的快照重新计算；编辑非 active Preset 不改变 live Chain。
- note 只改变 Preset 记录元数据，不改变 live Chain；保存 note 后更新 `updated_at`，但不把 live Chain 标记为 dirty。

关闭任何 Preset 页面后恢复来源焦点；校验错误显示在正文或提示带动态前缀，不覆盖固定确认和返回 token。

## 10. Input、InterfaceBar 与 MUTE

- INPUT 支持 instrument 与 dry WAV file；双击 INPUT 打开 InputSource。
- file 模式支持 `space` 播放/暂停、`s` 停止、`l` 循环；instrument 模式拒绝并说明原因。
- INPUT 行显示文件名，不加 `Dry`；PLAY 块与 Slot 行尾按钮右缘对齐。
- MUTE 由独立的 live `mute` 布尔值表示；`master` 始终保留用户参数，允许用户明确设为 0。有效输出 master 为 `mute=true ? 0 : master`。
- `x` 或点击 `MUTE` token 切换 MUTE；MUTE 与 Slot bypass 独立。修改 gain/quality 不解除 MUTE，修改 master 不自动改变 mute，只更新 unmute 后使用的参数。
- Preset 保存 `master` 参数但不保存 `mute`；因此单纯 MUTE 不产生 dirty。保存时不能把临时静音写成 `master=0`。
- 处于 MUTED 时加载 Preset：应用 Slots、gain、master、quality，但保持 `mute=true`；unmute 后使用新 Preset 的 master。若 master 本身为 0，unmute 后仍显示 `MASTER 0`，但不显示 `MUTED`。
- MUTE 必须显示 `$error` + `MUTED`，不能只变色；恢复后保持原 Chain 状态。用户主动把 master 设为 0 时显示参数值和 `MASTER 0`，不得伪装为 MUTE。

InputSource 状态机：

| 状态 | 内容与动作 | 完成/退出 |
|---|---|---|
| instrument | `✓ Instrument`；播放键 disabled 并说明需选择 dry file | `enter` 选择后关闭 |
| dry files available | 文件列表、当前文件 `✓`、缺失数量和 download all | `enter` 选择后立即 playing + loop，并关闭 |
| empty | `(no dry inputs)` + download all | `d` 或 download 行开始下载 |
| downloading | 保留列表，显示 `done/total + filename`，禁用重复下载 | 完成后刷新并按文件身份恢复光标 |
| partial/error | 已完成文件仍可选，显示失败原因 | `d` 重试缺失项，`esc` 可关闭 |
| playing/paused/stopped | 显示状态、位置、loop 和文件名 | `space/s/l` 原地更新，不关闭 |

下载页面关闭后，已开始的文件写入可继续，但 worker 不访问已卸载 widget、不重开页面、不抢焦点；新下载任务重新计算 missing，不删除已经完成的文件。

## 11. 模态与异步规则

必须提供并完整处理：InputSource、Audio Settings、Pack Install、Uninstall、Preset Load Confirm、Preset Save As、Preset Edit、Preset Rename/Delete、Command Palette。

- 模态内 Tab 循环；`esc` 取消或关闭；确认键和点击结果等价。
- Pack Install/Uninstall 显示文件数、大小、目标和依赖；活动 Chain 中任一 Slot 使用的文件禁止卸载。
- Preset 引用文件的卸载需要第二次确认；确认期间重新计算依赖，避免陈旧计划。
- 异步任务有唯一 operation id 和对象身份守卫；非 silent 结果还必须匹配当前 active pane、query、sort 和 Type filter。切换 view tab 或改变这些查询条件会使未完成请求失效；过期结果只能写缓存，不能更新当前表格、DetailPane、状态带或焦点。silent 预取可以填充缓存，但不能发布隐藏 view 的 highlight 或抢焦点。
- Slot 变更写入与引擎加载串行化；新请求可以合并未开始请求，但不能让旧完成事件覆盖新 Chain。
- loading 保留旧有效内容；失败保留旧 Chain、焦点和 target，并提供明确 retry 或替代动作。
- managed engine 使用单一提交事务：先等待当前 session ready acknowledgement，再用唯一 transaction id 校验并完整准备候选运行链；准备失败时不写 JSON、不改 TUI、不动当前运行链。候选准备成功后，以同一个 `revision` 原子写入 JSON 并切换运行链；任一步提交失败都按 1.11 发送新的 rollback transaction 并恢复旧 JSON、旧运行链和旧 UI 状态。
- external engine 只有 JSON 写入这一可确认提交；写入成功后 UI 只显示 `file committed · runtime unknown`，不得声称外部 DSP 已应用。下一次可观察到的 runtime 状态由外部 engine 自己报告。
- 应用退出时取消 worker、timer、长按和 pending UI callback；晚到 callback 不访问已卸载 widget。
- 安装、卸载、导入、删除等成功 mutation 在原子提交完成后发布 `MutationCommitted`；刷新由第 3.5 节的 coordinator 统一调度，失败、取消和阻塞路径不刷新。

动作矩阵：

| 页面 | 键位与动作 | 单提示带示例 |
|---|---|---|
| InputSource | `enter` 选择；`space/s/l` 播放控制；`d` 下载 | `space play/pause · s stop · l loop · d download · enter select · esc close` |
| Pack Install | `space/a` 选择；`enter/i` 安装；`u` 卸载 | `a all/none · i install · u uninstall · esc cancel` |
| Uninstall | `u` 或 `enter` 确认；`esc` 取消 | `u uninstall · esc cancel` |
| Preset Load Confirm | dirty 时 `enter` 加载；`esc` 取消 | `enter load · esc cancel` |
| Preset Save As | `enter` 保存；`esc` 取消 | `enter save · esc cancel` |
| Chain Save | `SAVE HERE` 或 `SAVE AS NEW`；名称与 `SAVE AS NEW` 同行；冲突时 `enter overwrite`；`esc` 取消 | `cancel · enter save` / `cancel · enter overwrite` |
| Clear All Slots | `enter` 确认清空链上 Slot；`esc` 取消 | `cancel · enter clear all` |
| Preset Edit | `enter` 保存 draft；`ctrl+enter` 保存并加载；`esc` 取消 | `enter save · ctrl+enter save/load · esc cancel` |
| Preset Rename | `enter` 重命名；`esc` 取消 | `enter rename · esc cancel` |
| Preset Delete | `enter` 删除；`esc` 取消 | `enter delete · esc cancel` |
| Audio Settings | picker 改变立即应用；`enter/esc` 关闭 | `enter close · esc close` |

关键状态：

- InputSource 下载中保留列表并显示文件级进度；关闭页面可继续持久化，但 worker 不再更新已卸载 UI。
- Pack Install 未下载文件默认选中、已下载文件默认不选；运行期间冻结目标集合，安装和卸载不能并行。
- Uninstall 打开和执行前都重新计算 active Slot、库外路径和 Preset 引用；目标按 model id 冻结，列表刷新不能扩大集合。
- Audio Settings 始终提供 System Default；枚举失败仍可选择默认值；managed engine 改变立即重启，external engine 明确提示未重启。
- Command Palette 提供 Search、参数增减、Focus Presets、Preset Save/Save As、Audio Settings、Next Theme 和 Quit；Preset Load 只负责聚焦 Presets 面板，不打开二级浏览页；危险数据操作不进入 palette。

## 12. 数据协议与引擎边界

### 12.1 `live_chain.json`

```json
{
  "input": {
    "source": "file",
    "file": "data/dry_inputs/x.wav",
    "state": "stopped",
    "loop": false
  },
  "slots": [
    {"path": "data/tones/19/model.nam"},
    {"path": null},
    {"path": "data/tones/60066/cab.wav"}
  ],
  "gain": 1.0,
  "master": 1.0,
  "quality": 1.0,
  "mute": false,
  "revision": 42
}
```

| 字段 | 类型/默认 | 规则 |
|---|---|---|
| `slots` | array，默认 `[]` | 0–6 项，有序；每项只允许 `path` |
| `slots[].path` | relative path 或 null | `.nam/.wav` 可执行；null = Empty 或无候选的持久态 |
| `gain` | number，默认 1.0 | 0–10 |
| `master` | number，默认 1.0 | 0–10 |
| `quality` | number，默认 1.0 | 0–1 |
| `mute` | boolean，默认 `false` | 只影响实时输出，不进入 Preset；不改变 `master` 参数 |
| `revision` | 非负整数，默认 0 | 每次规范写入递增；用于 TUI、文件和 managed runtime 对齐，不进入 Preset |
| `_transaction_id` | managed 内部 string，可缺省 | 仅用于一次 candidate/rollback 的身份关联，不进入 Preset，不作为 UI 状态或排序依据 |
| `input.source` | `instrument` 或 `file` | 默认 `instrument`；决定 InputSource 模式 |
| `input.file` | relative path 或 null，默认 null | `source=file` 时必填，必须位于 `data/dry_inputs/`；`source=instrument` 时必须为 null |
| `input.state` | `playing`、`paused` 或 `stopped` | 默认 `stopped`；instrument 模式必须为 `stopped` |
| `input.loop` | boolean，默认 `false` | instrument 模式必须为 `false`；file 模式可切换 |

`input` 是独立于 Slots 的对象。文件缺失时保留合法路径并显示 Input Error/Stopped，不因此破坏有效 Chain；路径越界、字段类型错误或非法 source/字段组合拒绝整个新配置。未识别的 input 字段读取时保留但 UI 不解释，规范写入时删除。

- 路径落盘为无 `..` 的项目根相对 POSIX 路径，读取比较时使用 `realpath` 解析为绝对路径。
- Slot 路径解析后的目标必须是 `data/tones/` 内的普通文件；Input file 必须是 `data/dry_inputs/` 内的普通 `.wav` 文件。符号链接只有在最终 realpath 仍位于对应允许根内时有效。
- 扩展名按 ASCII 大小写不敏感判断；Slot 只接受处理格式受支持的 `.nam/.wav`。其他扩展或内容格式不受支持时，交互写入、Preset 应用和外部 live 配置均拒绝，不得保留为 Slot 或交给引擎跳过。
- 读取旧绝对路径时，仅接受 realpath 位于上述允许根内的路径，并在下一次有效写入时规范化为相对路径；目录穿越、根外绝对路径、设备、FIFO、socket 和逃逸符号链接拒绝整个配置。
- Slot 项不保存 id、type、label、tone id、bypass、参数或 UI 状态。
- 写入采用同目录临时文件 + rename；一次用户动作只产生一个完整 JSON 替换，并递增 `revision`。
- 超过 6 项、Slot 非 object、path 非 string/null、Slot 文件扩展名或处理格式不受支持、参数非有限数、`mute` 非 boolean、`revision` 不是非负整数、Input 字段类型错误或路径非法时拒绝整个新配置，保留上一份有效 Chain。
- `revision`、`mute`、`input` 和 managed-only 的 `_transaction_id` 是规范顶层字段；其他未知顶层字段读取时保留但 UI 不解释。`model` 和 `ir` 是迁移保留字，不属于可保留未知字段，任何含 `slots[]` 的规范写入必须删除它们。未知 Slot 字段在规范写入时删除。
- 外部合法写入在下一轮询同步；结构非法时显示 warning，不覆盖坏文件，继续运行上一份有效 Chain。
- TUI 写入时记录 canonical JSON fingerprint 和 `revision`。轮询读到同一 fingerprint 时保留本进程的 bypass 候选；读到不同 fingerprint、缺少 revision 或 revision 不属于本进程最近提交时，视为外部整体替换并清空全部候选。

### 12.2 旧格式兼容

读取旧 `model/ir`：

1. `model` 为有效路径时追加首个 Slot。
2. `ir` 为有效路径时追加下一个 Slot。
3. 缺失或 null 不产生 Empty Slot。
4. 转换只发生在内存；用户下一次有效 Chain 写入或 Preset 保存时输出 `slots[]`。
5. 同时出现 `slots[]` 与 `model/ir` 时，以 `slots[]` 为准并 warning；不得合并两套表示。

旧文件没有 `mute` 时按 v0.1 兼容规则读取：`master=0` 解释为 legacy MUTE，恢复参数暂取 1.0；其他 master 读取为 `mute=false`。v0.2 的首次规范写入必须补齐 `mute` 和 `revision`，此后 `master=0, mute=false` 才表示用户主动把参数归零。

旧 Preset 的 model 与 ir 都缺失或为 null 时转换为零 Slot；这不是解析错误。
旧 live chain 首次发生有效写入后，文件必须只含 `slots[]`，不能残留 `model` 或 `ir`。

### 12.3 引擎

- `--live`、`--list`、`--in`、`--out`、`--gain`、`--master` 和 `--root` CLI 接口保持兼容；Slot 化不改变设备选择和非 live 调用方式。
- 信号顺序固定为 `input → gain → slot₁ → … → slot₆ → master → out`；`mute=true` 时运行时有效 master 为 0，但不覆盖配置中的 `master` 参数。
- `.nam` 构建 NAM 节点，`.wav` 构建 IR 卷积；null 和未知格式跳过。
- Slot 增删、重排、换文件和恢复触发链重建；只有内容或顺序变化才重建。
- 新链必须按 11 节的 managed transaction 完整校验、准备并提交；任一节点加载失败保留整个旧链和旧 JSON。prepare、写入和 apply 使用同一 revision，candidate 与 rollback 使用可区分的 transaction id。
- `quality` 应用于所有支持 quality scaling 的 NAM 节点；IR 节点忽略 quality；运行链内部保存当前 `revision`，用于确认 runtime 与文件提交一致。
- 引擎独立校验 6 Slot 上限，不能只信任 TUI。
- 零有效 Slot 对 instrument 和 file input 都是合法直通链；引擎不得再以“缺少 model”为由拒绝启动。
- Slot 容器本身不得增加音频 block 缓冲：相同 block/sample rate 下，单有效 Slot 的输出样本延迟必须与 v0.1 单节点路径相同；空 Slot 不占 DSP。
- WavInput、播放状态、loop 和位置回传行为不因 Slot 化改变。

### 12.4 `level.json`

`level.json` 继续提供 `in`、`out`、`play_state`、`play_pos`，约每 0.1s 更新，并在 managed 模式提供 `runtime_session_id`、`runtime_transaction_id`、`runtime_revision`、`runtime_status` 和单调的 `runtime_ack_seq`。它是只读观测，不承载 Slot 状态；缺失、损坏或过期时显示无活动电平和 stopped，不让 UI 崩溃。runtime acknowledgement 必须按 session、transaction、revision 和 ack sequence 一起判断新鲜度。

## 13. 提示与窄屏优先级

所有 Pane、DetailPane、二级表面和模态都使用一个右下角提示带。提示带不是左右两个布局区，而是一个连续序列：动态状态和条件 action 在左，稳定 action 在最右侧。

```text
                         3/6 slots · loading… · save · clear all slots
                                      delete · tone · bypass · move ↑ · ↓
                                                    viewing 03 · target 02 · enter load · esc back
```

### 13.1 Pane 底部提示与动作等价

- 提示带按“动态状态 · 条件 action · 稳定 action”顺序生成，三段仍是一个连续的右对齐提示带，不存在独立的左栏或右栏。普通状态文字不可点击；`↓ more` 等条件 action 必须有独立命中区，点击等价于当前列表按 `↓` 加载下一页。
- 右侧稳定 action 的每个 `key label` token 都有独立命中区域；点击 token 与按其显示键位完全等价，包含写入、返回、确认和取消结果。键位别名（如 `enter/i`）可以同时接受，但只显示一个稳定 token。
- 表格行单击只聚焦并更新 DetailPane；表格主动作必须通过 `enter`、双击或提示带 token 执行。选择框点击等价于 `space`，全选 token 点击等价于 `a`。
- view tab TAG 点击等价于激活对应 `view_tab_id`；TAG 外部点击不改变当前 view tab。SearchBar query 点击等价于 `/`，sort 点击打开 sort 选项；点击 SearchBar 外部按第 3.4 节触发 `outside-click dismiss`，但不触发原 Pane 的加载、保存或删除动作。模态外部点击按阻塞表面规则忽略。
- SearchBar 的 Type select 是唯一 Type/gear 过滤控件；结果表头和 Author 列不可过滤。选择立即应用并保持 SearchBar、cursor 和 viewport。

| Pane / 模式 | 单提示带（动态前缀 · 稳定动作后缀） | 点击规则与键盘等价 |
|---|---|---|---|
| Library / LOCAL | `LOCAL · {count} · type: VALUE · ↓ more · a all/none · space select · d uninstall · enter open · r refresh` | 无 Type filter 时省略 `type: VALUE`；行单击聚焦；双击/`enter` 打开；选择框 = `space`；token 同键；`↓ more` = `↓` |
| Library / TONE3000 | `TONE3000 · {count} · type: VALUE · loading/error · ↓ more · r refresh · enter open` | 无 Type filter 时省略 `type: VALUE`；行单击聚焦；双击/`enter` 进入 DetailPane PACK；刷新 token = `r`；`↓ more` = `↓` |
| ChainPanel | `n/6 slots · loading/error · save · clear all slots` | 主边框 token 同键；Slot 行单击 = 聚焦；Slot 边框显示 `delete · tone · bypass/restore · move ↑ · ↓`，两个 move token 均可点击；`enter` 与双击均为 Active ↔ Bypass；播放块点击 = `space` |
| DetailPane / Description | `viewing source · target nn · status · esc back` | view tab TAG 切换内容；`esc` token 同键；正文单击只移动焦点，不加载文件 |
| DetailPane / Pack Selection | `viewing source · target nn · ▶/▷ · installed/not downloaded · enter load · i/u · x expand · esc back` | view tab TAG 切换内容；文件行单击只浏览；已下载文件双击/`enter` 加载；未下载文件双击/`enter` 进入安装；`x` 或点击 token 打开大 Pack 页面；当前 `▶` 双击/`enter` bypass；token 同键 |
| DetailPane / Empty | `slot nn · NONE · target · enter browse · d delete · esc back` | `enter browse` 打开来源；`d`、`esc` token 同键；不支持的文件在进入 Slot 前直接拒绝 |
| Presets | `{count} presets · active/dirty · enter load · s save · n new · e edit · r rename · d delete · space/a select` | 行单击聚焦；双击/`enter` load；选择框 = `space`；token 同键；SearchBar query 点击 = `/` |
| Preset Edit | `draft · n/6 slots · dirty · enter save · ctrl+enter save/load · esc cancel` | 所有 token 同键；模态外部点击不关闭；编辑控件失焦按 draft 规则处理 |
| Input / InterfaceBar | `instrument/file · playing/paused/stopped · MUTE/MUTED · file/runtime revision · space play/pause · s stop · l loop · x mute · esc input` | PLAY、STOP、LOOP、MUTE 点击分别等价于 `space/s/l/x`；`esc input` 只关闭 InputSource |
| InputSource | `source · state · done/total · space play/pause · s stop · l loop · d download · enter select · esc close` | 行单击选择焦点；双击/`enter` 选择；token 同键；下载中外部点击不取消任务 |
| Pack Install / Uninstall | `selected · size/dependencies · progress/error · a all/none · i install · u uninstall · esc cancel` | 选择框 = `space`；token 点击等价于 `a/i/u/esc`；外部点击不关闭 |
| Audio Settings | `audio · current device · restart status · enter close · esc close` | picker 点击立即应用；关闭 token 与 `enter/esc` 等价；外部点击不承担关闭或回滚 |
| Command Palette / 其他确认模态 | `command/confirm · query or warning · enter run/confirm · esc close/cancel` | 结果行单击聚焦、双击/`enter` 执行；外部点击不关闭确认模态 |

- 提示带整体右对齐；稳定动作后缀的右边界、顺序和点击命中区不因动态状态变化而移动。
- 空间不足先保证稳定 action 的完整 token，必要时缩写说明词并从左到右隐藏低优先级 action，条件 action 可整体隐藏，动态前缀使用剩余宽度省略；`esc` 和当前主要确认始终保留。状态与 action 之间仍保留 ` · `，不得拼接成不可命中的首个 key-only token。
- ChainPanel 优先级：当前状态动作 > `+` > `d` > move > model > playback。
- DetailPane 优先级：`esc` > `enter` > `i/u` > `space/a`；view tab TAG 不占用底部 action token。
- 缩写只能缩动作词，不改键位；不得把不同动作缩成同一 token，也不得渲染半截 token。

## 14. 验收基线

### 14.1 领域与协议

- 新格式 0–6 每个 Slot 数量都做读写往返；0、1、6 作为空链、单节点和上限的专项冒烟；第 7 个拒绝且旧链不变。
- 旧格式覆盖 model only、ir only、双键、双 null、slots 与旧键并存；首次新写入断言旧键已删除。
- 顺序、重复 Model、Empty、未知格式和项目相对路径均有测试。
- 引擎验证实际处理顺序、空槽零处理、重建失败回滚、revision 对齐、6 NAM 冒烟和多 NAM 共用 quality。

### 14.2 交互

- 添加、删除、首尾重排 no-op、连续重排和 target 跟随均验证键盘与点击。
- 删除覆盖 Active、Bypass、Empty 和最后一个 Slot；零 Slot 添加入口与满 6 disabled 状态都要验证。
- `tab/shift+tab` 按 Input → Slot → add → gain/master/quality 顺序导航；`↑/↓` 只切 Model；`⌥↑/⌥↓` 只重排，三组动作无串线。
- 参数 `·` token 对 gain/master/quality 都恢复为 `1.0`；明确的 `0` 只能通过减少键或精确编辑设置，且在 MUTE、Bypass 和 dirty 状态下保持第 1.3 节语义。
- Pack 覆盖其他文件加载、当前 `▶` bypass、当前 `▷` 恢复、bypass 换文件恢复。
- 无 target 拒绝且不改链、零 Slot 必须先添加、重复 Model 只比较 target。
- Active、Bypass、Empty、Loading、Error 在 DetailPane 和无颜色模式均覆盖；未知扩展名和内容格式覆盖拒绝路径，断言 Chain、Slot、target 和候选均不变。
- Slot 删除后 target 选择、重排后焦点、外部写入和晚到异步响应都有测试。
- 外部写入、Preset load、undo 和 redo 整体替换 `slots[]` 后都必须清空 target 和 bypass 候选；覆盖旧 Bypass → 新 Empty 同下标及重复 path 场景。
- TUI 自己写入 Bypass 后经历一次轮询仍保留候选；不同 fingerprint、无 revision 或外部 revision 的 `path:null` 必须降级为 Empty。
- Active、Bypass、Empty 三种持久状态分别走 ChainPanel、Library、Local/Slot Pack、Remote Pack、DetailPane、Presets、参数区和 Input 的矩阵路径；每条路径都验证入口焦点、加载目标、`esc` 返回和写入结果。
- Library view tab 选择、Remote Pack 安装、DetailPane view tab 切换、参数和 Input 操作不得覆盖 target 或误写 Slot；当前 `▶` 的重复选择必须以双击触发 bypass，`▷` 的重复选择必须恢复 Active。
- Library Enter/双击后的首个已下载 Model 必须可直接加载到原 Target；首个未下载只提示且不安装。Remote Pack 安装成功后，已安装文件行必须可通过 `enter`/双击直接加载到原 Target；安装失败可 retry 且不写 Chain。
- Library、DetailPane 等包含多个同级内容的 Pane 验证 `view_tab_id`、TAG 高亮、点击和 `[/]` 切换、view tab strip 单 focus stop、`tab/shift+tab` 只做焦点顺序，以及 `←/→` 不再切换 view。
- 每个可搜索列表验证一行 SearchBar 中同时存在 query、sort 和适用时的 type；验证背景焦点态、无边框样式、`/`、`tab`、`enter`、`esc` 和 sort/type 选择行为。
- 验证 Type/gear 过滤只出现在 SearchBar，Author 和其他列不可过滤；结果表头点击不打开菜单，SearchBar Type 选项按当前数据动态生成，uppercase 选中项按原生 token 即时过滤且不改变表格起始位置或底部提示高度。
- 安装、卸载、导入、删除和部分成功分别验证：成功项只发布一次 `MutationCommitted`，所有已注册页面各 reconcile 一次，失败/取消/阻塞不发布事件。
- mutation coordinator 验证相同 revision 的重复事件合并、无 revision 的不同事件按顺序分别 reconcile，以及同一事件对象重复投递只 reconcile 一次。
- managed transaction 验证等待当前 session ready、candidate 使用唯一 transaction 和同一 revision、telemetry 身份完整；验证 apply 失败时 rollback 使用新的 transaction acknowledgement，且旧 applied 状态不会被消费。
- managed transaction 验证原始 chain 文件不存在时先应用临时零 Slot rollback chain，收到 acknowledgement 后删除临时文件；engine 不活跃、无效 revision 和 acknowledgement 超时都显示明确失败。
- 在 LOCAL、TONE3000、TOP CREATORS、DetailPane、ChainPanel 和 Presets 中分别验证 `ViewAnchor` 的 screen、App tab、`view_tab_id`、focus、稳定 row key、cursor column、first visible row、行内偏移、scroll、selection、confirmation 和 Detail 恢复；删除当前行覆盖“下一行优先、否则上一行”。
- 验证成功 mutation 不自动切 tab、push screen、打开 Picker、抢隐藏页面焦点或清空 Detail；关闭操作页面时来源页面的 anchor 仍恢复。

### 14.3 Preset 与端到端

- Preset 保存和加载保留 Slot 数量、Empty、顺序、重复路径和参数；Preset record 的 note 独立保存、搜索和恢复。
- Preset 面板验证 `enter load · s save · n new · e edit · r rename · d delete · space/a select`；这些键只在 Presets 面板生效。
- Preset SearchBar 验证 `/` 或点击打开、即时本地过滤、点击外部或第一次 `esc` 关闭编辑态但保留 query、未激活时再次 `esc` 清空、`enter/tab` 返回表格、无匹配和过滤后焦点/选择恢复；搜索不改变 active、dirty 或 live Chain。
- Preset Edit draft 验证 Slot 增删、重排、文件替换、gain/master/quality、note、`enter` 保存、`ctrl+enter` 保存并加载、`esc` 丢弃；取消不得改变 live Chain。
- Preset 多选删除验证“有选择删全部选中项、无选择删当前项”、确认摘要、stale 目标和部分成功结果。
- Bypass 保存明确降级为 Empty；MUTE 不进入快照，`master` 参数按用户值保存；Input、target 不进入快照。
- 验证 `master=0, mute=false` 与 `master>0, mute=true` 的显示、保存、加载、重启和 unmute 结果互不混淆。
- 旧 Preset 加载后新格式保存；undo/redo 只覆盖 Preset 应用、50 步上限和新应用清 redo。
- 端到端验证 Library → Pack → Target Slot → live JSON → 引擎热切换 → Preset 保存/恢复。
- InputSource 覆盖 instrument、empty、available、downloading、partial/error 和三种播放状态；选择 dry file 后断言立即 playing + loop。

### 14.4 视觉与尺寸

- 0–6 Slot 时 Input、参数和提示区不重叠；动态行不会挤掉主要动作。
- ChainPanel 验证外层 `TONE CHAIN` 边框、INPUT/每个 Slot 的 fieldset 标题、固定两行内容区和固定右侧动作列；所有已知及新增原生 gear 均按 uppercase 显示，增删 Slot 或长标签不引发布局跳动。
- 序号、状态、标签、Target 和 active 标记在彩色与 `NO_COLOR` 中可辨。
- hover 不改变布局；主题切换不改变 Fixed Token 或焦点；对比度达到第 4.1 节。
- `120×40`、`120×35`、`110×40`、`100×36`、`90×36`、`80×32`、`79×40` 和 unsupported 尺寸均做运行或截图验证。
- 路径安全覆盖 `..`、根外绝对路径、大小写扩展、非普通文件和符号链接逃逸。
- CLI 回归覆盖 `--live/--list/--in/--out/--gain/--master/--root`；WavInput 播放、loop、位置回传保持一致。
- 单 Slot 与 v0.1 控制组在相同 block/sample rate 下比较输出起始样本，断言 Slot 容器未增加一个 audio block。
- managed engine 验证候选准备失败、JSON 写入失败和 runtime 切换失败都恢复同一份旧 JSON、旧运行链和旧 UI；external engine 只显示 file committed，不伪造 runtime applied。
- SearchBar 覆盖未激活、query 激活、sort 激活、有结果、无结果、错误六种视觉状态；验证背景焦点态、无边框、外部点击收起但保留 query，`esc` 清空，布局高度不跳变。
- 二级表面和二级菜单覆盖 Description、Pack Selection、Slot Warning、InputSource、下拉菜单和模态；验证来源标识、固定行高、`↑/↓`、`enter`、`esc back`、焦点边界、外部点击收起/忽略规则和底层 dim 关系。
- 每个 Pane 验证右下角单提示带、动态状态/条件 action/稳定 action 的顺序、token 命中区域、单击/双击/键盘等价路径；点击 `↓ more` 必须真正触发下一页，点击 action token 的结果必须与对应键盘动作一致。
- 窄屏把 action 压成 key-only 后，状态后的第一个 token 仍可 hover 和点击；view tab TAG 跨越显示阈值时必须整体隐藏低优先级 TAG，不得显示半截 TAG 或改变 SearchBar 行高。

## 15. v0.1 → v0.2 实现迁移清单

| v0.2 目标 | v0.1 差异 | 同步范围 |
|---|---|---|
| `slots[]` 替代 `model/ir` | 当前协议和引擎为两个固定节点 | protocol、CLI、引擎、TUI、skills、测试 |
| 0–6 个无类型 Slot | 当前固定 AMP/CAB 两行 | ChainPanel、DetailPane、滚动和窄屏 |
| `+` 添加、`d` 删除 Slot | 当前 `d` 只卸载固定节点内容 | bindings、鼠标 token、undo、测试 |
| `tab/shift+tab` 聚焦 Slot，`↑/↓` 切 Model | 旧稿同时把 `↑/↓` 写成导航和切换 | bindings、行尾按钮、hint、README |
| Preset 操作改为面板作用域 | 当前 `p`/`ctrl+s`/`ctrl+shift+s` 混入全局，`e` 只编辑 note | app bindings、PresetPanel、PresetEdit modal、hints、快捷键测试 |
| 移除 Preset 二级浏览菜单 | 当前 `PresetPickerScreen` 重复提供 Preset 浏览和加载 | PresetsPanel search、load-confirm、command palette、焦点返回测试 |
| `⌥↑/⌥↓` 相邻重排 | 当前没有重排 | protocol mutation、焦点/target、引擎重建 |
| Target Slot 跨面板保留 | 当前只有固定 AMP/CAB 目标 | Library/Detail linkage、空链/满链行为 |
| Slot 级 bypass | v0.1 候选绑定固定节点 | candidate map、Pack 标记、重启降级 |
| Preset 快照 Slots | 当前存 model/ir logic refs | schema、兼容读取、dirty、undo/redo |
| 活动文件卸载检查所有 Slot | 当前只查 model/ir | uninstall plan、依赖确认、测试 |
| 仅红绿灰固定 | 当前 warning 也固定、无 idle token | theme 注册、metadata、状态测试 |
| 成功 mutation 后所有页面刷新且保持视角 | 安装、卸载、导入和删除仍由多个入口手动刷新，可能清表、重置 cursor/viewport 或自动导航 | MutationCommitted、refresh coordinator、ViewAnchor、各页面 reconcile、焦点/视口测试 |
| 多内容 Pane 统一使用 view tab | Library、DetailPane 等页面仍通过左右键或独立页面切换同级内容 | view tab strip、TAG hover/click/active、单 focus stop、`[/]`、tab-local state、导航测试 |
| SearchBar 与 Type 过滤统一 | query、sort、Type 过滤分散在不同控件或使用边框框选 | 固定轨道的单行 query+sort+type、背景焦点态、SearchBar Type select、Author 无过滤、窄屏布局测试 |
| ChainPanel fieldset 与动态 uppercase Slot 标签 | 当前布局或旧文档可能固定 AMP/CAB 名称，且 Slot 内容/动作列会随文本变化 | TONE CHAIN 外框、Input/Slot 分组组件、原生 gear 展示映射、固定两行与动作列、0–6 Slot/新增类型视觉测试 |

迁移必须保持旧格式只读兼容，但不得在新写入中长期维持两套 Chain 表示。测试若固化 v0.1 固定 AMP/CAB 行为，应随实现更新，不能用旧测试否决 v0.2 目标。
