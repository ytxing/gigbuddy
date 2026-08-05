---
name: gigbuddy
description: 用户想要某个吉他音色/某风格音色链时使用——检索 TONE3000 NAM 模型(.nam)与箱体 IR，import 进本地音色库（SQLite），组装音色链 JSON 交给实时引擎热切换或离线渲染。触发场景："给我一个XX音色"、"找XX的音色链"、"渲染XX风格"、"帮我搭一个XX链" / find guitar tones, build tone chains.
---

# GigBuddy: NL → tone chain → library / render

Repo root is the working directory (contains `src/`, `bin/`, `data/`). The library DB
lives at `data/gigbuddy.db` (SQLite, schema in docs/library-schema.md) — it is the
durable asset you write to and query through the `gigbuddy` CLI:

```
.venv/bin/gigbuddy tone search <query> [--gear amp|cab|amp-cab] [--author A] [--tag T] [--limit N]
.venv/bin/gigbuddy tone import <id>        # metadata + model files -> DB + data/tones/
.venv/bin/gigbuddy tone list [--gear ...] [--query ...] [--limit N]
.venv/bin/gigbuddy tone show <id> [--json] # full metadata incl. description (local library)
.venv/bin/gigbuddy chain get / set '<json>'   # data/live_chain.json (engine hot-swaps, UI follows)
.venv/bin/gigbuddy preset list [--json]    # named chain snapshots (manage below)
.venv/bin/gigbuddy preset save <name> [--note "..."]   # snapshot the CURRENT live chain
.venv/bin/gigbuddy preset load <name>      # apply a preset to the live chain
.venv/bin/gigbuddy preset show <name> [--json] | preset current | preset rename <old> <new>
.venv/bin/gigbuddy preset note <name> [text]   # rewrite a note without touching the chain
.venv/bin/gigbuddy preset delete <name>
```

## Workflow

1. **Parse intent**: extract from the user's description → ① amp search terms
   (style/gear/artist, e.g. "fender super reverb", "john mayer clean") ② whether a
   cab IR is needed ③ desired character (clean/overdrive/high-gain) → note it in the chain.

2. **Search amp** (TONE3000, live):
   ```bash
   .venv/bin/gigbuddy tone search "<amp terms>" --limit 10
   ```
   Prefer `gear=amp/amp-cab`, high downloads, title matching the request.
   **Record the real tone_id from the output.**

3. **Search cab IR** (only when the chain needs a cab):
   ```bash
   .venv/bin/gigbuddy tone search "<cab terms>" --gear cab --limit 10
   ```
   Prefer `gear=cab`. Skip the IR node when the amp tone is `amp-cab` (amp+cab all-in-one).

4. **Import** (download + persist metadata, one command):
   ```bash
   .venv/bin/gigbuddy tone import <id>
   ```
   This writes the full metadata row (all TONE3000 fields) + model rows with local
   paths into `data/gigbuddy.db` and downloads files to
   `data/tones/<id>-<title-slug>/`, retaining each TONE3000 basename unchanged.
   For amp tones prefer ids whose `a2` count > 0 (A2 architecture). Re-importing is
   idempotent. After import, verify with `.venv/bin/gigbuddy tone show <id>` and record the
   real local file paths.

5. **Assemble the chain** (per docs/chain-schema.md) and hand it to the engine:
   ```bash
   .venv/bin/gigbuddy chain set '{"model": "data/tones/<id>-<title-slug>/<exact-basename>.nam", "ir": "...", "gain": 1.0, "master": 0.8}'
   ```
   Every `tone_id` must come from real search output and every `model_file` from real
   import output. chain set writes `data/live_chain.json` atomically — the running
   engine hot-swaps within ~0.3s and the TUI reflects it.

6. **Optional offline render** (when the user asks for a rendered wav file):
   ```bash
   python3 src/render.py <chain.json> <dry.wav> <out.wav>
   ```
   Default dry input is `data/dry_nam_input.wav` (NAM official MIT asset); if absent,
   ask the user or synthesize with `scripts/gen_test_wav.py`.

7. **Report**: chain JSON, local file paths, confidence annotations
   (confirmed = from real search/import output).

## 批量 preset 生成

触发场景：用户要"一系列 preset / 风格包 / 给我 N 个不同风格的链"（如"来 5 个风格包：清音、crunch、金属、布鲁斯、爵士"）。

