# GigBuddy 领域词汇表

> 词汇表只收领域概念，不收实现细节（实现决策见 docs/adr/ 与 backlog）。

## 核心实体

- **Chain（音色链）**：有序的 Slot 列表 + 全局参数（gain/master/quality）。数组顺序即信号处理顺序。
- **Slot（槽位）**：链上可加载一个音频处理单元的位置。本身**无种类**——加载内容决定一切。
- **Input（输入源）**：链头独立实体（乐器设备 / 干声音频文件），**不是 Slot**。
- **Tone（音色）**：TONE3000 上的一个音色条目，带 gear 类型与多个 Model 文件。
- **Model（模型）**：Tone 内的具体文件（`.nam` 变体或 `.wav` IR）——**Slot 的加载粒度是 Model**（非 Tone 整体）。一个 Model 的扩展名决定其处理类型。
- **Preset（预设）**：Chain 的命名快照（Slots + 参数）。旧格式（model/ir 键）加载时自动转换为新格式。
- **空 Slot（Empty Slot）**：未加载内容的槽位（显示 NONE，信号直通，不占 DSP）。

## 类型与标签（易混淆三角，务必区分）

- **Tone 类型（gear）**：TONE3000 的分类——amp / amp-cab / pedal / outboard / cab / space / experimental。其中 amp-cab 是"自带 cab 效果的单 `.nam`"。
- **Tone 格式（format）**：nam / ir / aida-x / aa-snapshot / proteus；`platform` 仅是旧别名。`space` 通常承载 IR，但显式 `format` 优先。
- **Model 架构（architecture_version）**：NAM 模型为 `1`（A1）、`2`（A2）或 `custom`；非 NAM 模型为 NULL。`IR` 不是架构值。
- **处理类型（Processing Type）**：由文件扩展名决定——`.nam` → NAM 推理；`.wav` → IR 卷积。Slot 的音频处理方式。
- **语义标签（Label）**：Slot 显示名（AMP / CAB / PEDAL / EXP），由所加载 Tone 的 gear 派生。仅用于显示与交互。

## 规则

- Slot 上限 6（含空槽）。
- Slot 顺序可调：选定后与相邻槽位对调（⌥↑/⌥↓），不支持拖拽；无邻居则不可移动。
- 文件范围仅限 TONE3000 音色文件（.nam / .wav IR）；插件不在当前范围。
- 全局参数（gain/master/quality）与 Slot 无关，恒为链级属性。
- **顺序即身份**：Slot 无稳定 id，数组下标即身份；对调 = 数组重排，Preset 保存重排后顺序，引擎按数组序处理。
- 同一 Model 可加载进多个 Slot（如双 OD 串联），允许。
- Slot 加载位置**不限顺序**（任意类型可放任意位置，引擎按序处理，不做智能干预）。
- Slot 加载粒度 = Model 文件；加载 Tone 时先选 Model 变体再进槽（pack 表选文件语义）。
