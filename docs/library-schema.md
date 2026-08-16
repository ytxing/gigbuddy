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
| format | Canonical TONE3000 format: `nam`, `ir`, `aida-x`, `aa-snapshot`, or `proteus` |
| platform | Deprecated TONE3000 alias retained for old rows; new code reads `format` first |
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

## GigBuddy visibility boundary

The database preserves TONE3000's raw architecture counts for provenance, but
GigBuddy's usable asset set is narrower: only NAM A2
(`architecture_version=2`) and IR models are exposed, downloaded, counted, or
resolved into a chain. A1, Custom, AIDA-X, AA-SNAPSHOT, Proteus, and unknown
architectures are hidden. A mixed Pack may therefore have a larger raw
`models_count` than the number of rows shown in its Pack view.

## models

| column | notes |
|---|---|
| id (PK) | TONE3000 model id |
| tone_id (FK → tones.id) | |
| created_at, updated_at | source model timestamps |
| user_id | source creator id |
| model_url | source URL |
| name | TONE3000 `models.name` — the site display name; **this is the download filename** |
| architecture_version | Canonical TONE3000 architecture: `1` (A1), `2` (A2), `custom`, or NULL for non-NAM |
| architecture | Legacy backend token (`SlimmableContainer` / `WaveNet` / `IR`), retained for compatibility |
| local_path (nullable) | downloaded file when imported；**存相对项目根路径**（`data/tones/...`，portable v0.1），读取时按项目根解析为绝对 |

Imported assets are grouped under `data/tones/<tone-id>-<tone-title-slug>/` as a
Tone Pack. Model files are direct children of that folder and keep TONE3000's
**semantic name** (`models.name`, the same as the site's zip download — spaces
preserved, no numbering). Remote imports also generate an optional
`gigbuddy.json` manifest beside the files; it is portable extra metadata, not
the engine protocol. If an older API response has no `name`, the importer falls
back to the URL basename (decoded and query-free); only a URL with no basename
falls back to `model-<id>.<ext>`. Legacy rows imported from the model_url
basename are migrated by `scripts/rename_semantic.py`. The TUI picker presents
this relationship as expandable tone folders and shows model metadata for the
highlighted file.
TUI uninstall keeps these metadata rows, moves managed files to `data/.trash/<operation>/`
with a manifest, and sets the affected `local_path` values to NULL.

## presets

Named chain snapshots (CLI: `gigbuddy preset …`; external agents can add/query/
update/delete user rows). User Presets are stored as editable JSON documents in
`data/presets/<id>-<name-slug>.json`. Repository-owned Presets are distributed
separately under `presets/built-in/*.json` and indexed as read-only rows at
startup. SQLite remains the searchable index and compatibility layer. Model
files are stored as **logic references** (`model_id`) resolved to the current
`models.local_path` at load time, so library renames/migrations never break a
preset; non-library paths are kept verbatim in `model_path`.
路径语义（v0.1 portable）：`models.local_path` 与 `model_path`/`ir_path` 存相对项目根路径，
加载时按项目根解析为绝对。

| column | notes |
|---|---|
| id (PK, autoincrement) | |
| name (UNIQUE) | preset name |
| note | optional description |
| chain_json | canonical `{slots, gain, master, quality}` snapshot; legacy flat `model`/`ir` rows remain readable |
| source | `user` for editable local JSON; `bundled` for read-only repository Presets |
| source_key | stable repository catalog identity for `bundled` rows; NULL for user Presets |
| created_at, updated_at | ISO timestamps |

The JSON document uses `schema_version: 1`, `kind: "gigbuddy-preset"`, and the
fields `id`, `name`, `note`, `chain`, `created_at`, and `updated_at`. GigBuddy
synchronizes these files through the explicit `refresh_preset_catalog()` write
boundary. CLI Preset commands, the TUI catalog poll, Preset import, and model
uninstall dependency checks call that boundary before they require a current
catalog. `preset_get()`, `preset_get_by_id()`, and `preset_list()` are pure
SQLite reads: they never import, move, rewrite, or delete files and never
register repository rows.

