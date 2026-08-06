# GigBuddy 🎸 — Your one-stop NAM tone manager

*v0.2.14 — 2026-08-06*

Guitar tone-chain tool with a **decoupled architecture**: a tone-library browser UI,
a realtime NAM engine, and an SQLite tone library that external AI agents drive
through a stable CLI. Tones come from TONE3000 (public API, anon key); rendering is
NeuralAudio (MIT).

**Open-source stance**: pure-API data source (zero local tone-library dependency),
fully MIT core stack.

## Architecture

```
┌─ Agent (external — Claude Code + gigbuddy skill, pi, anything) ─┐
│  · query library:  gigbuddy tone list/search/show/import         │
│  · change chain:   gigbuddy chain set (writes live_chain.json)   │
└───────────────┬──────────────────────────────────────────────────┘
                │ CLI + file handoff
┌─ Tone Library UI (Textual TUI, no agent inside) ─────┬──────────┐
│  browse/search local library (full metadata)          │          │
│  import from TONE3000 (download + metadata → DB)      │          │
│  chain control: pick tone → live_chain.json           │  SQLite  │
│  level meter                                         │ data/     │
└───────────────┬───────────────────────────────────────┴ gigbuddy │
                │ live_chain.json / level.json               .db    │
┌─ Engine (realtime_cli, PortAudio + NeuralAudio) ─────────────────┐
│  --live hot-swap (atomic ordered Slot-chain swap)                 │
│  --level-file telemetry                                          │
└──────────────────────────────────────────────────────────────────┘
```

- **Library DB** (`data/gigbuddy.db`): full TONE3000 metadata mirror — every search
  field preserved. Schema: docs/library-schema.md. Query via CLI or SQLite directly.
- **Agent ↔ UI round-trip**: `live_chain.json` carries the ordered Slot chain and
  revision; managed TUI writes use a transaction/control sidecar and the engine
  reports session, transaction, revision, status, and acknowledgement sequence in
  `level.json`.
- Offline rendering (`src/render.py` + `bin/nam_cli`) remains for wav output.

## Quick start

```bash
# 1. Build the engine (NeuralAudio + NAM Core, MIT; needs cmake/clang++ and brew install portaudio)
./cpp/build.sh

# 2. Build the tone library from TONE3000
bin/gigbuddy tone search "fender super reverb"     # search TONE3000
bin/gigbuddy tone import 19                        # download + persist metadata to DB
bin/gigbuddy tone list                             # browse the local library
bin/gigbuddy tone show 19                          # full metadata + local files

# 3. Point the engine at an ordered Slot chain (file names = TONE3000 semantic model names)
bin/gigbuddy chain set '{"slots": [{"path": "data/tones/19-fender-super-reverb-1977/Fender Super Reverb: EQ Flat, Volume 3, sm57.nam"}], "gain": 1.0, "master": 0.8, "quality": 1.0}'
bin/gigbuddy chain get

# 4. Offline render (optional, when you want a wav file)
python3 src/render.py chain.json data/dry.wav out.wav
```

## TUI (realtime tone-chain console)

The TUI starts the realtime engine by default. Use `--no-engine` when the engine
is already running in another terminal.

```bash
# optional terminal 1: realtime engine (hot-swap + level telemetry)
# 省略 --in/--out 使用系统默认音频设备；设备列表与选择见 TUI AUDIO SETTINGS 面板
./bin/realtime_cli --live data/live_chain.json --level-file data/level.json

# terminal 2: TUI (Textual; omit --no-engine if terminal 1 is not running)
.venv/bin/python -m tui --no-engine
```

