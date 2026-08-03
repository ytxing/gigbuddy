# GigBuddy 音色链 DSL v0.1

对齐 LLM2Fx-Tools 的"效果类型+顺序+参数"可执行链表示；MVP 先支持 amp + cab_ir 两个节点类型，效果器节点（comp/od/delay/reverb/mod）为二期预留（接 pedalboard 后启用）。

## 顶层结构

```json
{
  "name": "mayer-clean",              // 链名（必须，供保存/对比）
  "nodes": [
    {
      "type": "amp",                  // 节点类型: amp | cab_ir | (二期) comp/od/delay/reverb/mod
      "source": "tone3000",           // 资源来源: tone3000 | local
      "query": "two rock sss clean",  // 检索词（source=tone3000 时用于 search）
      "tone_id": "35497",             // 搜索结果中的真实 id（**禁止编造**，须来自 search 输出）
      "model_file": "data/tones/35497-01.nam",  // download 后的本地文件
      "params": {}
    },
    {
      "type": "cab_ir",
      "source": "tone3000",
      "query": "celestion g12-65",
      "tone_id": "27465",
      "model_file": "data/tones/27465-01.wav",
      "params": {"mix": 1.0}          // 0~1 干湿比，默认 1.0
    }
  ],
  "notes": "John Mayer 式清音：Two-Rock 系 + G12-65 箱体"
}
```

## 节点类型

| type | 渲染行为 | 参数 |
|---|---|---|
| `amp` | NeuralAudio 推理 .nam（SlimmableContainer 自动用默认子模型） | `quality` 0~1：A2 子模型尺寸（1.0 = Full 默认，更小 = Lite/省 CPU；A1 忽略） |
| `cab_ir` | FFT 卷积（IR 能量归一 + 自动重采样到干音采样率） | `mix`: 0~1 |
| `comp/od/delay/reverb/mod`（二期） | 接本地 VST3 效果器（pedalboard 子进程） | 各插件参数 |

## 反幻觉规则（anti-invention，借鉴 CortexRig）

1. `tone_id` **必须**来自 `src/tone3000.py search` 的真实输出，禁止从记忆/想象生成
2. `model_file` **必须**是 `download` 后真实存在的文件（渲染前校验）
3. 检索结果不满足需求时**重新搜索**（换关键词/排序），不许凑合
4. 节点参数标注可信度：链生成后对每个节点注明 `confirmed`（来自搜索结果/配对知识）或 `unconfirmed`（LLM 推断）

## 渲染校验

`src/render.py` 加载 chain JSON 时：
- 每个节点 `model_file` 必须存在，缺失即报错并指明节点
- `params.mix` 默认 1.0，越界报错
- 渲染顺序 = nodes 数组顺序（amp → cab_ir → ...）
