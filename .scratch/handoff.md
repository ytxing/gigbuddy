# GigBuddy Handoff — 2026-08-04

> 新会话开工前先读本文件。项目根：/Users/ytxing/workspace/tone-chain-agent
> 对话用中文，项目内容（代码/UI/文档）全英文。

## 一句话

吉他音色链工具：TONE3000（公开 API，anon key）→ 本地 SQLite 音色库（全字段元数据）→ `gigbuddy` CLI（开放给外部 agent）→ Textual TUI（控制台，无 agent）→ 实时 NAM 引擎（PortAudio + NeuralAudio，热切换）。v2 解耦架构已全部落地（SPEC: docs/SPEC-v2.md；tickets: .scratch/v2/issues/ticket-1..5.md 全勾）。

## 当前状态（2026-08-04，50 pytest 全绿）

### 数据层
- `src/library.py`：SQLite（tones 23 字段 + models + presets 表）、`bin/gigbuddy` CLI（tone list/search/show/import + chain get/set + preset 系列）
- `src/tone3000.py`：检索层。**关键**：models 查询用 `model_json->architecture` JSON 投影 + `models.name` 独立列（0.8s，全量 model_json 170s 永不用）；`models()` 返回 {id, model_url, name, architecture}；`download()` 命名 = models.name 原样（语义名，网页 zip 规则）
- `gigbuddy tone import <id>`：元数据 + 模型文件入库（幂等）；目录 `data/tones/<tone_id>-<title-slug>/`；IR tone（gear=cab）architecture="IR" 下 .wav
- gear 值域：amp/cab/amp-cab（无 ir）；`total_count` 不入库；无 get_tone_with_models RPC（PGRST202）
- `list_tones(has_files=True)`：无文件的 tone 不算本地

### TUI（tui/，Textual 8.2.8）
- 布局：Header（左上角命令按钮已隐藏，Ctrl+P 命令面板保留）→ 左 LibraryPanel（LOCAL/TONE3000/TOP CREATORS 三 tab）→ 右 ChainPanel+DetailPane → 底 DeviceBar+MeterBar 两行 → Footer
- **三 tab**：LOCAL（本地库，has_files 过滤，8 列：Title/Type/DL/Fav/Arch/Files/Up/Author，Author 列头点击过滤、Type 循环）；TONE3000（进入自动加载 TRENDING=空搜索，搜索支持 `@作者`/`#标签` 语法 → usernames/tag_names）；TOP CREATORS（前 5 位 + `＋ MORE` 行展开全部，点击创作者行 → 跳 TONE3000 搜 @user）
- **搜索全异步**（run_worker + asyncio.to_thread，status 显示 Searching…/Loading…，不卡 UI）；PackInstallScreen（Enter 打开：预览模型清单 space 勾选/a 全选/Enter 安装所选，完成 toast「Installed N file(s)」）
- **detail 作者/tag 可点击**（`[link=search:author:X]` markup → on_link_clicked → 跳转搜索）
- 链面板：AMP/IR 节点（单击聚焦、双击 toggle：IR bypass/amp mute；↑↓ 同 tone 文件夹步进模型；▲▼ 按钮竖排）
- 底栏：IN/OUT/BUFFER Select（`realtime_cli --list` 枚举，切换重启引擎）+ 延迟标注（block≈ms@48k）+ MUTE 开关（master 0/恢复，红底）
- MeterBar：色阶（绿<-24/黄<-12/红）+ peak hold ▍（1s）；0.1s 刷新（TUI tick + 引擎 level 写入双端）
- MarqueeBar（tui/marquee.py 手写滚动条）：库表上方 + DetailPane 顶部 + 安装页，聚焦行完整标题滚动；未聚焦 `_clip` 截断 + …
- 交互约定：单击聚焦/双击选中（全 UI）；Esc 取消/Enter 确认（GigBuddyModal 基类 tui/modals.py）
- 用户自定义主题 GIGBUDDY_THEME（管箱暖色），`t` 循环主题、`--theme` 启动参数

