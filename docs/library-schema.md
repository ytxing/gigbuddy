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
| gear | `amp` / `cab` / `amp-cab` |
| makes | JSON array of make names |
| platform | `nam` / `ir` / … |
| downloads_count, favorites_count | |
| a1_models_count, a2_models_count, custom_models_count | |
| username, avatar_url, user_id | |
| images | JSON array of URLs |
| model_name | first model's metadata name (may be NULL) |
| created_at, updated_at, published_at | ISO timestamps |
| has_model_with_url, irs_count, models_count | extra search fields |
| imported_at (local) | ISO timestamp of last import |
| local_dir (local) | `data/tones` download directory |

Indexes: `tones(title)`, `tones(gear)`, `tones(downloads_count DESC)`.

Every application connection enables `PRAGMA foreign_keys=ON` and sets
`PRAGMA busy_timeout=5000`. The local deployment intentionally keeps SQLite's
rollback journal until a measured TUI/import workload justifies switching to WAL.

## models

| column | notes |
|---|---|
| id (PK) | TONE3000 model id |
| tone_id (FK → tones.id) | |
| model_url | source URL |
| name | TONE3000 `models.name` — the site display name; **this is the download filename** |
| architecture | `SlimmableContainer` (A2) / `WaveNet` (A1) / `IR` (no model_json) |
| local_path (nullable) | downloaded file when imported |

Imported assets are grouped under `data/tones/<tone-id>-<tone-title-slug>/`. Model
files keep TONE3000's **semantic name** (`models.name`, same as the site's zip
download — spaces preserved, no numbering). If an older API response has no
`name`, the importer falls back to the URL basename (decoded and query-free);
only a URL with no basename falls back to `model-<id>.<ext>`. Legacy rows
imported from the model_url basename are migrated by
`scripts/rename_semantic.py`. The TUI picker presents this relationship as
expandable tone folders and shows model metadata for the highlighted file.

## presets

Named chain snapshots (CLI: `gigbuddy preset …`; external agents can add/query/
delete). Model files are stored as **logic references** (`model_id`) resolved to
the current `models.local_path` at load time, so library renames/migrations
never break a preset; non-library paths are kept verbatim in `model_path`.

| column | notes |
|---|---|
| id (PK, autoincrement) | |
| name (UNIQUE) | preset name |
| note | optional description |
| chain_json | `{model_id, model_path, ir_model_id, ir_path, gain, master}` |
| created_at, updated_at | ISO timestamps |

Built-in recommendation chains (`gigbuddy preset seed`) are defined in
`library.SEED_CHAINS` and resolved from the local library at seed time.

## chain (not in DB)

The live chain lives in `data/live_chain.json` (engine protocol). Read/write via
`gigbuddy chain get / set`.
