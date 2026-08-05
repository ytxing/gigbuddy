# ADR-0001：Chain 协议迁移到 Slots 数组

- 状态：提议
- 日期：2026-08-04

## 背景

Chain 从固定两级（model/ir）升级为多 Slot 架构（REQ-032，见 CONTEXT.md 词汇表）：真实多级 DSP、Slot 无固定种类、上限 6、顺序即身份、⌥↑/⌥↓ 对调、Preset 加载时自动转换。

## 决策

`live_chain.json` 的链配置从 `model`/`ir` 两个键迁移为**有序 `slots` 数组**：

```json
{
  "input": { "source": "file", "file": "data/dry_inputs/x.wav" },
  "slots": [
    { "path": "data/tones/19-.../Fender... sm57.nam" },
    { "path": "data/tones/60066-.../Mesa 4x12.wav" }
  ],
  "gain": 0.8, "master": 0.8, "quality": 1.0
}
```

- 每项只有 `path`（Model 文件路径）。**语义标签与处理类型都不存**——处理类型由扩展名推断（.nam→NAM / .wav→IR），语义标签（AMP/CAB/PEDAL）由 Model 所属 Tone 的 gear 推断，均为派生值。
- 数组顺序 = 信号处理顺序；下标 = 身份（对调 = 数组重排，无稳定 id）。
- 空槽 = 数组中的 `{ "path": null }`（显示 NONE，引擎跳过）。
- `input` 键保持独立（不在 slots 内）。

## 理由

- **深度**：协议层只暴露 `read_chain()`/`write_chain()`（调用方零改动），新旧格式、上限校验、类型推断全部藏在实现里。
- **纯数据无逻辑**：slots 数组只承载"顺序 + 路径"这一最小事实，标签/处理/语义全部按需派生——避免协议里出现"类型字段与加载内容不一致"的状态空间（用户明确"加载什么就是什么"）。
- **替代方案**：
  a. 保留 model/ir + 增加可选槽位键——两套表示长期并存，状态空间翻倍（model 键与 slots 首项可能不一致），放弃。
  b. 每项存 type 字段——类型与文件内容可能漂移（amp 槽加载 .wav 时 type 算什么？），违背"加载决定类型"语义，放弃。

## 后果

- 旧 preset（model/ir 键）加载时由协议层自动转换为 slots（`{model}→[{path}], ir 缺位不产生空槽`），不迁移数据文件。
- 引擎 CLI 与 TUI 的调用点不变，仅实现适配。
