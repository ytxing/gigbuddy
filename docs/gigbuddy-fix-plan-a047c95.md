# GigBuddy 修复计划

基线：`a047c955e062212f096baca4a0613a5646a53a90`  
基线提交：`fix: restrict GigBuddy catalog to A2 and IR models`  
状态：待实施，本文只记录计划，不包含业务代码修改。

## 1. 目标

修复最新 review 中仍存在的 TONE3000 搜索、分页、Chain 资产校验和 mutation 刷新问题，同时保持已确认的产品语义：

- 精确 model 搜索只显示 Tone 级下载状态；不新增“全部下载 / 部分下载 / 未下载”的细分状态。
- Slot 没有固定 gear 类型；合法 `.nam` 和 `.wav` 文件的处理类型由扩展名决定。
- gear 是 TONE3000 的开放分类，只影响搜索和显示，不决定文件处理类型。
- `total_count` 表示当前已经加载的去重结果数量；能否继续加载由独立的 `has_more` / `exhausted` 表示。
- mutation 刷新保留用户当前视角，不抢焦点、不重排无关的远程结果。

## 2. 当前问题清单

### P0：合法本地文件被数据库注册状态拒绝

`src/chain_protocol.py` 的 `_known_library_model_is_supported()`、`_allowed_file()`，以及 `src/library.py` 的 `_validate_known_chain_assets()` 要求文件必须存在精确 SQLite model 记录，并且记录必须属于 A2/IR。

这与当前规格冲突：合法本地 `.nam` / `.wav` 可以进入 Slot；没有可关联 Tone 时显示 `SLOT`。已注册且明确属于 A1、Custom 或未知处理类型的文件仍应拒绝。需要先区分“文件格式合法”与“catalog 元数据已注册”，不能用后者替代前者。

### P0：多来源分页重复抓取远端第一页

`src/tone3000.py` 的 `_fetch_search_prefix()` 每次从 page 1 开始。`search(page_number=N)` 重新抓取 A2 和 IR source 的完整前缀，没有保存每个 source 的 continuation、缓存 rows、exhausted 或已见 ID。

结果是翻页会重复请求已经拉取的页面，并增加延迟和 API 配额消耗。

### P0：`total_count` 是伪下界，TUI 用它推断是否还有下一页

当前搜索结果用 `max(len(merged), prefix_limit + requested)` 填充 `total_count`。它不是已加载数量，也不是可信的远端总数；未耗尽时无法代表最终 union total。TUI 进一步根据 `total_count` 和当前 page 长度推断 `_tone_has_more`，导致分页状态不可靠。

### P0：CAB / SPACE source 选择可能漏掉 NAM

`src/tone3000.py` 的 `_IR_GEAR_FILTERS` 和 `_search_sources()` 把 `cab`、`space` 只送入 IR source，甚至可能发送 `format=ir`。

当前规格把 gear 定义为开放显示/筛选分类，不能由 gear 推断处理类型。因此 CAB 或 SPACE gear 下的 NAM 可能在搜索阶段被漏掉。

### P1：A2/IR 合并不是全局排序

`_merge_search_sources()` 按 source 轮询交错结果，而不是对 union 去重后的结果重新按用户选择的 sort 全局排序。跨 source 的 downloads 等排序会违反表格排序语义；排序相同的行还需要稳定的 Tone/Model ID tie-breaker。

### P1：整体 Chain replacement 后旧 target 可能被恢复

`tui/chain_state.py` 的 `replace_chain()` 会清空 target，但 `tui/panels.py` 的 `restore_view_anchor()` 在 target 为 `None` 时仍按旧 Slot 下标 fallback 并调用 `focus_slot()`。

外部整体写入、Preset load、undo、redo 后应清空 target 和 bypass candidates；可以保留 viewport，但不能按旧下标或旧路径猜测新的身份。用户重新聚焦 Slot 后才建立新的 target。

### P1：mutation 后 TONE3000 行状态可能不立即刷新

`tui/library_panel.py` 的 `_apply_local_download_states()` 可以更新 remote rows/cache/table，但 `_reconcile_mutation_worker()` 只刷新 LOCAL rows，没有调用该路径。用户停留在 TONE3000 tab 时，安装或卸载后的状态 marker 可能要切 tab 或重新高亮才更新。

## 3. 非问题与保持不变项

- 精确 model 搜索的下载状态不作为缺陷。当前产品只要求 Tone 级状态，不要求区分部分下载和完全下载。
- INPUT 点击、INPUT 控件聚焦不会改变 target。
- 动态重组对 INPUT、Library Search 等外部焦点的保护目前没有发现新回归。
- Tab 切换和基础 undo/redo 测试目前没有发现新回归。
- 不重新请求 TONE3000、不重排远程结果、不清空 DetailPane，仍是 mutation reconcile 的约束。

