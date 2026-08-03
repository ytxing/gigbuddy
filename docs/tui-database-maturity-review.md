# GigBuddy TUI / SQLite 成熟度审查

审查日期：2026-08-02  
范围：当前仓库的 Textual TUI、SQLite 音色库，以及它们与实时引擎之间的边界。  
方法：只使用 lazygit、k9s、Harlequin 和 SQLite 的一手文档/源码资料；建议均映射到当前代码，而不是把参考项目的全部功能照搬进来。

## 结论先行

当前实现已经具备一个可用 TUI 的主路径：启动后焦点落在音色库列表，`↑/↓` 浏览、`Enter` 选中，`/` 进入搜索，`Esc` 退出搜索或关闭弹层；音色详情在语义化表格中展示；模型 picker 按 tone 文件夹组织并在高亮文件下方显示 metadata；右侧链路只读展示；远程导入在后台执行并显示文件级进度。

这和成熟项目的共同骨架一致：列表是主导航面，输入框是临时模式，`Enter` 是确认/进入，`Esc` 是取消/返回，详情是与列表同步的观察面。当前最值得补的是数据库一致性和升级边界，而不是继续堆叠更多面板。

| 领域 | 当前判断 | 优先级 |
|---|---|---|
| TUI 主导航与焦点 | 已达到可用基线；仍建议补统一帮助页和焦点可见性回归测试 | P1 |
| TUI 搜索与详情 | 已落地标题/作者/描述搜索、Type 筛选、树形文件夹和高亮详情 | 已完成 |
| TUI 后台刷新/导入 | 已有 0.3 秒刷新、后台 worker、进度条和失败状态 | 已完成 |
| SQLite 外键 | 每个连接已显式 `PRAGMA foreign_keys=ON`，并有 orphan regression test | 已完成 |
| SQLite 导入事务 | tone 与全部 model 已在一次导入事务中提交，异常整体回滚 | 已完成 |
| SQLite 并发 | 已显式设置 5 秒 busy timeout；WAL 仍等待真实 workload 评估 | P1 |
| SQLite schema 升级 | 已有 `models.name` 的窄兼容升级；仍没有 `PRAGMA user_version` 版本化迁移入口 | P1 |
| FTS5 | 当前用 `LIKE '%query%'`，小库足够；规模增长后再引入 FTS5 | P2 |

## 当前实现基线

以下事实来自当前 checkout，而不是参考项目的推断：

- `GigBuddyApp.on_mount()` 将焦点放到 `LibraryTable`，周期性调用 `refresh_from_files()` 更新电平、链路和本地库。见 `tui/app.py:104-107`、`tui/app.py:138-142`。
- `LibrarySearchInput` 用 `↓` 把焦点交给列表，`Esc` 清除搜索并回到列表；`LibraryTable` 提供 `Enter`、`↑/↓`、PageUp/PageDown、Home/End 和 Esc。见 `tui/library_panel.py:32-75`。
- 主库的搜索结果同时显示 title、type、downloads、author，远程搜索将 query 传给 TONE3000；本地 SQL 也搜索 title、username、description。见 `tui/library_panel.py:148-166`、`src/library.py:138-156`。
- 选中本地 tone 后先进入 `ToneActionScreen`，再由左侧库路径打开 picker；右侧 `ChainPanel` 是只读展示。见 `tui/app.py:159-189`、`tui/tone_action.py:11-61`、`tui/panels.py:26-51`。
- picker 用 `Tree` 按 tone 分组，`←/→` 折叠、展开或回到搜索框，选中文件时更新详情表。见 `tui/picker.py:27-63`、`tui/picker.py:114-142`、`tui/picker.py:172-244`。
- 远程导入放到 `asyncio.to_thread()`，进度回调更新 `ProgressBar` 和状态行；方向键不被下载阻塞。见 `tui/library_panel.py:168-207`。
- TUI 导入以 `quiet=True` 调用库层，下载线程不会把 CLI 的 stdout 文案混进 Textual 画面；状态统一走状态行和进度条。见 `src/library.py:208-240`、`src/tone3000.py:79-116`。
- SQLite schema 是 `tones` 与 `models` 两张表，`models.tone_id` 声明为 `REFERENCES tones(id)`，并有 title、gear、downloads_count、models.tone_id 索引。见 `src/library.py:45-84`、`docs/library-schema.md:6-46`。
- `connect()` 为每个连接设置 row factory、`foreign_keys=ON` 和 `busy_timeout=5000`，再执行 `SCHEMA`；对旧库只做 `models.name` 的加列兼容，不把 WAL 或 `user_version` 假装成已经完成的能力。见 `src/library.py:90-114`。
- `import_tone()` 在下载后把 tone 与所有 model 写入一个事务；upsert helper 仍默认自动提交，批量导入时显式传 `commit=False`。见 `src/library.py:118-149`、`src/library.py:208-240`。
- 修正前隔离探测（临时 SQLite 文件，不改仓库数据）得到：`foreign_keys=0`、`journal_mode=delete`、`busy_timeout=5000`、`user_version=0`；当前连接已验证 `foreign_keys=1`、`busy_timeout=5000`，仍保留 `journal_mode=delete`。本机 Python SQLite 可创建 FTS5 表。

