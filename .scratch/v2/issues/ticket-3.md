# T3: TUI rework — drop agent chat, add library browser

Blocked by: T1, T2
Blocks: T5

## Context
SPEC-v2 §5. The Textual UI (tui/) drops the embedded agent (chat panel, agent.py, ClaudeSDKClient)
and becomes a pure control surface: tone library browser + chain control + meter.

## Task
- Remove: ChatPanel, agent.py usage, AgentClient, chat input (keep or repurpose layout).
- Add: LibraryPanel (DataTable: title/gear/downloads, sortable, row select → metadata detail
  pane), import flow (TONE3000 search input → download → DB → refresh table),
  chain control (existing ChainPanel node clicks + g/G/m/M), MeterBar unchanged.
- Layout: left library 3fr | right chain+detail 2fr; bottom meter.
- Pickers: reuse TonePickerScreen for chain nodes; keep local list fed from DB (not bare glob).

## Acceptance
- [x] TUI boots without agent; no API credentials needed
- [x] Library table lists imported tones with metadata; selecting a row shows full detail
- [x] Node click still hot-swaps chain (live_chain.json path)
- [x] Importing a tone from within the UI appears in the table immediately

## Notes (2026-08-02)
- tui/agent.py deleted; ChatPanel replaced by LibraryPanel (tui/library_panel.py, new).
- LibraryPanel repaints on DB fingerprint change (count+max id) — browsing position and
  remote search results survive the 0.3s tick; search mode freezes tick refresh.
- Picker local list now queries models with local_path (library.list_local_models);
  remote picks go through library.import_tone (DB persistence).
- DetailPane shows full metadata incl. local files.
- Verified headless via run_test (boot/library/detail/picker/hot-swap/UI-import).
