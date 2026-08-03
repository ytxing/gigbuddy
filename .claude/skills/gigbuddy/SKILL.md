---
name: gigbuddy
description: 用户想要某个吉他音色/某风格音色链时使用——检索 TONE3000 NAM 模型(.nam)与箱体 IR，import 进本地音色库（SQLite），组装音色链 JSON 交给实时引擎热切换或离线渲染。触发场景："给我一个XX音色"、"找XX的音色链"、"渲染XX风格"、"帮我搭一个XX链" / find guitar tones, build tone chains.
---

# GigBuddy: NL → tone chain → library / render

Repo root is the working directory (contains `src/`, `bin/`, `data/`). The library DB
lives at `data/gigbuddy.db` (SQLite, schema in docs/library-schema.md) — it is the
durable asset you write to and query through the `gigbuddy` CLI:

```
bin/gigbuddy tone search <query> [--gear amp|cab|amp-cab] [--limit N]
bin/gigbuddy tone import <id>        # metadata + model files -> DB + data/tones/
bin/gigbuddy tone list [--gear ...] [--query ...] [--limit N]
bin/gigbuddy tone show <id>
bin/gigbuddy chain get / set '<json>'   # data/live_chain.json (engine hot-swaps, UI follows)
```

## Workflow

1. **Parse intent**: extract from the user's description → ① amp search terms
   (style/gear/artist, e.g. "fender super reverb", "john mayer clean") ② whether a
   cab IR is needed ③ desired character (clean/overdrive/high-gain) → note it in the chain.

2. **Search amp** (TONE3000, live):
   ```bash
   bin/gigbuddy tone search "<amp terms>" --limit 10
   ```
   Prefer `gear=amp/amp-cab`, high downloads, title matching the request.
   **Record the real tone_id from the output.**

3. **Search cab IR** (only when the chain needs a cab):
   ```bash
   bin/gigbuddy tone search "<cab terms>" --gear cab --limit 10
   ```
   Prefer `gear=cab`. Skip the IR node when the amp tone is `amp-cab` (amp+cab all-in-one).

4. **Import** (download + persist metadata, one command):
   ```bash
   bin/gigbuddy tone import <id>
   ```
   This writes the full metadata row (all TONE3000 fields) + model rows with local
   paths into `data/gigbuddy.db` and downloads files to
   `data/tones/<id>-<title-slug>/`, retaining each TONE3000 basename unchanged.
   For amp tones prefer ids whose `a2` count > 0 (A2 architecture). Re-importing is
   idempotent. After import, verify with `bin/gigbuddy tone show <id>` and record the
   real local file paths.

5. **Assemble the chain** (per docs/chain-schema.md) and hand it to the engine:
   ```bash
   bin/gigbuddy chain set '{"model": "data/tones/<id>-<title-slug>/<exact-basename>.nam", "ir": "...", "gain": 1.0, "master": 0.8}'
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

## Hard rules

- **Never invent tone_id / file paths** — every resource reference must come from
  real `tone search` / `tone import` output in this session; if nothing matches,
  change keywords and re-search, don't fabricate.
- On import/render failure, fix the chain config first, then retry — no skipping.
- Check the local library first when the user asks about a tone already imported
  (`bin/gigbuddy tone list --query <q>` / `tone show <id>`).
- amp/cab sample-rate and format are handled by the engine/render layer — no manual
  conversion.
- When the user didn't specify a dry input, use the default and say so in the report.

## Example

User: "给我一个 RHCP 那种清音链"
1. `bin/gigbuddy tone search "frusciante clean" --limit 10` → pick amp tone (record id)
2. `bin/gigbuddy tone search "v30 cab" --gear cab --limit 10` → pick cab (or skip if amp-cab)
3. `bin/gigbuddy tone import <amp_id>` (+ `<cab_id>` if used) → note local file paths
4. `bin/gigbuddy chain set '{"model": "...", "ir": "...", "gain": 1.0, "master": 0.8}'`
5. Report chain + files + confidence.