## 4. 分阶段修复

### Phase 1：明确本地文件兼容边界

修改：`src/chain_protocol.py`、`src/library.py`。

实现要点：

1. 先按扩展名验证 `.nam` / `.wav`；扩展名决定 NAM / IR 处理类型。
2. 未注册但路径合法、文件存在且格式允许的本地文件可进入 Chain，Tone 关联缺失时派生标签为 `SLOT`。
3. 如果存在明确 catalog 记录，则继续执行 A2/IR 业务限制；A1、Custom 或明确不支持的处理类型仍拒绝。
4. gear 只作为派生显示和筛选信息，不参与处理类型判断。
5. 保留旧 preset/live chain 读取兼容和现有错误信息契约。

验收：未注册 NAM、未注册 WAV、已注册 A2、已注册 IR、A1/Custom、缺失文件、未知扩展名和 preset 加载各有测试。

### Phase 2：引入按 source 的增量分页状态

修改：`src/tone3000.py`，再适配 `tui/library_panel.py`。

新增内部搜索状态，缓存键必须覆盖完整搜索身份：query、sort、gear/type、author、tag、make 以及当前 source/filter 版本。每个 source 至少保存：

- `next_page` 或等价 continuation；
- 已缓存的原始/规范化 rows；
- `seen_ids`；
- `exhausted`；
- 远端 total（仅作为参考，不能冒充当前 union count）。

建议增加结构化返回值：

```text
SearchPage:
  rows
  loaded_count
  has_more
  exhausted
```

保留现有 `search(...) -> list[dict]` 兼容包装，先让 TUI 使用结构化接口。首次搜索只拉取满足当前页面所需的数据；后续 load-more 只请求尚未拉取的 source page。新的搜索身份创建新状态，旧状态可按现有缓存生命周期回收。

验收：第 N 页不再请求任一 source 已拉取的 page；A2/IR source 可以独立耗尽；重复 ID 不增加 loaded count；改变 query 或筛选条件不会复用旧 rows。

### Phase 3：固定 count 与 has-more 语义

修改：搜索返回值和 `tui/library_panel.py` 分页状态。

规则固定为：

- `loaded_count = len(deduplicated_rows)`；
- `has_more = 任一 source 尚未 exhausted`；
- `exhausted = 所有 source 均 exhausted`；
- 只有所有 source exhausted 后，最终 union total 才可确定。

TUI 的 more action、提示条、页码和 disabled 状态只读取 `has_more` / `exhausted`，不再根据伪造的 row `total_count` 或“当前页长度等于 page size”推断。若保留远端 total，必须单独命名并注明其是服务端参考值。

验收：加载 40 行时 count 为 40；加载 80 行时 count 为 80；source 未耗尽时即使当前页不足 page size 仍可继续；所有 source 耗尽后 more action 消失或禁用。

### Phase 4：修正 CAB / SPACE 和全局排序

修改：`src/tone3000.py`。

1. `cab`、`space` 作为 gear filter 时同时查询适用的 A2 和 IR source。
2. 只有明确的 `ir` processing filter 才使用 IR-only 语义；gear token 不得隐式设置 `format=ir`。
3. source union 去重后按用户选定 sort 全局排序。
4. 使用稳定的 Tone ID/Model ID 作为 tie-breaker，并固定升降序和缺失值规则。

验收：CAB NAM、CAB IR、SPACE NAM、SPACE IR 都能出现；跨 source 的 downloads、name、date 等排序一致；重复 Tone/Model 只有一条且结果顺序稳定。

### Phase 5：修复整体 Chain replacement 的视角恢复

修改：`tui/chain_state.py`、`tui/panels.py`、必要时 `tui/app.py`。

将“整体替换”与普通行内刷新区分开：外部写入、Preset load、undo、redo 成功后原子地清空 target 和 bypass candidates，恢复 viewport 时不得调用旧 Slot fallback。允许保留滚动位置；不得抢 INPUT、Library Search 或其他外部控件焦点。只有用户显式重新聚焦 Slot 后才设置 target。

验收：每种整体 replacement 都断言 target 为 `None`、bypass candidates 为空、焦点不被抢；普通 TONE3000 行内 mutation 仍能按稳定 row key 恢复 cursor/viewport。

### Phase 6：修复 mutation 后远程状态刷新

修改：`tui/library_panel.py`。

在 `_reconcile_mutation_worker()` 中按以下顺序执行：

1. 校验 mutation generation/request identity；
2. 刷新 LOCAL 数据；
3. 调用 `_apply_local_download_states()` 更新 TONE3000 remote rows、cache 和 table；
4. 按稳定 row key 恢复 anchor；
5. 发布 highlight/detail 状态，且不重排远程结果、不切 tab、不清 DetailPane。

