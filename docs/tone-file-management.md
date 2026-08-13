# GigBuddy tone file management

**Language:** [English](tone-file-management.md) | [中文](tone-file-management.zh-CN.md)

This guide is for GigBuddy users. It explains
where tone files live, how to download a Pack from TONE3000, how to add a Pack
from your own disk, and what LOCAL, Chain, and Presets do with those files.

## The short version

- A Tone Pack is one directory under GigBuddy's managed `data/tones/` directory.
- A direct child ending in `.nam` is treated as a NAM model.
- A direct child ending in `.wav` is treated as an IR.
- `gigbuddy.json` is optional metadata. It cannot change the meaning of the
  file extension.
- Remote TONE3000 imports are downloaded by GigBuddy. Local Packs are added by
  copying a directory into `data/tones/`.
- GigBuddy scans only the Pack's direct child files. It does not recursively
  search subdirectories.

## Find the managed directory

The path depends on how GigBuddy was installed:

| Installation | Tone Pack directory |
|---|---|
| Source checkout | `<checkout>/data/tones/` |
| Default user install | `~/.local/share/gigbuddy/data/tones/` |
| Custom install location | `<GigBuddy home>/data/tones/` |

The same home contains the local index at `data/gigbuddy.db`, the live Chain at
`data/live_chain.json`, and editable Presets under `data/presets/`.

Do not put a Pack in `~/.local/share/gigbuddy/tones/` or beside the checkout's
`src/` directory. GigBuddy will only scan the managed `data/tones/` root.

## Remote import from TONE3000

Use remote import when you want TONE3000 metadata, a source link, creator
information, and download tracking in GigBuddy.

### From the TUI

1. Open the `TONE3000` view and sign in when GigBuddy asks for your TONE3000
   session.
2. Search for a Tone and open its Pack/model view.
3. Select the Pack or the individual A2/IR models you want to keep.
4. Confirm the install. The files appear in `LOCAL` after the download is
   complete.

### From the CLI

From a source checkout:

```sh
bin/gigbuddy tone login
bin/gigbuddy tone search "fender super reverb"
bin/gigbuddy tone import <tone-id>
bin/gigbuddy tone list
bin/gigbuddy tone show <tone-id>
```

In a user install, use `gigbuddy` instead of `bin/gigbuddy`:

```sh
gigbuddy tone search "fender super reverb"
gigbuddy tone import <tone-id>
```

The search output shows the real numeric Tone ID. Running the import command
again will not create a second Pack. It downloads the supported A2 NAM and IR
files, records the complete remote metadata, and keeps the Pack in the same
directory on later imports.

### Where the files go

```text
data/tones/123-fender-super-reverb/
  gigbuddy.json
  Clean SM57.nam
  4x12 V30.wav
```

Names come from TONE3000's semantic model names when available. GigBuddy does
not add a model ID or a sequence number to an otherwise meaningful filename.
If the API has no semantic name, it falls back to the download URL basename.

### Partial and repeated imports

The TUI can install selected models from a Pack. A partial Pack is normal: the
metadata can describe more supported models than the files currently on disk.
Importing the same Tone later fills in missing files.

Before moving a downloaded file into the Pack, GigBuddy checks the existing
local record and file checksum. Matching files are reused. New files are
downloaded into a hidden staging directory and then moved into the Pack. A
failed transfer never appears as an installed `.nam` or `.wav` file.

Remote imports also create or update `gigbuddy.json`. GigBuddy keeps user-edited
display fields and unknown fields in an existing GigBuddy manifest. A malformed
or foreign JSON file is left alone; it does not make valid model files
unavailable.

## Add a local Tone Pack

Local import means copying an existing folder into the managed library. There
is no `gigbuddy tone import-local` command in this release.

For a source checkout:

```sh
mkdir -p data/tones/my-pack
cp "/path/to/my-clean-capture.nam" data/tones/my-pack/
cp "/path/to/my-v30-cab.wav" data/tones/my-pack/
```

For the default user install:

```sh
mkdir -p ~/.local/share/gigbuddy/data/tones/my-pack
cp "/path/to/my-clean-capture.nam" \
  ~/.local/share/gigbuddy/data/tones/my-pack/
cp "/path/to/my-v30-cab.wav" \
  ~/.local/share/gigbuddy/data/tones/my-pack/
```

You can copy the whole Pack directory instead of individual files. Keep the
Pack directory itself directly under `data/tones/`:

```text
data/tones/my-pack/
  clean.nam
  v30.wav
```

GigBuddy does not rename, move, or delete the source files you copied. After
copying, open the `LOCAL` view; GigBuddy rescans automatically, and reopening
the view triggers a fresh scan. A directory is shown only when it contains at
least one supported direct child file.

## File rules

| File | GigBuddy type | Scanned? |
|---|---|---|
| `capture.nam` | NAM model | Yes |
| `cabinet.wav` | IR | Yes |
| `capture.NAM` | NAM model | Yes |
| `notes.txt` | None | No |
| `subdir/capture.nam` | None | No, nested files are ignored |
| `.capture.nam` | None | No, hidden files are ignored |
| `.part` or staging files | None | No |
| `gigbuddy.json` | Pack metadata | Read separately |

