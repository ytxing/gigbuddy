# 多 Slot 链架构 · 设计定稿（REQ-032）

状态：历史设计参考。当前实现的唯一规格来源是
`docs/ui-interaction-spec-v0.2.md` v0.2.14；协议决策见
`docs/adr/0001-slots-chain-protocol.md`。本文件中的旧布局和快捷键描述不覆盖
当前规范。

---

## 1. 目标

把链从固定两级（AMP + CAB）升级为**多 Slot 链**：真实多级 DSP、Slot 无固定种类（加载内容决定一切）、上限 6、顺序可调。文件范围仅限 TONE3000 音色文件（`.nam` / `.wav` IR），插件后续再议。

## 2. 领域词汇（摘要，完整见 CONTEXT.md）

| 术语 | 定义 |
|---|---|
| Chain | 有序 Slot 列表 + 全局参数（gain/master/quality），顺序即信号处理顺序 |
| Slot | 链上可加载一个音频处理单元的位置；本身无种类 |
| Model | Tone 内的具体文件（`.nam` 变体 / `.wav` IR）；**Slot 加载粒度 = Model** |
| Input | 链头独立实体（乐器/干声），不是 Slot |
| Preset | Chain 的命名快照；旧格式加载时自动转换 |
| 空 Slot | 未加载（NONE，信号直通，不占 DSP） |
| 处理类型 | 派生值：`.nam`→NAM 推理、`.wav`→IR 卷积（由扩展名推断） |
| 语义标签 | 派生值：AMP/CAB/PEDAL/EXP（由 Model 所属 Tone 的 gear 推断），仅显示与交互用 |

## 3. 协议规格（live_chain.json）

### 3.1 新格式

```json
{
  "input": { "source": "file", "file": "data/dry_inputs/x.wav" },
  "slots": [
    { "path": "data/tones/19-.../sm57.nam" },
    { "path": null },
    { "path": "data/tones/60066-.../Mesa 4x12.wav" }
  ],
  "gain": 0.8,
  "master": 0.8,
  "quality": 1.0
}
```

- `slots`：有序数组。每项仅含 `path`（Model 绝对路径或 null=空槽）。**不存类型/标签/参数**——全部派生。
- 数组下标 = 身份；顺序 = 处理顺序；对调 = 数组重排（无稳定 id）。
- `input` 独立于 slots，语义不变（preset 不存 input）。
- `gain/master/quality` 链级属性不变。
- **上限 6 槽**（含空槽）。协议层写入时校验：超限拒绝并报错。

### 3.2 旧格式转换（只读兼容）

旧格式 `{"model": "...", "ir": "..."}`（可能缺键）加载时转换：
- `model` 存在 → 首个 slot `{path: model}`
- `ir` 存在 → 追加 slot `{path: ir}`
- 缺位键**不产生空槽**（空槽只能由用户显式添加）
- 转换纯内存进行，不重写旧文件；preset 保存时写新格式

### 3.3 派生规则

- `slot_processing(path)`：`.nam` → `nam`；`.wav` → `ir`；其他 → `unknown`（引擎跳过并告警）
- `slot_label(path)`：查 Model 所属 Tone 的原生 gear → 只做 uppercase 展示；例如
  `amp`→`AMP`、`amp-cab`→`AMP-CAB`、`outboard`→`OUTBOARD`。未知非空 gear
  也保留原始 token 并 uppercase；只有 Empty Slot 显示 `SLOT`。

## 4. 引擎规格（cpp/realtime_cli.cpp）

- **接口不变**：`--live` 热切换监听 live_chain.json；`--list`/`--in/--out/--gain/--master` 不变。
- **DSP 链构建**：读 `slots` 数组 → 按序构建处理节点：
  - `.nam` → NAM 推理节点（NeuralAudio）
  - `.wav` → IR 卷积节点（FIR）
  - `null` / 未知类型 → 跳过（直通，零开销）