### 引擎（cpp/，C++）
- realtime_cli：`--live` 热切换（atomic 指针交换 0.3s 内）、`--level-file`（0.1s 原子写）、`--in/--out/--ch/--gain/--master/--block/--sr`、`--list` 设备枚举、A2 quality 热调
- nam_cli：离线渲染；render.py：nodes 数组支持多 amp 串联（离线）
- **实时多 NAM 串联：用户明确不做**（2026-08-02 决策）

## 测试（50 个 pytest，`tests/`）
- test_library.py（24）：schema/upsert/JSON 往返/过滤/models/chain 原子/import mock/CLI
- test_tui_keyboard.py（26）：键盘焦点、双 tab、安装页、双击 toggle、步进、MORE 等
- **注意**：测试辅助 `goto_tone_tab` 用 pilot.click(tab) + pause(0.3)（编程设 active 会回滚）；测试 mock 网络（tone3000.search/models/import_tone）

## 待办 / 注意
- **git 从未提交**（`git status` 全 `??`）—— 用户多次未要求提交，要提交需先问
- data/tones/ 旧平铺文件残留（gitignore，未删）
- 路线图剩余：VST3 效果器（pedalboard 子进程化）、交叉淡化、渲染评估闭环、AudioStream 输出
- 运行：`.venv/bin/python -m tui`（自动拉起引擎；`--no-engine` 外部跑）；CLI：`bin/gigbuddy ...`

## 踩坑大全（跨会话必读）

**Textual/TUI**
- `TabbedContent.active = X` 编程赋值在 headless **会回滚**（Tabs.watch_active 重发旧 TabActivated）→ 切 tab 一律 `tab.post_message(tab.Clicked(tab))`（用户点击路径）；tab 切换检测用 **tick 轮询**（LibraryPanel.check_active_tab，0.1s 时 reactive active 一定最新），**不用 TabActivated 事件**（事件链发多次、pane 滞后）
- highlight 路由：on_data_table_row_highlighted 按 `event.data_table` 所在 TabPane vs `_active_pane` 过滤（隐藏表事件污染 detail）；`_publish_highlight` 空表不发（防竞态清空）；搜索失败**直接 post ToneHighlighted(None)**（sentinel 走 _publish_highlight 会被去重短路）
- `[b $var]` 复合开标签必须 `[/]` 闭合（`[/b]` 报 closing tag mismatch）；markup 字面 `[`/`]` 用 `\[` `\]` 转义；验证 link 渲染不能用 str(content)（Rich Text __str__ 丢 markup），查 span.style.link
- DataTable：`update_cell` 第二参是列 key 字符串；`get_row_at(i)[0]` 返回 str；`coordinate_to_cell_key((r,0)).row_key.value` 拿行 key；`clear()` 触发 RowHighlighted(row_key=None)（先判 None 再 .value）；Enter 被表消费 → on_data_table_row_selected 桥接
- `Region.contains(x, y)` 要两个位置参数；`available_themes` 返回 str 列表；SystemCommand 在 `textual.app`（NamedTuple: title/help/callback/discover）；HeaderIcon `display: none` 隐藏命令按钮
- run_worker 的 coroutine 要 await（测试里直接调 async 方法）；`event.chain >= 2` 判断双击
- 大段替换后 grep 验证真的写入了（用户/编辑器并行改文件会覆盖）

**TONE3000 API**
- models 查询：`select=id,model_url,name,model_json->architecture`（JSON 投影，0.8s；全量 model_json 含权重 170s+）
- 搜索：`@`/`#` → usernames/tag_names（RPC 叠加过滤）；空搜索 = downloads-all-time（trending）
- 元数据组装：tones_counts + users + tone_tags/tags + tone_makes/makes（无 get_tone_with_models RPC）
- 下载命名 = models.name 原样（网页 zip 规则）；progress 回调 (done, total, filename)；3 次重试

**引擎/构建**
- NeuralAudio 编译踩坑（memory 文件有全记录）：子模块 tarball、Eigen 3.4 + LSTM 补丁、-DRTNEURAL_USE_EIGEN=1
- 延迟：block 256@48k ≈5.3ms 理论 + CoreAudio 设备缓冲 ≈30ms 实测；--block 128 可压

## 下一步建议
1. git 首次提交（问用户）
2. /code-review 双轴 review（Standards + Spec）
3. 路线图剩余项任选
