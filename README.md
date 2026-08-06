# GigBuddy 🎸 — Your one-stop NAM tone manager

*v0.1.0-alpha.6 — 2026-08-06*

Guitar tone-chain tool with a **decoupled architecture**: a tone-library browser UI,
a realtime NAM engine, and an SQLite tone library that external AI agents drive
through a stable CLI. Tones come from TONE3000 (public API, anon key); rendering is
NeuralAudio (MIT).

**Open-source stance**: pure-API data source (zero local tone-library dependency),
fully MIT core stack.

## Architecture

```
                                          ┌────────────────────────────────┐
                                          │ External clients                │
                                          │ Claude Code + skill · pi · ...  │
                                          └───────────────┬────────────────┘
                                                          │ CLI commands
                                                          ▼
┌─────────────────────────────┐     ┌──────────────────────────────────────┐
│ TONE3000 public API         │────▶│ gigbuddy CLI                        │
│ search · metadata · models  │     │ tone · chain · preset                │
└──────────────┬──────────────┘     └───────────────┬──────────────────────┘
               │ remote search / import             │ query / write
               ▼                                    ▼
┌─────────────────────────────┐     ┌──────────────────────────────────────┐
│ Textual tone library UI     │◄───▶│ Local state                          │
│ browse · search · import    │     │ SQLite  data/gigbuddy.db             │
│ chain control · level meter │     │ Files   live_chain.json · level.json │
└─────────────────────────────┘     └──────────────────┬───────────────────┘
                                                       │ --live / --level-file
                                                       ▼
                                      ┌──────────────────────────────────────┐
                                      │ realtime_cli                         │
                                      │ PortAudio + NeuralAudio              │
                                      │ hot-swap model/IR · level telemetry  │
                                      └──────────────────────────────────────┘
```

- **Library DB** (`data/gigbuddy.db`): full TONE3000 metadata mirror — every search
  field preserved. Schema: docs/library-schema.md. Query via CLI or SQLite directly.
- **Agent ↔ UI round-trip**: files (`live_chain.json` / `level.json`) — unchanged
  protocol. The engine hot-swaps within ~0.3s of a chain write.
- Offline rendering (`src/render.py` + `bin/nam_cli`) remains for wav output.

## Quick start

### One-command install (macOS)

```bash
curl -fsSL https://raw.githubusercontent.com/ytxing/gigbuddy/v0.1.0-alpha.6/scripts/install.sh | bash
```

The installer creates an isolated environment under `~/.local/share/gigbuddy`,
builds the audio engines, and exposes `gigbuddy` and `gigbuddy-tui` through
`~/.local/bin`. Set `GIGBUDDY_HOME` or `GIGBUDDY_BIN_DIR` to use different paths.

After installation, run `gigbuddy` to open the TUI and start the realtime engine. Use
`gigbuddy tui --no-engine` when an engine is already running in another terminal.

#### First-run default presets

The first time you run any `gigbuddy` subcommand (or open the TUI), the built-in
default presets are set up automatically: it downloads the 16 models the presets
reference (15 amp models + 1 cabinet IR, ~4.6MB, one-time) and seeds all 15
presets. The flow is idempotent (settings marker `default_presets_initialized`)
and failure-safe — network/API errors print a notice, seed whatever already
became available, and retry on the next launch. Inspect and load them with:

```bash
gigbuddy preset list                          # 15 built-in presets (band-*/classic-*)
gigbuddy preset load band-guitar-rhcp         # apply + engine hot-swap
gigbuddy preset seed --replace                # manually rebuild the built-in catalog
```

For a Python-only CLI install, `uv tool install git+https://github.com/ytxing/gigbuddy.git`
is also possible, but it does not build the realtime engine and is not the full
GigBuddy installation.

### Uninstall

The script asks separately whether to remove downloaded tones and local data. It
leaves Homebrew PortAudio installed.

```bash
curl -fsSL https://raw.githubusercontent.com/ytxing/gigbuddy/v0.1.0-alpha.6/scripts/uninstall.sh | bash
```

For a non-interactive uninstall:

```bash
curl -fsSL https://raw.githubusercontent.com/ytxing/gigbuddy/v0.1.0-alpha.6/scripts/uninstall.sh | bash -s -- --yes
curl -fsSL https://raw.githubusercontent.com/ytxing/gigbuddy/v0.1.0-alpha.6/scripts/uninstall.sh | bash -s -- --yes --keep-data
```