The extension determines the type. A `.nam` file is shown as a NAM/A2-like
local asset, and a `.wav` file is shown as an IR. GigBuddy checks the path,
extension, and existence; it does not prove that the file contents are a valid
NAM model or a compatible IR before the native engine tries to load it.

## Optional `gigbuddy.json`

A local Pack works without a manifest. Add one when you want stable Pack
identity, a friendly Pack name, author information, tags, or notes for files.

```json
{
  "schema_version": 1,
  "kind": "gigbuddy-tone-pack",
  "pack": {
    "id": "local-my-princeton-pack",
    "name": "My Princeton captures",
    "author": "Me",
    "gear": "amp",
    "tags": ["clean", "edge-of-breakup"],
    "makes": ["Fender"],
    "description": "Captures for my small-combo setup.",
    "source": {
      "kind": "local",
      "url": null,
      "tone_id": null
    }
  },
  "models": [
    {
      "file": "clean.nam",
      "name": "Clean capture",
      "description": "Lower-gain setting",
      "metadata": {"mic": "SM57"}
    },
    {
      "file": "v30.wav",
      "name": "V30 cabinet"
    }
  ],
  "metadata": {}
}
```

LOCAL uses this order when it builds its display:

1. The actual file and its extension.
2. The manifest entry whose `file` matches that direct child filename.
3. Pack metadata from `gigbuddy.json`.
4. TONE3000 metadata when a remote Pack is involved.
5. The directory name and filename as local fallbacks.

`models[].format` is display metadata only. If it disagrees with the extension,
the extension wins. A missing, malformed, or foreign manifest is reported as a
metadata problem, but valid `.nam` and `.wav` files remain usable.

Without a manifest, GigBuddy uses the directory name as the Pack name, the
filename as the model name, `LOCAL` as the author, and a path-derived Pack
identity. A Pack ID in the manifest gives the Pack a named identity, but moving
a Pack does not automatically migrate existing Presets. After moving a Pack,
open LOCAL and save affected Presets again with the newly selected files.

## LOCAL, TONE3000, Packs, Models, Chain, and Presets

- `TONE3000` is the live remote catalog. It needs network access and the user's
  TONE3000 session.
- `LOCAL` is the installed library. It contains remote Packs and user-copied
  local Packs in one view.
- A Pack is the folder-level item. It carries display metadata and contains one
  or more model files.
- A Model is one `.nam` or `.wav` file inside a Pack.
- A Chain is the current ordered list of Slots. It stores file paths and chain
  parameters; it does not copy the Tone metadata.
- A Preset stores a Chain snapshot. For local files it also stores the Pack
  identity and relative filename when available, so loading can resolve the
  current path instead of guessing a remote ID.

Select a local or remote Model from LOCAL and load it into the focused Slot in
the same way. The realtime engine accepts supported managed `.nam` and `.wav`
files regardless of whether their source was TONE3000 or your own disk.

## Editing and removing files

### Adding or replacing a file

Copy the new file into the Pack root and wait for LOCAL to refresh. Replacing a
file with the same name updates its size and checksum in the local index.
If the file is currently in the active Chain, switch it out before replacing
it, then load the replacement into the Slot.

### Removing a local file

Delete or move the file yourself. The next local scan removes it from the
visible Pack/model list. GigBuddy does not delete your other local files as a
side effect of scanning. A removed file's old description may remain in a
manifest as history, but it is not selectable until the file exists again.

### Uninstalling a remote file

Use the uninstall action in LOCAL or the Pack/model detail view. GigBuddy checks
whether the file is in the active Chain and whether a Preset refers to it. A
confirmation is required for a Preset reference. Managed remote files are
moved to `data/.trash/<operation>/` rather than immediately erased; remote
metadata remains so the Tone can be installed again later.

Uninstalling one model does not remove the other models in the Pack. When the
last remote model is removed, the remote Tone remains in the metadata index but
no longer counts as locally installed.

Under normal operation, GigBuddy rejects an uninstall target that resolves
outside the managed `data/tones/` root. Do not have another process replace the
same Pack while an uninstall is running. Changes made by another process at the
same time are unsupported.

## Moving a Pack and Preset behavior

Keep the Pack directory name and its manifest when moving it inside a GigBuddy
home. The manifest keeps the Pack's metadata, but moving the directory is not a
Preset migration. Open LOCAL after the move and save affected Presets again.

If a Pack has no manifest, its identity is derived from its managed path. Moving
it creates a new local Pack identity. A Preset that still points to the old
path will report the file as unavailable until the file is restored or the
Preset is saved again with the new model.

Do not move a Pack outside `data/tones/` and expect it to remain managed. Chain
and Preset validation intentionally rejects paths outside that root.

## Offline and failure behavior

- Local Packs, imported files, and saved Presets can be browsed offline.
- Remote search, creator pages, model details, and downloads need the network
  and a valid TONE3000 session.
- A missing manifest does not block a local file.
- Invalid JSON does not get overwritten automatically.
- An unsupported extension is ignored by the scanner.
- A corrupt or incompatible NAM/WAV file may appear in LOCAL but fail when the
  native engine loads it. Replace it with a valid file and refresh LOCAL.
- GigBuddy does not recursively scan folders, so nested files need to be moved
  to the Pack root.
- Do not have another process rename, replace, or delete the same Pack while a
  remote import or uninstall is running. Changes made by another process at the
  same time are unsupported.