## TUI 参考与映射

### 1. lazygit：把键位按“模式”分层

官方生成的 [Keybindings_en.md](https://raw.githubusercontent.com/jesseduffield/lazygit/master/docs/keybindings/Keybindings_en.md) 将键位分成 Global、List panel、Input prompt、Confirmation panel 等模式。关键约定是：

- 全局 `Esc` 取消，`?` 打开键位菜单，`q`/`Ctrl-C` 退出。
- 列表面支持 `/` 搜索、Home/End 到首尾、PageUp/PageDown 翻页，并允许左右滚动或切换 tab。
- 列表中 `Enter` 是“进入文件/展开目录”，输入提示中 `Enter` 是确认、`Esc` 是关闭/取消。
- 文件视图同时提供平铺/树形切换，目录与文件的 `Enter` 语义不同。

当前项目已经采用了其中最重要的模式边界：

- `LibraryTable` 的方向键、Enter、Home/End、PageUp/PageDown 与 lazygit 的列表面约定一致。
- 搜索框和列表分别拥有自己的 Enter 语义，避免 App 级快捷键覆盖文本输入或表格选择。
- picker 的可展开 tone 文件夹对应 lazygit 的树形文件视图；目录节点只展开，文件节点才确认。
- `GigBuddyModal` 和 `ToneActionScreen` 已有 Enter 确认、Esc 取消。

建议补充：主界面增加一个只读帮助 overlay（建议沿用 `?`），把当前 `/`、`↑↓`、`Enter`、`Esc`、`Tab`、`Ctrl-P`、`q` 和 gain/master 键位按“列表/搜索/弹层”分组显示。这个补充比增加更多隐含快捷键更有价值，也能防止未来重新出现“焦点在输入框导致方向键无效”的回归。当前 `tui/app.py` 没有 `?` 绑定，属于明确的 P1 缺口。

### 2. k9s：持续观察 + 临时过滤 + 可退出模式

官方 README [derailed/k9s](https://raw.githubusercontent.com/derailed/k9s/master/README.md) 将 k9s 定义为持续观察 Kubernetes 资源的终端 UI；官方 [Commands / Key Bindings](https://k9scli.io/topics/commands/) 明确列出：`?` 查看帮助、`/` 过滤当前视图、`Esc` 退出 view/command/filter 模式、`Enter` 提交命令，退出可用 `:q` 或 `Ctrl-C`。

对应到 GigBuddy：

- `refresh_from_files()` 的 0.3 秒 tick 让 meter、chain 和外部导入的 library 状态持续跟随文件/数据库；`LibraryPanel` 用 fingerprint 避免刷新覆盖用户当前浏览位置。这是 k9s “watch” 模式在本项目中的合理缩小版。
- `/` 进入搜索、Enter 提交、Esc 恢复本地列表，正好对应 k9s 的“临时过滤模式”。
- 右侧链路只做观察，不再承担第二套控制入口，避免出现两个互相竞争的视图状态。

建议补充：状态行应始终区分 `local`、`remote search`、`importing`、`completed`、`failed` 五类状态，并让 Esc 在 importing 时只取消/退出临时搜索，不假装中断一个已经在后台运行的下载。当前已有 remote/importing/failed 文案，但没有统一状态模型；在继续扩展前先把状态枚举固化即可。

### 3. Harlequin：数据库操作面与配置能力分开

官方 [Harlequin README](https://raw.githubusercontent.com/tconbeer/harlequin/main/README.md) 将其定位为终端 SQL IDE，提供 SQLite adapter（例如 `harlequin -a sqlite path/to.db`）、数据目录、查询执行/导出、主题和可配置 keymap，并可通过 F1 在应用内打开文档。

对本项目的借鉴不是把 GigBuddy 变成 SQL IDE，而是保留清晰的边界：

- TUI 是音色库和实时链路的控制面；`gigbuddy` CLI/SQLite 是外部 agent 的稳定数据面。这个边界已经写入 `docs/SPEC-v2.md:8-14`，不应把任意 SQL 编辑器塞回主界面。
- 当前 `Ctrl-P` command palette（`tui/app.py:79-96`）已经提供可发现的 Search、gain/master、theme、quit 命令，延续了 Harlequin “可配置/可发现”方向。
- Harlequin 的 adapter 思路提示未来如果要开放数据库工具，应通过独立 CLI/adapter，而不是让 TUI 直接暴露危险的写 SQL。

建议补充：把可安全暴露的动作（搜索、刷新、清除筛选、打开帮助、切换主题、退出）继续加入 command palette；暂时不做通用 keymap 配置文件，先固定一套符合 TUI 约定的键位并用 headless 测试守住它。

## SQLite 参考与映射

### 1. 外键必须按连接显式开启（已完成）

SQLite 官方 [Foreign Key Support](https://www.sqlite.org/foreignkeys.html) 明确说明：外键约束为兼容性默认关闭，应用必须对每个 database connection 执行 `PRAGMA foreign_keys=ON`；不能假设默认值。`PRAGMA foreign_key_check` 可用于检查既有数据。

当前 schema 已经声明 `models.tone_id INTEGER NOT NULL REFERENCES tones(id)`；`connect()` 现在对每个连接显式开启该 pragma，孤儿 model 会被拒绝。回归测试也会验证这个约束：

1. 已在 `src/library.py:89-103` 的 `connect()` 中执行 `PRAGMA foreign_keys = ON`。
2. 已加入测试：插入不存在的 `tone_id` 必须抛 `sqlite3.IntegrityError`。
3. 对已有数据库提供一次 `PRAGMA foreign_key_check` 诊断；若发现孤儿行，先报告并修复，不要静默删除。

这是低成本、高收益的正确性修复，优先级高于 WAL 或 FTS5。

### 2. 下载后的 metadata + models 应是一个事务（已完成）

SQLite 官方 [Transactions](https://www.sqlite.org/lang_transaction.html) 说明：自动事务在最后一条 statement 完成时提交；显式 `BEGIN...COMMIT` 可把多个写入绑定成一个原子事务；SQLite 同时只允许一个写事务，失败可能返回 `SQLITE_BUSY`。

当前 `upsert_tone()` 和 `upsert_model()` 保留了直接调用时的 auto-commit 便利，但 `import_tone()` 传入 `commit=False`，把 tone 和所有 model 包在一个连接事务里。因此第 N 个 model 写入失败时，数据库不会留下半个导入。

已按这个边界实现：

- tone row 和所有 model rows 全部成功才 commit；任一步失败就 rollback。
- 下载文件本身仍可先落盘，但数据库中只记录完整成功的导入；失败状态由 UI/日志展示。
- 对重试保持现有 upsert 幂等语义，并在测试中模拟第 N 个 model 失败，确认没有半条导入。

没有引入嵌套 `BEGIN`；SQLite 的显式事务不嵌套，需要时仍应使用 savepoint。

### 3. WAL、busy timeout 与 checkpoint（P1）

SQLite 官方 [Write-Ahead Logging](https://www.sqlite.org/wal.html) 说明：WAL 通常允许读写并行，读者不阻塞写者、写者不阻塞读者；但所有进程必须位于同一主机，WAL 还需要 checkpoint，且不适合网络文件系统。官方 [PRAGMA](https://www.sqlite.org/pragma.html) 文档说明 `busy_timeout` 是每个连接的等待策略，`journal_mode=WAL` 和 `wal_autocheckpoint` 是显式可配置项。

本项目有 TUI 0.3 秒轮询、后台导入 worker、外部 `gigbuddy` CLI 和实时引擎旁路文件，读写并发是真实存在的；当前连接显式设置 `busy_timeout=5000`，仍使用 `journal_mode=delete`。建议：

- 已完成：在 `connect()` 里明确设置 `PRAGMA busy_timeout = 5000`，不依赖 Python `sqlite3.connect()` 的隐式默认值。
- P1：本地单机部署可以启用 `PRAGMA journal_mode=WAL`，并保留默认/显式的 checkpoint 策略；在启动或维护命令中观察 `-wal` 文件大小。
- P1：为 `SQLITE_BUSY` 增加明确的错误提示/重试边界，不要无限重试。
- 保持数据库位于本机 `data/`，不要把 WAL 数据库放到网络共享目录。

WAL 不是“必然更快”的开关：官方文档也列出额外 `-wal/-shm` 文件和 checkpoint 成本。应先以 TUI 轮询 + import 的真实 workload 测一次，再决定是否把 WAL 设为默认；不要同时擅自改 `synchronous`，那是另一个 durability trade-off。

### 4. Schema migration / `user_version`（P1）

SQLite 官方 [PRAGMA user_version](https://www.sqlite.org/pragma.html#pragma_user_version) 定义了一个由应用自行管理的整数版本号，SQLite 本身不会使用它。当前连接对 `models.name` 有一个向后兼容的加列处理，但数据库仍没有统一的版本化迁移入口；`user_version` 当前探测为 0。

建议建立最小迁移框架：

- 连接后读取 `PRAGMA user_version`，按版本号顺序执行短 SQL migration，并在同一事务中更新版本。
- 先把现有 schema 标为 version 1，未来的 foreign-key/WAL 连接策略可以是连接 pragma，不必伪装成 schema migration。
- migration 失败要 rollback 并阻止继续运行，避免半升级数据库。
- schema 文档同步记录当前版本和迁移历史。

这不需要 ORM，也不需要单独的 migration 包；一个小的 Python migration list 足够当前规模。

### 5. FTS5 搜索（P2，按规模触发）

SQLite 官方 [FTS5](https://www.sqlite.org/fts5.html) 支持 `CREATE VIRTUAL TABLE ... USING fts5(...)`、`MATCH` 查询、按 `rank` 排相关性，以及前缀、短语和布尔查询。当前 `src/library.py:147-149` 使用三个 `LIKE '%query%'` 条件；title 有普通索引，但前后通配符通常无法利用该索引。

建议先保留当前实现，满足现有小规模本地库和简单作者搜索；当导入量或搜索延迟成为可观测问题时再做：

- 建一个只索引 `title`, `username`, `description` 的 FTS5 表，必要时再加入 tags/makes。
- 通过外部 content 或触发器/批量 upsert 保证 FTS 行和 `tones` 同步；不要引入一个可能漂移的“第二份 metadata”。
- 用 `MATCH` 参数化查询并按 `rank` 加上 downloads_count 的次级排序。
- 给作者和标题分别提供字段过滤时，使用 FTS5 column filter，不把用户输入拼进 SQL。

FTS5 是搜索规模优化，不是当前交互正确性的前置条件；在没有测量前不值得为了“看起来成熟”增加同步复杂度。

## 分级清单

### 已落地

- 列表主导航和常用键位：`tui/app.py:104-107`、`tui/library_panel.py:32-75`。
- `/` 搜索、`Enter` 提交、`Esc` 恢复本地列表，且搜索支持标题/作者/描述：`tui/library_panel.py:148-166`、`src/library.py:138-156`。
- Type 筛选与表头循环切换：`tui/library_panel.py:216-231`。
- picker 的 tone 文件夹组织、`←/→` 树导航、Enter 选文件、焦点 metadata：`tui/picker.py:27-63`、`tui/picker.py:114-142`、`tui/picker.py:172-244`。
- 右侧 chain 只读展示、`amp-cab` 选择时移除 IR：`tui/panels.py:26-51`、`tui/app.py:171-189`。
- 后台导入、逐文件进度和失败状态：`tui/library_panel.py:168-207`。
- 后台导入的 stdout 隔离：`tui/library_panel.py:180-184`、`src/library.py:208-240`。
- CLI + SQLite + `live_chain.json` 的解耦边界：`docs/SPEC-v2.md:8-14`、`src/library.py:222-235`。
- 基础 schema、upsert 幂等和本地文件模型：`src/library.py:45-84`、`tests/test_library.py:93-172`。

### 建议补充

按投入/收益排序：

1. **已完成：显式开启外键并增加 orphan regression test。**
2. **已完成：把一次导入的 tone + models 合并为单事务。** 直接调用 upsert 仍保留默认 commit，批量导入走 `commit=False`。
3. **P1：统一 TUI 帮助 overlay。** 使用 `?`，按模式列出键位；保留 `Ctrl-P` 作为可发现动作入口。
4. **已完成基础项：显式声明 busy timeout；仍需对 SQLITE_BUSY 增加可见且有限的重试/错误状态。**
5. **P1：启用 WAL 前先做真实 workload 测量；若启用，同时记录 checkpoint 和本机文件系统约束。**
6. **P1：引入 `PRAGMA user_version` + 极小迁移列表。** 把下一次 schema 变化从“启动时悄悄 CREATE”变成可审计步骤。
7. **P2：只有在搜索规模/延迟证明需要时才引入 FTS5。** 以外部 content 或可靠同步机制避免索引漂移。
8. **P2：给 importing 状态加取消意图/状态枚举。** 当前 worker 能显示进度和失败，但取消下载需要先确认 `tone3000.download` 是否可安全中断。

### 暂不值得做

- **把 GigBuddy TUI 改造成通用 SQL IDE。** Harlequin 的价值在 adapter、数据目录和查询工作流；本项目需要的是稳定的音色库/链路控制面，通用 SQL 写入会破坏安全边界。
- **引入 ORM 或服务端数据库。** 当前数据模型只有 tones/models，本地单机 SQLite + CLI 已满足外部 agent；ORM/数据库服务会增加部署和迁移成本而不解决当前 correctness gap。
- **为每个字段做一堆过滤面板。** 当前 Type + 自由文本搜索已经覆盖类型、作者、标题和描述；在没有真实搜索痛点前，保持 `/` 临时过滤更符合成熟 TUI 的低认知负担。
- **把右侧只读 chain 再做成第二套可编辑导航。** 用户已经确定左侧是唯一控制入口；lazygit/k9s 的经验也支持“当前列表负责动作，旁边面板负责上下文”。
- **无测量地强制 `synchronous=NORMAL`、自动 VACUUM 或大范围索引。** 这些会改变 durability/写放大/文件体积，应由 workload 和故障恢复目标驱动。
- **在本地库尚未达到搜索瓶颈前引入 FTS 触发器、复杂 rank 或全文高亮。** 先把事务、外键、迁移和连接策略做正确。

## 建议验收顺序

1. 对已有数据库运行一次 `foreign_key_check` 诊断。
2. 增加 `user_version` migration skeleton，并在 schema 文档记录 version 1。
3. 增加 `?` 帮助 overlay 和 TUI headless keymap regression tests。
4. 以“0.3 秒刷新 + 一个后台导入 + CLI 查询”的真实场景比较 rollback journal/WAL；只有观测到锁等待或刷新抖动时才启用 WAL。
5. 对本地库规模和搜索延迟做基线，超过阈值后再评估 FTS5。

## 一手来源

- lazygit 官方仓库生成键位表：[Keybindings_en.md](https://raw.githubusercontent.com/jesseduffield/lazygit/master/docs/keybindings/Keybindings_en.md)
- k9s 官方 README：[derailed/k9s README](https://raw.githubusercontent.com/derailed/k9s/master/README.md)
- k9s 官方键位文档：[Commands / Key Bindings](https://k9scli.io/topics/commands/)
- Harlequin 官方 README：[tconbeer/harlequin README](https://raw.githubusercontent.com/tconbeer/harlequin/main/README.md)
- SQLite 官方事务文档：[Transactions](https://www.sqlite.org/lang_transaction.html)
- SQLite 官方 WAL 文档：[Write-Ahead Logging](https://www.sqlite.org/wal.html)
- SQLite 官方外键文档：[Foreign Key Support](https://www.sqlite.org/foreignkeys.html)
- SQLite 官方全文检索文档：[FTS5](https://www.sqlite.org/fts5.html)
- SQLite 官方 PRAGMA 文档：[PRAGMA](https://www.sqlite.org/pragma.html)，尤其是 `foreign_keys`、`busy_timeout`、`journal_mode`、`wal_autocheckpoint` 和 `user_version`