```bash
# Manual/developer setup (the installer above already does this)
# 1. Create the local Python environment (Python 3.11+)
python3 -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install -e '.[dev]'

# 2. Fetch pinned C++ dependencies (macOS build path)
./scripts/bootstrap_third_party.sh

# 3. Build the engine (needs clang++ and Homebrew PortAudio)
./cpp/build.sh

# 4. Build the tone library from TONE3000 (the TUI also does this interactively)
.venv/bin/gigbuddy tone search "fender super reverb"     # search TONE3000
.venv/bin/gigbuddy tone import 19                        # download + persist metadata to DB
.venv/bin/gigbuddy tone list                             # browse the local library
.venv/bin/gigbuddy tone show 19                          # full metadata + local files

# 5. Point the engine at a chain (file names = TONE3000 semantic model names)
.venv/bin/gigbuddy chain set '{"model": "data/tones/19-fender-super-reverb-1977/Fender Super Reverb: EQ Flat, Volume 3, sm57.nam", "gain": 1.0, "master": 0.8}'
.venv/bin/gigbuddy chain get

# 6. Offline render (optional, when you want a wav file)
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
.venv/bin/gigbuddy-tui --no-engine
```

TUI features (v0.1):
- **Library browser** (left): three tabs — LOCAL (imported tones), TONE3000
  (live search + trending + sortable results with per-tab SORT/TYPE filters) and
  TOP CREATORS (6-column leaderboard: Rank/Creator/Tones/Downloads/Fav/Models,
  Most Tones by default with its own SORT bar; enter/double-click a creator row
  jumps to a TONE3000 `@author` search of that creator's tones). Search syntax:
  `@author`, `#tag`, `author:name`, `tag:name`, `make:"full device name"`.
- **Tone-chain panel** (right): INPUT / AMP / CAB nodes with state lamps
  (green active / red BYPASS / grey empty). `↑/↓` on AMP or CAB steps through
  the same tone folder's models; the CAB row has its own ▲/▼ arrow buttons.
  Double-click toggles BYPASS (engine pass-through, content kept). Parameters
  are fully editable: `g·G / m·M / q·Q` click to step, hold for fast long-press
  stepping, click the dot to zero, and click the value to type it directly
  (gain/master 0–10, quality 0–1). `d` unloads a slot.
- **Detail pane** (right, under the chain): dual-mode — Description
  (metadata) ↔ Selection (pack file list, hot-swap with enter) switched by
  `←/→` or the corner hint. Focusing an AMP/CAB node opens its pack; focusing a
  TOP CREATORS row shows that author's profile (bio + verified badge); a
  successful author verification is cached locally and the badge is reused in
  every author display; remote tones show a downloadable file list whose rows
  open the pack install screen.
- **AUDIO**: the compact bar keeps live levels + MUTE; AUDIO SETTINGS has
  input/output devices (System Default first), buffer, sample rate and latency.
- **Dry input playback**: the INPUT row can play a dry guitar file
  (space play/pause, s stop, l loop) — pick the source with enter.
- **Presets**: `p` opens the preset picker, `ctrl+s` saves, `ctrl+shift+s`
  saves as new; `ctrl+z` undoes the last preset application.
- **Level meter** (bottom): 0.3s refresh from the engine.

Search examples:

```text
super reverb @tone3000
author:tone3000 tag:clean super reverb
two rock clean @coretonecaptures
make:"Two Rock Traditional Clean" @coretonecaptures
tag:"edge of breakup" marshall
```

Engine hot-swap (`--live`): watches `data/live_chain.json` (model/ir/gain/master),
swaps model/IR atomically within 0.3s; `--level-file` feeds levels back as JSON.

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

Current live chain (`data/live_chain.json`) keys: `model` (.nam path), `ir` (.wav IR
path, optional), `gain`, `master`, `quality` (A2 model sub-model size, 0–1,
1.0 = full precision; TUI `q`/`Q`). Full DSL and node semantics:
docs/chain-schema.md.

## Known limitations (v0.1)

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

GigBuddy is released under the MIT License; see `LICENSE`. Third-party dependency
licenses and the TONE3000/model redistribution boundary are documented in `NOTICE.md`.

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