TUI features (v0.2):
- **Library browser** (left): four view tabs — LOCAL (imported tones), TONE3000
  (live search + trending + sortable results with per-tab SORT/TYPE filters) and
  TOP CREATORS (6-column leaderboard: Rank/Creator/Tones/Downloads/Fav/Models,
  Most Tones by default with its own SORT bar; enter/double-click a creator row
  jumps to a TONE3000 `@author` search of that creator's tones). Search syntax:
  `@author`, `#tag`, `author:name`, `tag:name`, `make:"full device name"`.
- **Tone-chain panel** (right): INPUT plus 0–6 ordered Slots with derived
  uppercase labels and state lamps. `tab/shift+tab` navigates Slots;
  `↑/↓` steps a Slot through its pack's models and `alt+↑/alt+↓` reorders
  adjacent Slots. `+` adds, `d` deletes, and `enter` toggles BYPASS/restore.
  Parameters are fully editable: `g·G / m·M / q·Q` step, hold for repeated
  stepping, click the center dot to restore the protocol default, and click the
  value to type it directly (gain/master 0–10, quality 0–1).
- **Detail pane** (right, under the chain): dual-mode — Description
  (metadata) ↔ Selection (pack file list, hot-swap with enter) switched by
  `[/]` or the view-tab strip. Focusing a Slot opens its pack; focusing a
  TOP CREATORS row shows that author's profile (bio + verified badge); a
  successful author verification is cached locally and the badge is reused in
  every author display; remote tones show a downloadable file list whose rows
  open the pack install screen.
- **AUDIO**: the compact bar keeps live levels + MUTE; AUDIO SETTINGS has
  input/output devices (System Default first), buffer, sample rate and latency.
- **Dry input playback**: the INPUT row can play a dry guitar file
  (space play/pause, s stop, l loop) — pick the source with enter.
- **Presets**: the Presets pane owns `n` (Save As), `s` (save active), `e`
  (edit a full Slot/parameter/note draft), `r` (rename), `d` (delete),
  `enter` (load), and `ctrl+z`/`ctrl+shift+z` undo/redo preset application.
- **Level meter** (bottom): 0.3s refresh from the engine.

Search examples:

```text
super reverb @tone3000
author:tone3000 tag:clean super reverb
two rock clean @coretonecaptures
make:"Two Rock Traditional Clean" @coretonecaptures
tag:"edge of breakup" marshall
```

Engine hot-swap (`--live`): watches `data/live_chain.json` (`slots[]`, parameters,
input and mute), swaps the complete chain atomically; `--level-file` feeds levels
back as JSON.

## gigbuddy CLI (agent-facing interface)

```
gigbuddy tone list [--gear amp|cab|amp-cab] [--limit N] [--query Q] [--json]
gigbuddy tone search <query> [--gear ...] [--limit N] [--json]   # TONE3000 live
gigbuddy tone show <id> [--json]                                 # full metadata
gigbuddy tone import <id>                                        # download + persist
gigbuddy chain get                                               # cat live_chain.json
gigbuddy chain set '<json>'                                      # write it (hot-swap)
gigbuddy preset seed                                             # add/update built-in catalog
gigbuddy preset seed --replace                                   # delete all presets, rebuild catalog
gigbuddy preset list                                             # named chain snapshots
gigbuddy preset save <name> [--note "..."]                       # snapshot current chain
gigbuddy preset load <name>                                      # apply (engine hot-swap)
gigbuddy preset current                                          # active name; * means dirty
gigbuddy preset rename <old> <new>                               # rename, preserving active
gigbuddy preset note <name> [note]                               # set / clear description
gigbuddy preset show <name> / delete <name>                      # inspect / remove
```

Presets store model **logic references** (`model_id`), resolved to current paths
at load time — library renames never break a preset. The active preset is shared
by the CLI and TUI. TUI: `p` loads, `ctrl+s` twice confirms an active-preset
overwrite, and `ctrl+shift+s` saves as a new name. In the PRESETS pane, use
`space` / `a` / `d` / `esc` for select, select all, bulk delete, and clear;
`n` / `r` / `e` create, rename, or edit the focused preset.

LOCAL uses the same `space` / `a` / `d` / `esc` selection model. Uninstalling
moves managed files to `data/.trash`, clears `models.local_path`, and retains
tone/model metadata. Active-chain files are blocked; preset dependencies require
an extra confirmation. The compact main AUDIO bar keeps live levels and MUTE
visible; AUDIO SETTINGS opens input/output, buffer, sample rate, and latency.

Notes:
- `gear` domain is `amp` / `cab` / `amp-cab` (no `ir`); IR tones are `gear=cab`, `platform=ir`.
- Import is idempotent (files skip when present, rows upsert). Files are grouped
  under `data/tones/<tone-id>-<title-slug>/` and keep TONE3000's **semantic model
  name** (`models.name`, same as the site's zip download — spaces preserved);
  use `tone show` for the real path. IR tones record architecture `"IR"` and
  download `.wav` files.
- The gigbuddy skill (`.claude/skills/gigbuddy`) drives this CLI end to end; its
  anti-invention rules (tone ids only from real search output) apply to any agent use.

## Directory structure

```
cpp/            nam_cli.cpp (offline NAM render CLI)
                realtime_cli.cpp (realtime engine: --live hot-swap + --level-file + VU)
src/            library.py (SQLite schema + gigbuddy CLI) / tone3000.py (TONE3000 layer)
                render.py (offline render pipeline)
tui/            Textual UI: app.py (layout/hotkeys) / panels.py (chain/detail/meter)
                library_panel.py (browser) / picker.py (tone picker) / live.py (file channel)
scripts/        gen_test_wav.py (fallback test-signal generator)
data/           (gitignored: dry inputs / outputs / tone cache / live_chain.json / level.json / gigbuddy.db)
third_party/    (gitignored: NeuralAudio + deps, fetched by scripts)
docs/           SPEC-v2.md (decoupled architecture) / chain-schema.md (chain DSL)
```

## Tone-chain format

Current `data/live_chain.json` uses an ordered `slots[]` array with 0–6 entries;
each entry is `{ "path": "..." }` or `{ "path": null }`. The protocol also
contains `gain`, `master`, `quality`, `mute`, `input`, and a non-negative
`revision`. The managed-only `_transaction_id` is used for one candidate or
rollback and is not part of preset data. `model`/`ir` are read-only legacy input;
canonical writes remove them. Full rules are in
`docs/ui-interaction-spec-v0.2.md` and `docs/adr/0001-slots-chain-protocol.md`.

## Known limitations (v0.2)

- **TOP CREATORS** reads TONE3000's official `user_public_counts` leaderboard,
  the same source used by `tone3000.com/top-creators`. Tones, Downloads,
  Favorites, and Models are stable server-side aggregates.
- **Creator followers/following** are not shown: the public users API exposes
  no follower fields and the website is behind Cloudflare.
- **Remote data loads are network-bound** — the first TOP CREATORS visit waits
  for one leaderboard request; displayed statistics are not rewritten later.
- Notifications and a few table refreshes are timing-sensitive under heavy
  load; rare test flakes have been observed in CI-style runs.

## License

MIT (to be finalized). Dependencies: NeuralAudio (MIT), NAM Core (MIT), RTNeural (BSD-3),
Eigen (MPL-2), math_approx (MIT), PortAudio (MIT-like), Textual (MIT).

## Roadmap

- [x] MVP: render core (NeuralAudio) + amp/IR pipeline + TONE3000 retrieval layer
- [x] Blueprint DSL v0.1 (docs/chain-schema.md) + GigBuddy skill (NL → chain → render)
- [x] Realtime engine: hot-swap (--live), level telemetry, USB interface verified
- [x] v2 decoupling: SQLite tone library (full metadata) + gigbuddy CLI + TUI browser
      (agent removed from TUI; DB open to external agents)
- [ ] Local VST3 effects (pedalboard, subprocess-isolated; activate placeholder nodes)
- [ ] Crossfade switching (anti-click, guitarix delta-delay style)
- [ ] Render-vs-reference automatic evaluation loop
- [ ] AudioStream interface output