核心语义：`preset save <name>` **快照当前 live chain**（`data/live_chain.json` 的 model/ir/gain/master/quality；库内文件的 model/ir 存为逻辑引用 `model_id`，外部路径原样保留），不是"从参数构造链"。批量生成 = **逐风格循环**：`chain set` 写入链 → `preset save` 快照 → 下一个。`chain set` 是整体覆盖写（不是合并），每次必须给全 `model`/`ir`/`gain`/`master`。

工作流：

1. **解析意图 → 风格清单**：从用户描述归纳 N 个风格，每个风格明确：① amp 搜索词（风格/乐手/设备）② 期望性格（clean / overdrive / high-gain）③ 是否需要 cab IR。在报告中先列清单让用户确认（可一次性全部生成）。

2. **逐风格搜索并记录真实 id**：
   ```bash
   .venv/bin/gigbuddy tone search "<amp terms>" --gear amp --limit 10
   .venv/bin/gigbuddy tone search "<cab terms>" --gear cab --limit 10   # 需要 IR 时
   .venv/bin/gigbuddy tone search "<terms>" --author <user> --tag <tag> # 可选精确过滤
   ```
   与单链流程一致：偏好 `gear=amp/amp-cab`、高下载、标题贴合；amp-cab 一体 tone 不再单独找 cab。**记录真实 tone_id**（Hard rules）。

3. **Import 并读描述分析**（`tone show` 的 description 是 note 的素材来源）：
   ```bash
   .venv/bin/gigbuddy tone import <id>        # 幂等，重复导入无副作用
   .venv/bin/gigbuddy tone show <id>          # 本地库全字段：description/tags/gear…
   ```
   从 description/tags 归纳该音色的性格、适用场景、音色特点（如"通透清音、适合 funk/雷鬼"），作为该 preset note 的分析结论。搜索 hit 本身不含 description——描述一律以 import 后的 `tone show` 为准。

4. **组装链并批量快照**（循环每个风格）：
   ```bash
   .venv/bin/gigbuddy chain set '{"model": "data/tones/<id>-<title-slug>/<exact-basename>.nam", "ir": "...", "gain": 1.0, "master": 0.8}'
   .venv/bin/gigbuddy preset save "<风格>-<特征>" --note "<分析摘要：性格/适用场景/音色特点>"
   ```
   命名建议：小写 ASCII 连字符 `<风格>-<特征>`（如 `blues-clean-70s`、`metal-modern-gain`、`jazz-clean-neck`）；同名会覆盖——批量前先 `preset list` 检查是否与既有 preset 冲突，冲突时换名或先问用户。注意 `preset save` 会把刚保存的 preset 设为 active preset（`preset current` 可见），批量保存后 active 指向最后一条——按需用 `preset load` 切回。

5. **验证**：
   ```bash
   .venv/bin/gigbuddy preset list                 # 全部 preset + active 标记
   .venv/bin/gigbuddy preset show <name> --json   # 单条：model_id/路径/gain/master/note
   .venv/bin/gigbuddy preset load <name>          # 抽查：应用到 live chain（引擎 ~0.3s 热换）
   ```
   检查：每条 preset 的 model_id/路径来自真实输出、note 与分析结论一致、amp-cab 判断正确。

后续维护（同样走 CLI）：改 note 不动链用 `preset note <name> "<新文本>"`（省略文本即清空）；改名 `preset rename <old> <new>`；删除 `preset delete <name>`。

## Hard rules

- **Never invent tone_id / file paths** — every resource reference must come from
  real `tone search` / `tone import` output in this session; if nothing matches,
  change keywords and re-search, don't fabricate.
- On import/render failure, fix the chain config first, then retry — no skipping.
- Check the local library first when the user asks about a tone already imported
  (`.venv/bin/gigbuddy tone list --query <q>` / `tone show <id>`).
- amp/cab sample-rate and format are handled by the engine/render layer — no manual
  conversion.
- When the user didn't specify a dry input, use the default and say so in the report.

## Example

User: "给我一个 RHCP 那种清音链"
1. `.venv/bin/gigbuddy tone search "frusciante clean" --limit 10` → pick amp tone (record id)
2. `.venv/bin/gigbuddy tone search "v30 cab" --gear cab --limit 10` → pick cab (or skip if amp-cab)
3. `.venv/bin/gigbuddy tone import <amp_id>` (+ `<cab_id>` if used) → note local file paths
4. `.venv/bin/gigbuddy chain set '{"model": "...", "ir": "...", "gain": 1.0, "master": 0.8}'`
5. Report chain + files + confidence.