During an explicit refresh, GigBuddy imports new documents from
`data/presets/`, exports legacy SQLite-only rows, and reconciles hand-edited
tracked documents. The file wins when it changed; an invalid file is kept and
ignored with a warning. Deleting a tracked file deletes its indexed preset.
Rename, note, draft, delete, and legacy seed operations update both user
representations atomically as far as the local SQLite/file boundary permits.
Bundled rows never create files under `data/presets/`; deleting or renaming a
repository document removes its old `bundled` index row after a complete,
non-empty catalog scan. A missing, temporarily empty, unreadable, or ambiguous
repository directory does not clear the indexed catalog. User rows with no
explicit bundled provenance are never auto-claimed by matching names or chain
contents.

### Shareable Preset documents

`gigbuddy preset export NAME [PATH]` writes a separate portable document for
sharing with another GigBuddy user. Its kind is
`"gigbuddy-shareable-preset"`, its provider is `"tone3000"`, and every loaded
model is identified by its TONE3000 `model_id`. It contains no local path,
Pack identity, database id, or machine-specific filename.

```json
{
  "schema_version": 1,
  "kind": "gigbuddy-shareable-preset",
  "provider": "tone3000",
  "name": "blackface-clean",
  "note": "Clean amp with a 4x12 IR",
  "chain": {
    "slots": [
      {"model_id": 12345, "input_gain_db": 1.5},
      {"model_id": 67890, "bypass": true},
      {"model_id": 12345}
    ],
    "gain": 0.8,
    "master": 0.9,
    "quality": 1.0
  }
}
```

`chain.slots` keeps Slot order, repeated model references, bypass state, and
per-Slot trims. An empty Slot uses `{"model_id": null}`. The Slot
`model_id` values are the only model references in the format; GigBuddy derives
the unique, first-use-ordered download list from them. Local-only Pack assets
cannot be exported in this format. Older files may contain a redundant
top-level `model_ids` field; it is ignored for compatibility.

New exports write the effective `output_gain_db` for every non-empty Slot,
including `0.0`, so an export/import round trip cannot change its level. A
missing `output_gain_db` is reserved for older share files; when importing one,
GigBuddy applies the NAM model's recommended output calibration if available.

The share file is imported explicitly; it is not a local preset just because it
has a `.json` suffix and it should not be placed in `data/presets/`. Importing
it with `gigbuddy preset import preset-name.json` resolves each missing model ID
through TONE3000's exact model endpoint, groups IDs by parent Tone, downloads
only the requested models, and writes the normal local preset after every model
is available. Already installed models are reused. Add `--load` to apply the
new preset to the live Chain, or `--name NAME` to choose a local name.

Built-in Presets are registered from `presets/built-in/*.json` whenever GigBuddy
opens. Registration is local and does not wait for downloads: each row is shown
as `PREPARING`, `READY`, or `UNAVAILABLE`, and failed rows remain visible for a
later retry. The TUI downloads missing models in the background. Loading an
unavailable built-in Preset, or running `gigbuddy preset bootstrap`, retries the
required TONE3000 models without writing the live chain until every Slot can be
resolved.

`gigbuddy preset seed --local-only` performs registration without network I/O.
The `--replace` option is retained for command-line compatibility but is
deprecated and no longer deletes user Presets. The repository documents are the
only built-in catalog source; there is no separate Python seed definition.

## settings

Shared toolchain state as key/value rows. `active_preset` stores the preset most
recently saved or loaded; rename follows it and deleting that preset clears it.
Use `gigbuddy preset current` instead of querying this table directly.

## chain (not in DB)

The live chain lives in `data/live_chain.json` (engine protocol). Read/write via
`gigbuddy chain get / set`.
