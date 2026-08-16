# ADR-0002: Preset catalog ownership and mutable data layout

- Status: accepted
- Date: 2026-08-16

## Context

GigBuddy has three durable Preset representations:

- repository-owned JSON under `presets/built-in/`;
- user-editable JSON under `data/presets/`;
- a searchable SQLite projection in `data/gigbuddy.db`.

Historically, `library.py`, bootstrap code, the TUI, and install scripts each
performed part of the synchronization. A database reset could assign new row
IDs while old JSON remained, a repository update could race an editable-file
refresh, and install/uninstall code could disagree about which `data` directory
was durable. The result was not one isolated bug: one operation could be
overwritten later by a poll or reconciliation path using a different source.

## Decision

`PresetCatalog` is the sole application-facing owner of Preset synchronization,
but it delegates each consistency domain to one internal module:

- `EditablePresetStore` owns user rows, Preset-related settings, editable JSON,
  file tracking, quarantine, and crash recovery;
- `BundledPresetSource` and `BundledPresetRegistry` own repository scans,
  stable `catalog_key` identity, registration, and the registration cache;
- `ModelPreparation` owns missing-model downloads, verified availability, and
  process-local preparation state.

`PresetCatalog` orders operations that cross those domains. A full refresh
registers a stable bundled snapshot first, then gives all valid and invalid
bundled names to editable reconciliation. A preparation command registers the
snapshot before selecting rows for download, then verifies that the same
registered chains still exist before publishing preparation results.

`library.py` remains the compatibility adapter used by the CLI and TUI. It
provides Chain normalization and model-library operations to the catalog, then
delegates every `preset_*` persistence operation through the catalog interface.
Ordinary reads remain side-effect free. Callers that need current filesystem
state use the explicit refresh or synchronization commands.

Lock ownership follows the same boundaries. `EditablePresetStore` owns the
reentrant reconciliation lock and takes it before `BEGIN IMMEDIATE` for every
editable mutation or reconciliation. `BundledPresetRegistry` owns a separate
registration lock and its own SQLite writer transaction. `ModelPreparation`
owns the state and download locks; it never holds the state lock while reading
SQLite or performing network I/O, and it never opens a Preset transaction.

For a user-level install, mutable data is physically separate from the Git
checkout. The default layout is:

```text
~/.local/share/gigbuddy/       code checkout
~/.local/share/gigbuddy-data/  mutable data
~/.local/share/gigbuddy/data -> ../gigbuddy-data
```

`GIGBUDDY_DATA_HOME` can select another external directory. The compatibility
link keeps existing runtime paths valid. Source checkouts keep their existing
embedded `data/` layout unless an external directory is explicitly configured.

## Invariants

- A bundled row has `source = 'bundled'` and a non-empty `source_key`.
- A user row is never claimed as bundled by matching its name or contents.
- Bundled JSON never contains a machine-local model path and is never copied to
  `data/presets/`.
- An untracked editable file that conflicts with a bundled name is preserved in
  `data/presets/.quarantine/`.
- An incomplete, empty, unreadable, changing, or ambiguous bundled scan cannot
  delete the last valid SQLite projection.
- Missing models affect availability; they do not remove the Preset row or
  write the live Chain.
- Every editable mutation and editable-file reconciliation holds the catalog
  reconciliation lock and reserves the SQLite writer with `BEGIN IMMEDIATE`.
- A failed editable publish or SQLite commit restores the previous top-level
  JSON files before the error is returned. A failed delete cannot leave a
  discoverable JSON file that a later refresh would import again.
- Editable reconciliation only commits the exact file version it parsed. A file
  changed or recreated by an external editor during publication is never
  overwritten; the pass rolls back and the next explicit refresh retries it.
- An editable mutation carries the tracked file token from its SQLite snapshot
  into file isolation. If the file changed after the mutation's preparation,
  the mutation restores the file, rolls back SQLite, and reports a conflict.
- Cleanup after a committed mutation is best effort. A warning policy or backup
  cleanup failure cannot make a successfully committed operation report failure.
- A hidden transaction directory left by abrupt process termination is resolved
  from the committed SQLite row and file-tracking token. Uncertain JSON versions
  are quarantined before the committed row is republished; failed preservation
  stops recovery without deleting the committed row.
- A built-in preparation result may change only the semantic generation that
  started it. The generation includes ordered Slot model IDs, gains, bypass,
  chain controls, and model-to-Tone source metadata; local projection fields do
  not participate. A stale completion or exception cannot mutate a replacement.
- Install and uninstall compare resolved data-directory identities, including
  relative links, before moving or deleting data.
- Uninstall removes command links or generated wrappers only when their
  physically resolved executable target is inside the selected install.

## Consequences

Preset changes now have one application-facing orchestrator and one owner per
consistency domain, while the existing `library.preset_*` interface remains
compatible. Repository Presets can ship with the application without a separate
seed definition or generated starter database. Code updates no longer carry
mutable data inside the checkout.

SQLite and the filesystem still cannot share one native transaction. Editable
JSON is therefore staged completely in a hidden same-filesystem transaction
directory. Existing `id-*.json` files are moved there before the new document
is published through an atomic no-overwrite link. Reconciliation verifies the
isolated file token against the version it parsed and stores the staged
publication's token, rather than sampling a path that an external editor may
already have replaced. The SQLite row and tracking token are then committed. A
caught failure before commit removes the attempted publication and restores the
isolated files; after commit, cleanup failure can only leave hidden backup files,
which catalog scans do not import. Delete uses the same sequence without
publishing a replacement, so a committed deletion cannot be undone by the next
refresh.

This is an application-level recovery protocol; it is not a claim that SQLite
and filesystem updates form one native transaction. On the next explicit
refresh, a hidden `.preset-*` directory is interpreted using SQLite as the
commit record. If the tracked top-level file still matches, only transaction
debris is cleaned. Otherwise all uncertain top-level, staged, and backup JSON is
quarantined, the stale tracking token is removed, and the committed SQLite row
is republished. A committed delete therefore stays deleted, while a mutation
interrupted before commit restores the old row. If preservation fails, recovery
stops and retries later instead of silently discarding a candidate file.

The recovery tests use abrupt process termination. Full power-loss durability
still depends on filesystem ordering and is not equivalent to a native
cross-store transaction. Conflicts are moved instead of overwritten, and
destructive bundled reconciliation still requires a complete stable scan.
