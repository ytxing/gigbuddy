# Built-in Presets

GigBuddy ships a small catalog of calibrated guitar and bass rigs with the
checkout. The catalog is useful on a new machine before any model has been
downloaded, and it does not depend on a generated SQLite database or on files
left behind by a previous installation.

## Where They Come From

The source documents are versioned here:

```text
presets/built-in/*.json
```

Each document has this shape:

```json
{
  "schema_version": 1,
  "kind": "gigbuddy-bundled-preset",
  "catalog_key": "marshall-jcm800-klon",
  "name": "marshall-jcm800-klon",
  "note": "...",
  "chain": {
    "slots": [
      {"model_id": 381187, "output_gain_db": 4.25}
    ],
    "gain": 1.0,
    "master": 1.0,
    "quality": 1.0
  }
}
```

The `catalog_key` is the stable identity of the bundled Preset and does not
change when its display `name` changes. The `model_id` is the stable TONE3000
model identity. A bundled document never contains a machine-local path. NAM
slots may include the recommended `output_gain_db`; the value is calibrated
from the NAM loudness metadata and is kept when the model is downloaded.

## Startup Behavior

On launch, GigBuddy performs a fast local registration pass:

1. It reads the repository documents and registers their names and chain data
   in the local SQLite index.
2. It immediately displays the built-in rows in **PRESETS**. No model download
   or live-chain write occurs during this pass.
3. A background worker groups missing model IDs by their parent TONE3000 Tone
   and downloads them without blocking the TUI.
4. The row state changes as the local files become usable.

The state column means:

| State | Meaning |
|---|---|
| `PREPARING` | The background worker is downloading one or more required models. |
| `READY` | Every model required by the Preset is available locally. |
| `UNAVAILABLE` | A download failed, login is missing, or a required model could not be resolved. |
| `USER` | The row is an editable Preset created by the user. |

An unavailable row remains visible. Selecting or double-clicking it starts a
background retry for that Preset only. A failed retry leaves the current live
chain untouched and keeps the row available for a later retry. Loading a ready
row applies the chain through the normal managed-chain path.

## Login and Installation

The install scripts still perform the TONE3000 login check and can open the
system browser. The installer only registers the bundled catalog; it does not
wait for the model downloads required by all 20 starter Presets. If the user
declines login, the catalog still appears, while remote-backed rows remain
`UNAVAILABLE` until the user signs in with:

```sh
gigbuddy tone login
```

Without a controlling terminal, the installer stops and asks for an explicit
choice. An automated install that intentionally skips initial Preset
registration must pass `--skip-presets`:

```sh
curl -sSL https://raw.githubusercontent.com/ytxing/gigbuddy/v1.2.4/scripts/install.sh | bash -s -- --skip-presets
```

After login, open the TUI and load a row to retry one rig. To explicitly retry
all built-in models from the CLI, use:

```sh
gigbuddy preset bootstrap
```

The command reports failed downloads by Preset name and leaves those Presets
visible. It does not replace the live chain.

## CLI Workflow

List the catalog without downloading models:

```sh
gigbuddy preset list
gigbuddy preset list --json
```

Load one built-in Preset. If its models are missing, the command automatically
attempts to download only the models required by that Preset before writing the
live chain:

```sh
gigbuddy preset load marshall-jcm800-klon
```

The same command works for an ordinary user Preset, but user Presets retain the
existing behavior and do not trigger a bundled catalog download.

## Editing Rules

Bundled rows are read-only because the repository document is their source of
truth. The following operations are rejected for a bundled row:

- rename;
- edit chain or note;
- delete;
- overwrite with `preset save` or `preset import` under the same name.

To customize one, load it and use **Save As** with a new name. The new row is a
normal user Preset and is stored as an editable JSON file under:

```text
data/presets/*.json
```

If a user Preset already has the same name as a bundled document, the user row
wins and is never overwritten by repository synchronization. Existing rows from
before the bundled catalog was introduced have no reliable provenance marker,
so they remain user-owned even when their name or sound happens to match a
bundled document. To use the repository version, keep the user row under a new
name or remove it explicitly; synchronization never infers ownership from
names, notes, files, or audible content.

An untracked JSON file whose name conflicts with a bundled Preset is moved to
`data/presets/.quarantine/` instead of being imported or deleted. Rename the
Preset inside that preserved file before placing it back in `data/presets/`.

## Failure Boundaries

Bundled synchronization never writes `data/live_chain.json`. A failed model
download therefore cannot replace a working rig with an incomplete chain.
Model files are downloaded through the normal staged TONE3000 import path, and
only verified local model rows count toward `READY`.

If a repository Preset changes while an older download is still running, the
older result is discarded. Slot order, gains, bypass state, and the declared
Tone source are all part of that preparation generation, even when the
replacement references the same model IDs.

The catalog is also safe to use without the native audio engine by starting the
TUI with `--no-engine`; download and Preset management remain available, while
audio playback and hot-swapping require the engine.
