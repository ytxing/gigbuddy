# GigBuddy Library Schema (data/gigbuddy.db)

SQLite DB mirroring TONE3000 tone metadata. External agents may query it via the
`gigbuddy` CLI (stable interface — preferred) or read SQLite directly.

## tones

Every `search_tones_a2` field is preserved (1:1 with TONE3000; `total_count` is a
search-level aggregation, not a tone attribute, and is intentionally not stored).
Rows imported by id are assembled from the same sources (tones_counts + users +
tone_tags/tags + tone_makes/makes) so they carry identical fields.

| column | source / notes |
|---|---|
| id (PK) | TONE3000 tone id |
| title, description | |
| tags | JSON array of tag names |
| gear | `amp` / `amp-cab` / `pedal` / `outboard` / `cab` / `space` / `experimental` |
| makes | JSON array of make names |
| format | `nam` / `ir` / `aida-x` / `aa-snapshot` / `proteus` (canonical) |
| platform | Deprecated compatibility alias for `format`; retained for old rows only |
| sizes | JSON array of TONE3000 size values |
| license | TONE3000 license enum |
| links | JSON array of creator links |
| downloads_count, favorites_count | |
| a1_models_count, a2_models_count, custom_models_count | |
| username, avatar_url, user_id | flattened compatibility view of the official embedded user |
| user | JSON `EmbeddedUser` object (`id`, `username`, `avatar_url`, `url`) |
| user_url | embedded user profile URL |
| is_public | official visibility flag |
| url | canonical TONE3000 tone URL (legacy mirror rows are filled deterministically) |
| images | JSON array of URLs |
| model_name | first model's metadata name (may be NULL) |
| created_at, updated_at, published_at | ISO timestamps |
| has_model_with_url, irs_count, models_count | extra search fields |
| imported_at (local) | ISO timestamp of last import |
| local_dir (local) | `data/tones` download directory; NULL after the last local file is uninstalled |

Indexes: `tones(title)`, `tones(gear)`, `tones(downloads_count DESC)`.

Every application connection enables `PRAGMA foreign_keys=ON` and sets
`PRAGMA busy_timeout=5000`. The local deployment intentionally keeps SQLite's
rollback journal until a measured TUI/import workload justifies switching to WAL.

## models

| column | notes |
|---|---|
| id (PK) | TONE3000 model id |
| tone_id (FK → tones.id) | |
| created_at, updated_at | source model timestamps |
| user_id | source creator id |
| model_url | source URL |
| name | TONE3000 `models.name` — the site display name; **this is the download filename** |
| size | TONE3000 `Size` enum (`standard` / `lite` / `feather` / `nano` / `custom`) |
| architecture_version | `1` (A1) / `2` (A2) / `custom`; NULL for non-NAM models |
| architecture | Deprecated local alias (`SlimmableContainer` / `WaveNet` / `Custom`); `IR` is a compatibility marker, not an architecture |
| local_path (nullable) | downloaded file when imported；**存相对项目根路径**（`data/tones/...`，portable v0.1），读取时按项目根解析为绝对 |

Imported assets are grouped under `data/tones/<tone-id>-<tone-title-slug>/`. Model
files keep TONE3000's **semantic name** (`models.name`, same as the site's zip
download — spaces preserved, no numbering). If an older API response has no
`name`, the importer falls back to the URL basename (decoded and query-free);
only a URL with no basename falls back to `model-<id>.<ext>`. Legacy rows
imported from the model_url basename are migrated by
`scripts/rename_semantic.py`. The TUI picker presents this relationship as
expandable tone folders and shows model metadata for the highlighted file.
TUI uninstall keeps these metadata rows, moves managed files to `data/.trash/<operation>/`
with a manifest, and sets the affected `local_path` values to NULL.

## presets

Named chain snapshots (CLI: `gigbuddy preset …`; external agents can add/query/
update/delete). Model files are stored as **logic references** (`model_id`) resolved to
the current `models.local_path` at load time, so library renames/migrations
never break a preset; non-library paths are kept verbatim in `model_path`.
路径语义（v0.1 portable）：`models.local_path` 与 `model_path`/`ir_path` 存相对项目根路径，
加载时按项目根解析为绝对。

| column | notes |
|---|---|
| id (PK, autoincrement) | |
| name (UNIQUE) | preset name |
| note | optional description |
| chain_json | `{model_id, model_path, ir_model_id, ir_path, gain, master, quality}` |
| created_at, updated_at | ISO timestamps |

Built-in chains (`gigbuddy preset seed`) use name prefixes and descriptions for
the two categories and instrument. They reference exact local model IDs.
`gigbuddy preset seed --replace` deletes
all existing presets and replaces them with the built-in catalog.

## settings

Shared toolchain state as key/value rows. `active_preset` stores the preset most
recently saved or loaded; rename follows it and deleting that preset clears it.
Use `gigbuddy preset current` instead of querying this table directly.

## chain (not in DB)

The live chain lives in `data/live_chain.json` (engine protocol). Read/write via
`gigbuddy chain get / set`.
