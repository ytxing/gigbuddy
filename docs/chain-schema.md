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

## 实时引擎扩展：input 键（干声试听，live_chain.json）

实时引擎（`bin/realtime_cli --live`）使用的扁平链格式（非 nodes DSL）额外支持
顶层 `input` 键，控制输入源与干声试听回放：

```json
{
  "model": "data/tones/.../x.nam",   // 相对项目根路径（portable v0.1，引擎按 --root/exe 位置解析）
  "ir": "data/tones/.../cab.wav",    // 可省略；null = CAB 直通
  "gain": 0.8, "master": 0.8, "quality": 1.0,
  "input": {"source": "file", "file": "data/dry_inputs/Mayer - Guitar.wav",
            "state": "playing", "loop": true}
}
```
顶层键：`model`/`ir`（模型与箱体路径，均可为 null 表示直通）、`gain`/`master`/
`quality`（参数）、`input`（输入源与干声试听，见下）。

- `input` 缺失或 `source: "instrument"` → 乐器输入（音频接口，默认）
- `source: "file"` → 干声 wav 作为输入（TONE3000 网页试听素材，
  `data/dry_inputs/`，`tone3000.fetch_dry_inputs` 下载）
- `state`: `playing`（推进）/ `paused`（保留位置）/ `stopped`（归零）
- `loop`: 循环播放；非循环播完引擎自动回落 stopped
- 引擎每 0.1s 在 `data/level.json` 回传实际状态：
  `{"in":…, "out":…, "play_state": "playing", "play_pos": 3.75}`（播放位置秒）
- TUI 播放控制：全局 `space` 播放/暂停、`s` 停止、`l` 循环；链面板顶部
  INPUT 节点行显示来源与状态，单击打开输入源选择器
- preset 只存音色链（不存 input）；加载 preset 保留当前输入源
