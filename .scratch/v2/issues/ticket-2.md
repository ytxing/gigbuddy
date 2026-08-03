# T2: download/import → DB persistence

Blocked by: T1
Blocks: T3, T4

## Context
SPEC-v2 §4. Downloading a TONE3000 tone (tone3000.py `download`) must persist full metadata to
the SQLite library, and map local files (models.local_path). Both UI import and agent CLI go
through the same path.

## Task
- `src/tone3000.py`: after `download()`, upsert tone metadata (via search RPC or tones_counts REST
  for the id) + model rows with local paths; IR tones (gear=cab, non-A2) recorded with
  architecture="IR" and .wav paths.
- `gigbuddy tone import <id>`: search → download → persist → show summary (metadata + file count).

## Acceptance
- [x] `gigbuddy tone import 19` (or any id) creates tones row with full metadata + models rows
      pointing at data/tones/19-*.nam
- [x] Re-import is idempotent (upsert, no duplicate rows)
- [x] `gigbuddy tone show 19` prints description/tags/gear/downloads/author/local files

## Notes (2026-08-02)
- `tone3000.download()` gained `return_paths=True` → list of {id, tone_id, model_url,
  model_json, local_path}; default return (count) unchanged so tui/picker.py + tui/agent.py
  callers were untouched. Old callers die with T3 anyway.
- IR tones (gear=cab): architecture recorded as "IR" (no model_json); ext forced .wav.
- Import is idempotent on both levels: files skip when present, models upsert on id.
