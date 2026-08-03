# T5: README + tests wrap-up

Blocked by: T3, T4
Blocks: none

## Context
v2 landing: docs up to date, basic regression coverage for the library layer.

## Task
- README: v2 architecture (decoupled), gigbuddy CLI reference, UI usage, agent usage.
- Tests: library.py unit tests (schema upsert, CLI list/show/chain get/set) — pytest or
  plain asserts runnable via `.venv/bin/python -m pytest` (add pytest to venv if needed).

## Acceptance
- [x] README reflects v2 (no agent-in-TUI claims) — rewritten in English; adds
      docs/library-schema.md (SPEC §8 open question, closed)
- [x] Library tests pass; CLI smoke-tested end to end — tests/test_library.py
      (10 tests, network-free, tmp-dir isolated): schema/upsert/JSON-roundtrip/
      filters/models/chain-atomic/import-mocked/IR-wav/CLI roundtrip/CLI json

## Notes (2026-08-02)
- v2 fully landed: T1..T5 all closed. SPEC-v2 §7 migration checklist complete.
- Whole repo is still uncommitted (git repo exists, no commits yet) — commit
  whenever the user wants; data/ is already gitignored.