失败、取消、阻塞和 no-op 不发布刷新事件。安装和卸载后停留在 TONE3000 当前 row 的状态必须在同一刷新周期内更新。

## 5. Ticket 与依赖

建议按以下顺序实施，单个 ticket 保持单一行为变化：

| ID | 内容 | 依赖 |
|---|---|---|
| GIG-01 | 本地 `.nam/.wav` 校验与 SLOT 派生标签 | 无 |
| GIG-02 | source 分页状态、rows cache、seen IDs、exhausted | 无 |
| GIG-03 | `SearchPage` 与 TUI `has_more/exhausted` 接入 | GIG-02 |
| GIG-04 | CAB/SPACE 双 source 和全局稳定排序 | GIG-02 |
| GIG-05 | 整体 Chain replacement 的 target 清空与焦点保护 | 无 |
| GIG-06 | mutation 后 remote download state 原位刷新 | GIG-03 |
| GIG-07 | teardown race 测试稳定性修复，单独处理 | 可并行，不能混入业务验收 |

GIG-02、GIG-05 可以并行；GIG-03 完成后再做 GIG-06；GIG-04 依赖 GIG-02 的 source state，但可以和 GIG-03 并行。每个 ticket 实施后先跑其聚焦测试，再进入下一个依赖 ticket。

## 6. 测试计划

### 单元和 API

```bash
.venv/bin/python -m pytest -q tests/test_tone3000_api.py
.venv/bin/python -m pytest -q tests/test_chain_protocol.py tests/test_library.py
```

覆盖本地文件边界、旧格式兼容、source 增量请求、重复 ID、筛选身份隔离、CAB/SPACE、全局排序和 count/has-more 语义。

### TUI 回归

```bash
.venv/bin/python -m pytest -q tests/test_t05_dynamic_chain_panel.py tests/test_tab_switch.py tests/test_chain_undo_redo.py
```

另加或更新测试覆盖：

- INPUT、Library Search、DetailPane 聚焦时整体 replacement 不抢焦点；
- Preset load、undo、redo 后 target/bypass candidates 清空；
- 安装/卸载后停留在 TONE3000 row 的 marker 即时更新；
- 分页 load-more 不重复请求已缓存 source page；
- query/sort/gear/author/tag/make 变化不串用旧状态。

### 全量门槛

```bash
.venv/bin/python -m pytest -q tests
```

当前基线结果为 `386 passed, 1 failed`。失败位于 `tests/test_metadata_theme_colors.py:159`，退出时 `on_preset_panel_highlighted()` 在 Textual screen stack 已清空后访问 `self.focused`，属于既有 teardown/lifecycle 问题。业务修复完成后必须单独修复或隔离该测试问题，再把全量测试作为发布门槛。仓库根目录直接运行会在第三方 xsimd Playwright 测试收集阶段因缺少 `playwright` 失败，这应记录为环境/依赖问题，不作为 GigBuddy 功能回归。

## 7. 发布门槛

完成发布前必须满足：

- 所有 P0 项有回归测试并通过；
- TONE3000 翻页的请求日志证明不会从第一页重复抓取已加载 source page；
- `loaded_count`、`has_more`、`exhausted` 在 UI 和 API 中语义一致；
- CAB/SPACE 的 NAM 与 IR fixture 均可检索；
- 整体 Chain replacement 不恢复旧 target、不抢外部焦点；
- mutation 后 remote 状态原位更新且不改变远程排序；
- 聚焦测试通过，全量测试通过，或已明确隔离并修复既有 teardown 问题；
- 不改变精确 model 搜索的 Tone 级下载状态语义，不增加版本号或发布动作，除非另行授权。

## 8. 实施约束与非目标

- 本计划不改变 Tone 级下载状态产品语义。
- 本计划不引入稳定 Slot ID，不改变 `slots[]` 协议，不把 gear 写入 Slot。
- 本计划不通过重新请求远端第一页来“校准”分页；状态必须由每个 source 的增量游标和本地 union 维护。
- 本计划不重做 UI 布局、不清空用户 Detail context、不新增无关性能优化。
- 实施顺序遵循 `ask-matt` 路由的 spec -> ticket -> TDD/implement -> code review；每个 ticket 完成后再进入依赖项。

## 9. 待确认决策

1. `SearchPage.rows` 是返回当前累计 union，还是只返回本次新增 rows。建议 API 返回累计 union，并另带 `new_rows` 供 TUI 增量更新，避免调用方自行重建状态。
2. 服务端 total 与多 source union total 不一致时，UI 是否显示服务端 total。建议不显示，直到所有 source exhausted；UI 只显示 `loaded_count` 和 more 状态。
3. 未注册本地文件是否需要允许用户手动补充 Tone 元数据。建议本轮不做，先显示 `SLOT` 并保证加载、保存、恢复链路正确。
