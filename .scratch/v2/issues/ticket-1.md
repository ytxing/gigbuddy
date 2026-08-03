# T1: src/library.py — SQLite tone library + gigbuddy CLI

Blocked by: none
Blocks: T2, T3, T4

## Context
SPEC-v2 §3-4. TONE3000 metadata must be preserved fully (all search_tones_a2 fields) in a local
SQLite DB (data/gigbuddy.db), queryable by external agents via a stable CLI.

## Task
- `src/library.py`: sqlite3 schema `tones` + `models` (columns per SPEC-v2 §3), insert/upsert,
  list/search/show functions, `gigbuddy` CLI entry (argparse):
  `gigbuddy tone list [--gear ...] [--limit N] [--json]`, `tone search <query>`,
  `tone show <id>`, `tone import <id>` (stub: download wiring comes in T2),
  `chain get`, `chain set <json>` (read/write data/live_chain.json).
- DB file: data/gigbuddy.db (gitignored, like data/).

## Acceptance
- [x] `gigbuddy tone list` shows imported tones (none yet → empty, no crash)
- [x] `gigbuddy chain get` prints current live_chain.json; `chain set '{"master": 0.4}'` writes it
- [x] `tone import <id>` downloads nothing yet but records metadata row (or defers to T2 — decide
      and document in code) — **decided: metadata row now; download in T2** (documented in src/library.py)
- [x] All TONE3000 search fields preserved in tones table (no field dropped)

## Notes (2026-08-02, verified against the live API)
- gear value domain is `amp` / `cab` / `amp-cab` — **no `ir`** (ticket's original
  `amp|ir|amp-cab` was wrong; SPEC-v2 §4 corrected too). IR tones are gear=`cab`,
  platform=`ir`.
- `search_tones_a2` also returns `total_count` (search-level aggregation, same on
  every row) — intentionally dropped from the schema, documented in src/library.py.
- Import-by-id assembles the same 23-field shape as search rows via REST:
  tones_counts + users + tone_tags/tags + tone_makes/makes (+ models for model_name).
  Zero-field-loss verified programmatically (search row → DB → key diff).
- `get_tone_with_models` RPC does **not** exist (PGRST202) — frontend composes REST.
