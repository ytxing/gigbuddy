# GigBuddy v2 Spec — Decoupled Architecture

Status: approved (2026-08-02, user decision)
Supersedes: TUI-as-all-in-one (agent embedded in Textual) — v1 approach

## 1. Why

v1 embedded the agent (Claude Agent SDK) inside the Textual TUI. User decision: **decouple**.
- The control surface is just a simple audio/tone-library control UI — **no agent functionality inside it**.
- TONE3000 tone management with **full metadata preservation** (title/description/tags/gear/makes/
  downloads/favorites/author/ESR/images/…), stored locally as a durable asset.
- The tone-library database is **open to external agents** (Claude Code + gigbuddy skill today,
  pi or anything else tomorrow) via CLI + SQLite + file handoff.
- Agent ↔ UI round-trip via **files** (live_chain.json / level.json — existing protocol, unchanged).

## 2. Architecture

```
┌─ Agent (external, any implementation) ───────────┐
│  Claude Code + gigbuddy skill (existing)          │
│  · query library: gigbuddy CLI / read SQLite      │
│  · change chain: write live_chain.json            │
└───────────────┬──────────────────────────────────┘
                │ CLI + file handoff
┌─ Tone Library UI (simple TUI, no agent) ──────────┴─┐
│  · browse/search local library (full metadata)      │
│  · import from TONE3000 (download + metadata to DB) │
│  · chain control: pick tone → live_chain.json       │
│  · level meter                                     │
└───────────────┬────────────────────────────────────┘
                │ SQLite (tone library asset)
┌─ Engine (unchanged) ────────────────────────────────┐
│  realtime_cli (--live hot-swap, --level-file)        │
└──────────────────────────────────────────────────────┘
```

## 3. Data model — SQLite (src/library.py, DB at data/gigbuddy.db)

### tones
| column | source (TONE3000 search_tones_a2) |
|---|---|
| id (PK) | id |
| title, description | title, description |
| tags (JSON array) | tags |
| gear, makes (JSON), platform | gear, makes, platform |
| downloads_count, favorites_count | downloads_count, favorites_count |
| a1/a2/custom_models_count | a1_models_count, a2_models_count, custom_models_count |
| username, avatar_url, user_id | username, avatar_url, user_id |
| images (JSON array) | images |
| model_name | model_name |
| created_at, updated_at, published_at | created_at, updated_at, published_at |
| imported_at (local) | now |
| local_dir (local) | data/tones/ path prefix |

### models
| column | source |
|---|---|
| id (PK) | models.id |
| tone_id (FK) | models.tone_id |
| model_url | models.model_url |
| architecture | model_json.architecture |
| local_path (local, nullable) | downloaded file |

Indexes: tones(title), tones(gear), tones(downloads_count).

## 4. CLI — Agent-facing interface (src/library.py CLI / gigbuddy)

```
gigbuddy tone list [--gear amp|cab|amp-cab] [--limit N]
gigbuddy tone search <query>          # TONE3000, then import prompt
gigbuddy tone show <id>               # full metadata
gigbuddy tone import <id>             # download models + persist metadata
gigbuddy chain get                    # cat live_chain.json
gigbuddy chain set <json>             # write live_chain.json
```
Output: tabular text (or `--json`). This is the stable interface agents depend on.

## 5. UI scope (Textual, reuse v1 skeleton)

Remove: chat panel, agent client, picker's search→download flow stays (moved under library).
Add:
- Library browser: DataTable rows = tones (title/gear/dl/author), Type filter and
  header cycling; detail pane = full metadata.
- Import flow: search TONE3000 in UI → download → DB row created → appears in library.
- Chain panel + level meter + g/G/m/M hotkeys: unchanged from v1.
Layout: left = library browser 60% | right = chain + metadata 40%; bottom = meter.

## 6. Engine / protocol (unchanged)

- realtime_cli: --live data/live_chain.json, --level-file data/level.json, --in/--out/--ch/--gain/--master.
- No engine changes required in v2.

## 7. Migration checklist

| # | item | keep/new/remove |
|---|---|---|
| 1 | src/library.py: SQLite schema + import/list/search/show + CLI | new |
| 2 | tone3000.download: after download, persist metadata (or import separate step) | modify |
| 3 | TUI: remove chat/agent, add library browser + detail pane | modify |
| 4 | tui/agent.py, picker search | remove / adapt |
| 5 | gigbuddy skill: query via `gigbuddy tone ...`, chain via file | update |
| 6 | realtime_cli, live_chain.json protocol | keep |

## 8. Open questions

- Import UX: auto-import on download (in download()) vs explicit `tone import`? → default: both paths write DB.
- IR tones (gear=cab, non-A2): models stored with architecture="IR" and local .wav paths.
- Agent querying SQLite directly vs only CLI: allow both (document schema in docs/library-schema.md).
