---
name: gigbuddy
description: Use when the user wants a guitar tone or a tone chain for a style — search TONE3000 NAM models (.nam) and cabinet IRs, import them into the local tone library (SQLite), and assemble a chain JSON for the realtime engine hot-swap or offline render. Triggers: "give me an XX tone", "find an XX chain", "render XX style", "build me an XX chain".
---

# GigBuddy: NL → tone chain → library / render

Repo root is the working directory (contains `src/`, `bin/`, `data/`). The library DB
lives at `data/gigbuddy.db` (SQLite, schema in docs/library-schema.md) — it is the
durable asset you write to and query through the `gigbuddy` CLI:

```
bin/gigbuddy tone search <query> [--gear amp|cab|amp-cab] [--author A] [--tag T] [--limit N]
bin/gigbuddy tone import <id>        # metadata + model files -> DB + data/tones/
bin/gigbuddy tone list [--gear ...] [--query ...] [--limit N]
bin/gigbuddy tone show <id> [--json] # full metadata incl. description (local library)
bin/gigbuddy chain get / set '<json>'   # data/live_chain.json (engine hot-swaps, UI follows)
bin/gigbuddy preset list [--json]    # named chain snapshots (manage below)
bin/gigbuddy preset bootstrap       # download starter models and seed catalog
bin/gigbuddy preset save <name> [--note "..."]   # snapshot the CURRENT live chain
bin/gigbuddy preset load <name>      # apply a preset to the live chain
bin/gigbuddy preset show <name> [--json] | preset current | preset rename <old> <new>
bin/gigbuddy preset note <name> [text]   # rewrite a note without touching the chain
bin/gigbuddy preset delete <name>
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

5. **Assemble the chain** (per `docs/ui-interaction-spec-v0.2.md`) and hand it to the engine:
   ```bash
   bin/gigbuddy chain set '{"slots": [{"path": "data/tones/<id>-<title-slug>/<exact-basename>.nam"}, {"path": "..."}], "gain": 1.0, "master": 1.0, "quality": 1.0}'
   ```
   Every tone id must come from real search output and every file path from real
   import output. New writes use only ordered `slots[]`; old flat `model`/`ir`
   input is read-only compatibility. `chain set` writes `data/live_chain.json`
   atomically — the running engine hot-swaps within ~0.3s and the TUI reflects it.
   A slot may be `{"path": null, "candidate": "<model path>"}` — a **bypassed**
   slot: the engine skips it until the user activates it, but the UI keeps the
   model reference. Use this for optional/tonal-shaping IRs (e.g. an amp's
   tone-switch impulses) that should not be active by default.

6. **Optional offline render** (when the user asks for a rendered wav file):
   ```bash
   python3 src/render.py <chain.json> <dry.wav> <out.wav>
   ```
   Use an installed file under `data/dry_inputs/`, for example
   `data/dry_inputs/Mayer - Guitar.wav`; if no downloaded file is available,
   synthesize `data/dry_inputs/dry-test.wav` with `scripts/gen_test_wav.py`.

7. **Report**: chain JSON, local file paths, confidence annotations
   (confirmed = from real search/import output).

## Batch preset generation

Trigger: the user asks for a "series of presets / style pack / N chains in
different styles" (e.g. "5 style packs: clean, crunch, metal, blues, jazz").

Core semantics: `preset save <name>` **snapshots the current live chain**
(`slots[]` plus `gain/master/quality` from `data/live_chain.json`; library files
keep a `model_id` logical reference, external paths are kept verbatim) — it does
**not** construct a chain from parameters. Batch generation = a **two-phase loop**:

**Phase 1 — search and import every style first** (fewer round trips; all real
ids are known before any chain is written):
1. Parse the intent into a style list; for each style record: ① amp search terms
   (style/artist/gear) ② expected character (clean/overdrive/high-gain) ③ whether
   a cab IR is needed. List the styles for the user to confirm before generating
   (can generate all at once).
2. Search each style and record real ids:
   ```bash
   bin/gigbuddy tone search "<amp terms>" --gear amp --limit 10
   bin/gigbuddy tone search "<cab terms>" --gear cab --limit 10   # when an IR is needed
   bin/gigbuddy tone search "<terms>" --author <user> --tag <tag> # optional precise filter
   ```
   Same preference as the single-chain flow: `gear=amp/amp-cab`, high downloads,
   title matching; amp-cab all-in-one tones need no separate cab. **Record real
   tone_ids** (Hard rules).
3. Import and read the description for analysis (`tone show`'s description is the
   source material for the note):
   ```bash
   bin/gigbuddy tone import <id>        # idempotent
   bin/gigbuddy tone show <id>          # full local fields: description/tags/gear…
   ```
   Summarize character, use cases, and tone traits from description/tags (e.g.
   "transparent clean, fits funk/reggae") as the analysis conclusion for that
   preset's note. Search hits carry no description — always use the imported
   `tone show` output.

**Phase 2 — assemble and snapshot per style**:
```bash
bin/gigbuddy chain set '{"slots": [{"path": "data/tones/<id>-<title-slug>/<exact-basename>.nam"}, {"path": "..."}], "gain": 1.0, "master": 0.8, "quality": 1.0}'
bin/gigbuddy preset save "<style>-<character>" --note "<analysis summary: character/use cases/tone traits>"
```
Naming: lowercase ASCII hyphenated `<style>-<character>` (e.g. `blues-clean-70s`,
`metal-modern-gain`, `jazz-clean-neck`); same-name saves overwrite — run
`preset list` before the batch to check for conflicts, rename or ask the user on
collision. Note that `preset save` sets the saved preset as active
(`preset current` reflects it); after the batch the active preset is the last
one — `preset load` to switch back as needed.

**Verify**:
```bash
bin/gigbuddy preset list                 # all presets + active marker
bin/gigbuddy preset show <name> --json   # one row: slots/model_id/paths/gain/master/note
bin/gigbuddy preset load <name>          # spot check: apply to the live chain (engine ~0.3s hot-swap)
```
Check: every model_id/path comes from real output, the note matches the analysis,
and the amp-cab decision is correct.

Maintenance (same CLI): `preset note <name> "<new text>"` to change a note without
touching the chain (omit text to clear); `preset rename <old> <new>` to rename;
`preset delete <name>` to remove.

## Hard rules

- **Never invent tone_id / file paths** — every resource reference must come from
  real `tone search` / `tone import` output in this session; if nothing matches,
  change keywords and re-search, don't fabricate.
- **Never import or use A1 (WaveNet) models** — A1 is the deprecated architecture,
  filtered out of the product (download, browse, display, use). Only A2 and IR
  (plus Custom where applicable) are valid; an A1-only tone has no usable models.
- On import/render failure, fix the chain config first, then retry — no skipping.
- Check the local library first when the user asks about a tone already imported
  (`bin/gigbuddy tone list --query <q>` / `tone show <id>`).
- amp/cab sample-rate and format are handled by the engine/render layer — no manual
  conversion.
- When the user didn't specify a dry input, use the default and say so in the report.
- Optional/tonal-shaping IRs (an amp's tone-switch impulses, non-cabinet shaping)
  go into a **bypassed** slot (`{"path": null, "candidate": "<model path>"}`) —
  they are not cabinet IRs and should not be active by default; real cabinet IRs
  are active slots.

## Example

User: "Give me an RHCP-style clean chain"
1. `bin/gigbuddy tone search "frusciante clean" --limit 10` → pick amp tone (record id)
2. `bin/gigbuddy tone search "v30 cab" --gear cab --limit 10` → pick cab (or skip if amp-cab)
3. `bin/gigbuddy tone import <amp_id>` (+ `<cab_id>` if used) → note local file paths
4. `bin/gigbuddy chain set '{"slots": [{"path": "..."}], "gain": 1.0, "master": 1.0, "quality": 1.0}'`
5. Report chain + files + confidence.