- 信号流：input → gain → slot₁ → slot₂ → … → master → out（与现有链同架构扩展）
- **热切换**：槽位增删/重排/换文件 → 重建链（有变化才重载，失败保留旧值——沿用现有语义）。
- **上限**：>6 槽的配置拒绝加载（协议层已挡，引擎侧兜底）。
- 单槽直通时不引入额外延迟（与现状单 amp 或单 ir 性能一致）。
- WavInput（input 干声播放）完全不变。

## 5. TUI 规格（tui/panels.py / tui/app.py）

### 5.1 链面板布局

- ChainPanel 渲染为**动态槽位列表**（每槽一行，样式沿用现有 AMP/CAB 行：状态灯 + 语义标签 + 标题 + 文件名 + 右侧箭头块）。
- 行序 = slots 数组序。
- 空槽行：`○ NONE` 灰底（现有空态样式）。
- INPUT 行固定在列表顶部（不入槽，不可移动/删除）。
- 全局参数行（g·G / m·M / q·Q）与提示条保持在面板底部（不变）。

### 5.2 交互与键位

| 操作 | 键位 | 说明 |
|---|---|---|
| 聚焦槽位 | 单击 / ↑↓ 在槽位间移动 | 沿用现有节点聚焦 |
| 切换同 pack Model | ↑↓（槽位聚焦时） | 沿用现有语义（`_switch_chain_model`） |
| 添加空槽 | `+` 键或面板按钮 | 上限 6；满时禁用并提示 |
| 删除槽位 | `d`（聚焦槽位时） | 卸载并移除该行；空槽也可删 |
| 上移一格 | **⌥↑** | 与上方邻居对调（无邻居则无操作 + 提示） |
| 下移一格 | **⌥↓** | 与下方邻居对调（无邻居则无操作 + 提示） |
| 双击 | 与邻居对调？——**否**：双击保持 bypass 语义（如有） | 移动仅 ⌥ 键 |
| 加载 Model 进槽 | pack 表（DetailPane Selection）选文件 Enter/双击 | 替换当前聚焦槽的 path |

- 无拖拽；无空格不可继续移动（"没有空格不支持继续移动"语义 = 无邻居不可移，加空槽后可再移）。
- 焦点在槽位间移动不改变顺序。

### 5.3 提示条（遵循 REQ-024/025 规则）

右侧常驻：`⌥↑⌥↓ move · Esc` 等固定动作；左侧状态变化：`{n} slots · loading…` 等。

### 5.4 联动

- 槽位操作（增删/移动/换 Model）→ 写 live_chain.json → 引擎热切换 + DetailPane 同步（沿用现有响应式链）。
- DetailPane pack 表：聚焦槽位时打开对应 pack；换 Model 写回该槽 path。

## 6. Preset 规格（src/library.py）

- 保存：快照链（slots + 参数），写新格式。
- 加载：读 preset → 协议层解析（旧格式自动转换）→ 写 live_chain.json → 引擎热切换。
- preset 快照域不变：slots + gain/master/quality（input 不入）。

## 7. 测试策略

| 层 | 测试点 |
|---|---|
| 协议 | 新格式读写往返；旧格式转换（model 缺位/ir 缺位/双缺）；上限 6 校验；path null 空槽 |
| 引擎 | 多槽链构建顺序；空槽/未知类型跳过；热切换增删重排；失败保留旧值 |
| TUI | 槽位列表渲染（含空槽）；添加/删除/⌥ 对调（含无邻居 noop）；pack 表换槽；提示条文案；preset 新旧加载 |
| 端到端 | 6 槽全 NAM 链真实引擎冒烟；preset 旧格式加载 → 新格式保存 |

## 8. 已决项（定稿确认，2026-08-04）

1. `+` 键添加空槽（实现时查 BINDINGS 冲突，冲突则改面板按钮）——已确认。
2. 双击 bypass 语义保留（槽位级 bypass，与删除/空槽并存）——已确认。
3. **`amp-cab` 标签 = `AMP-CAB`**（保留原生连字符，表示单 `.nam` 自带 cab 效果）。
