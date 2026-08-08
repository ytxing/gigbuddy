---
name: gigbuddy
description: Use when a user asks to find, compare, explain, import, or connect guitar or bass tones from TONE3000 through GigBuddy. Search TONE3000 tone packs, read their gear/format/tags/makes/description, inspect exact model names and files, choose engine-compatible NAM or IR variants, or build/apply a tone chain or preset. Triggers: "find me an XX tone", "build me an XX chain", "explain this TONE3000 description", "what models are in this tone", "install or audition a tone".
---

# GigBuddy: find and explain TONE3000 tones

Use this skill to turn a natural-language tone request into an evidence-backed
TONE3000 search, a concrete Tone/Model choice, and, when requested, a local
GigBuddy chain or preset.

## Runtime boundary

Run commands from the repository root, the directory containing bin/, src/,
and data/. If bin/gigbuddy is not present, locate the repository before
running a command. Do not guess a different project root or a local file path.

The stable agent-facing interface is:

~~~bash
bin/gigbuddy tone list [--gear amp|amp-cab|pedal|outboard|cab|space|experimental] [--limit N] [--query Q] [--json]
bin/gigbuddy tone search <query> [--gear GEAR] [--author USER] [--tag TAG] [--limit N] [--json]
bin/gigbuddy tone show <tone-id> [--json]
bin/gigbuddy tone import <tone-id>
bin/gigbuddy chain get
bin/gigbuddy chain set '<json-object>'
bin/gigbuddy preset list [--json]
bin/gigbuddy preset save <name> [--note "..."]
bin/gigbuddy preset load <name>
bin/gigbuddy preset show <name> [--json]
bin/gigbuddy preset current | preset rename <old> <new> | preset note <name> [text]
bin/gigbuddy preset delete <name> | preset bootstrap
~~~

Interpret the commands as follows:

- tone list searches the imported local SQLite library. Its --query covers
  local title, creator, and description text.
- tone search performs a live TONE3000 search through the repository's public
  API adapter. Use --json whenever the response must be analyzed.
- tone show reads one imported Tone and is the authoritative local view of
  the full description and its models[] rows.
- tone import downloads the selected Tone's supported model files and stores
  the complete Tone/Model metadata. It is a write and a network download; do it
  only for selected candidates or when the user asks to install/apply a Tone.
- The CLI exposes --author and --tag, and --gear (including ir); there is no
  --make or architecture filter. Search a Make name as normal query text
  instead of inventing a flag.
- preset bootstrap downloads the starter model catalog and seeds the built-in
  presets; preset save snapshots the CURRENT live chain (never builds a chain
  from arguments).

The remote search JSON may contain a Tone description and a summary
model_name, but it does not contain the complete models[] list. To explain
every concrete Model or choose an exact filename, import the selected Tone and
then run tone show <tone-id> --json.

## Mandatory retrieval workflow

### 1. Parse the request

Extract these constraints before searching:

1. Instrument and use: guitar/bass, amp, full rig, cab IR, or a descriptive
   sound target.
2. Identity terms: artist, song, amplifier, speaker, microphone, creator, or
   manufacturer.
3. Sound terms: clean, edge of breakup, crunch, high gain, bright, dark,
   scooped, tight, ambient, room, direct, and so on.
4. Capture constraints: a named mic, amp setting, room/close mic, DI/no cab,
   full rig, or a CPU/quality preference.
5. The requested action: explain only, find candidates, download/import,
   apply to the live chain, render, or save a preset.

Keep the first query to two or four high-signal terms. Preserve the user's
exact device or artist spelling when it is likely to occur in a title, Make,
tag, or description.

### 2. Check local assets first

Avoid downloading a duplicate when the requested Tone is already imported:

~~~bash
bin/gigbuddy tone list --query "<device or artist terms>" --limit 20 --json
~~~

For a local candidate, inspect it before selecting a file:

~~~bash
bin/gigbuddy tone show <tone-id> --json
~~~

Treat a local metadata row with no usable models[].local_path as metadata,
not as an installed playable asset. Confirm that the selected path exists
before writing a chain.

### 3. Search TONE3000 in focused passes

Start with the most specific search and record the real id from the current
output:

~~~bash
bin/gigbuddy tone search "<artist or device> <sound>" --gear amp --limit 10 --json
~~~

Use these search passes when the first result set is weak:

- Search the exact amplifier/artist and sound, such as two rock clean or
  plexi edge of breakup.
- Search a capture trait, such as fender clean sm57, jcm800 v30, or
  mayer direct.
- Search the style alone, such as blues crunch or modern metal tight, and
  use the returned title, description, tags, makes, and format to rank
  results.
- Search a creator or tag with the supported filters:
  --author <username> and --tag <tag>.

Use --gear deliberately:

| gear | Treat it as | Default chain treatment |
|---|---|---|
| amp | standalone amp capture | candidate for the .nam model slot |
| amp-cab | full rig / amp and cab represented together | do not add a separate cab unless the exact Model/description says it is DI or has no cab |
| cab | cabinet or impulse-response pack | candidate for the .wav IR slot |
| pedal | pedal-category Tone | metadata candidate; the current chain has no pedal slot |
| outboard | outboard-category Tone | metadata candidate; do not place it in an amp slot by category alone |
| space | space/reverb or legacy IR category | inspect format and Model extension before using it |
| experimental | site experimental category | inspect format and engine compatibility explicitly |

When a cab is needed, make a separate search:

~~~bash
bin/gigbuddy tone search "<speaker or cab terms>" --gear cab --limit 10 --json
~~~

A Tone with format=ir is the canonical IR signal, while old cab/space rows may
carry the same meaning.

If no result is suitable, change one keyword or remove one constraint and
search again. Do not choose a poor match just to produce an ID. Keep a short
record of each candidate's query and real Tone ID so later imports cannot use
an ID from memory.

### 4. Rank candidates with evidence

Rank in this order:

1. Engine compatibility and requested role: nam for amp, ir/.wav for a cab,
   and no unsupported format in the live chain.
2. Exact title, Make, artist, device, and capture terms.
3. Exact Model variant after inspecting models[].name.
4. Creator description, tags, and stated settings/microphones/room/direct
   path.
5. Downloads and favorites as tie-breaker signals only. They measure
   popularity, not verified sound quality or suitability.

For a comparison, return two or three candidates instead of hiding the
trade-off. State why the winner matches and which constraint remains unknown.

## Tone versus Model: read the right object

Treat a TONE3000 Tone as a pack/entry and a Model as one concrete file inside
that pack. Never use their IDs interchangeably.

| Field | Meaning | Selection rule |
|---|---|---|
| id | Tone pack ID | Pass it to tone import and tone show; take it only from current search/show output |
| title | Tone pack title | Match the broad device or creator intent |
| description | Creator-written prose | Quote/translate it, then separate stated facts from inference; it is not a measurement report |
| tags | Creator/site labels | Use as search and ranking evidence, not as a guarantee |
| makes | Associated device, brand, or capture labels | Cross-check against title/description; do not assume every Make is a separate chain node |
| gear | TONE3000 category | Determine the intended role, then confirm the actual file format |
| format | Canonical Tone format | nam, ir, aida-x, aa-snapshot, or proteus; the current engine accepts only nam and ir |
| platform | Deprecated format alias | Prefer format when both are present |
| model_name | One summary/first Model metadata name | Use as a hint only; it is not the full Model list |
| models_count / a1_models_count / a2_models_count / irs_count | Counts | Use to understand pack coverage, never as a substitute for models[] |
| models[].id | Concrete Model technical ID | Use for identity and reporting; it is not the download filename |
| models[].name | Concrete semantic Model name | This is the website/zip-facing name; preserve spaces, punctuation, and capture settings exactly |
| models[].local_path | Downloaded local file | Use this exact path after checking it exists; do not reconstruct it from the Tone ID |
| models[].architecture_version | NAM architecture enum | 1 = A1, 2 = A2, custom = custom; null is expected for IR/non-NAM Models |
| models[].architecture | Legacy compatibility label | WaveNet, SlimmableContainer, or Custom are aliases; IR is a compatibility marker, not a NAM architecture |
| models[].size | TONE3000 size label | Treat it as metadata unless the current command path exposes a size selector |

Use this command for the exact list:

~~~bash
bin/gigbuddy tone show <tone-id> --json
~~~

Read models[].name, not only the Tone-level model_name and not the storage
URL. The importer preserves the semantic name as the local filename, adding
or correcting the engine suffix when necessary: NAM becomes .nam, and IR
becomes .wav. A numeric Model ID is never a replacement for that filename.

For example, a name such as
Fender Super Reverb: EQ Flat, Volume 3, sm57 communicates a base amp, an EQ
and volume capture condition, and a microphone-labelled variant. It does not by
itself prove mic placement, frequency response, or that the Model is better.
Use the Tone description to confirm what those tokens mean, and choose the
exact models[].name that matches the user's requested variant.

## Interpret description without overclaiming

For each selected Tone, read the description in four passes:

1. Quote the relevant original text so the user can verify the source.
2. Translate jargon into plain language in the user's language.
3. Extract creator-stated facts: source gear, controls, speaker, microphone,
   room/close-mic setup, intended use, and named artists.
4. Mark the result as stated, metadata, inferred, or unknown.

Use this compact glossary when it appears in a description:

- clean: little or no intentional amp distortion.
- edge of breakup: near the point where picking or volume produces mild
  overdrive.
- crunch / high gain: progressively stronger drive; confirm with settings
  and the exact Model name.
- full rig: the capture is intended to include the amp and cab path.
- DI, direct, or no cab: the amp path omits the cab; a separate compatible
  IR may be needed.
- room / room only: room microphone or room component, not automatically a
  separate reverb effect.
- close mic: near-field microphone capture; SM57, R121, U87, and C414 are
  microphone labels, not additional effects.
- headroom: how far the signal can rise before obvious clipping or breakup;
  it is a creator description, not a measured number here.
- flat EQ, volume 3, or similar tokens: captured control settings; do not
  treat them as live editable parameters unless the engine exposes them.

Do not convert marketing language such as mix ready, massive, or perfect into
a guaranteed result. Do not infer a mic, speaker, cab, or effect that the
description and exact Model rows do not support. If the description is empty,
say that the selection is based on title, tags, Makes, Model names, and format
only.

## Architecture filtering (A1/WaveNet)

GigBuddy filters the deprecated A1 (WaveNet) architecture everywhere:
downloads, browsing, and display. A Tone whose models are all A1 will import
zero playable models. Never try to force an A1 file into the chain; when a
search returns only A1 candidates, re-search with different terms instead.

## Import, select, and connect

After ranking, import only the chosen Tone or the small set the user approved:

~~~bash
bin/gigbuddy tone import <tone-id>
bin/gigbuddy tone show <tone-id> --json
~~~

The current CLI import downloads all supported Models for the Tone. Do not
promise one-file-only installation through this command. After import, select
one object from models[] by matching the user's requested variant and these
hard checks:

- An amp slot needs a real existing .nam path and an engine-compatible
  format=nam/NAM architecture (A2 or custom; never A1).
- A cab slot needs a real existing .wav path and IR semantics.
- A non-NAM format such as aida-x, aa-snapshot, or proteus may remain
  searchable metadata, but tone import will reject it for the current engine.
- A null architecture on an IR is expected; never label it A1 or A2.

Use the exact paths from tone show --json; do not invent a slug or replace a
semantic filename with model-<id>.

Chains use the ordered slots[] format (v0.2 slot chain, up to six slots).
For a standalone amp:

~~~bash
bin/gigbuddy chain set '{"slots": [{"path": "<exact .nam path>"}], "gain": 1.0, "master": 1.0, "quality": 1.0}'
~~~

For an amp plus a separate IR:

~~~bash
bin/gigbuddy chain set '{"slots": [{"path": "<exact .nam path>"}, {"path": "<exact .wav path>"}], "gain": 1.0, "master": 1.0, "quality": 1.0}'
~~~

chain set writes the complete live configuration and the engine hot-swaps
from data/live_chain.json. Include all slot paths and parameter keys; every
tone id must come from real search output and every file path from real
import output. quality controls the A2 sub-model size; it does not choose a
different TONE3000 Model.

For an amp-cab Tone, prefer the exact full-rig .nam variant and omit a
separate IR unless the description and Model name explicitly identify a DI/no-
cab variant or the user asks for external cab experimentation. Do not assume
that the amp-cab category alone proves every file in the pack contains a cab.

When the user asks for offline audio, first verify the selected files and then
use the repository render path documented in docs/chain-schema.md. Report
whether the result was actually rendered; finding metadata is not the same as
audibly validating a Tone.

## Presets and batch generation

Treat preset save as a snapshot of the current live chain, not as a command
that constructs a chain from a Tone ID. For a single preset:

1. Search and record real Tone IDs.
2. Import and inspect the selected Tone description and Model rows.
3. Set the full chain with exact local paths.
4. Save with a note: bin/gigbuddy preset save "<name>" --note "<character>".

For a batch request (a series of styles, e.g. clean / crunch / metal / blues /
jazz), use a two-phase loop — first search and import ALL styles, then set and
snapshot each chain:

1. Parse intent into a style list; for each style record the amp search terms,
   expected character (clean / overdrive / high-gain), and whether a cab IR is
   needed. List the styles for user confirmation before generating.
2. Phase A — search and import every style (record real ids, read description
   for the note material via tone show).
3. Phase B — per style: chain set (full slots[] + params) then
   preset save "<style>-<character>" --note "<analysis>".
4. Naming: lowercase ASCII hyphenated <style>-<character> (e.g. blues-clean-70s,
   metal-modern-gain, jazz-clean-neck). Same name overwrites — check preset
   list first; on conflict rename or ask.
5. Verify: preset list (all + active marker), preset show <name> --json
   (slots/model_id/paths/gain/master/note), preset load <name> (spot-check the
   engine hot-swap).

After batch saving, remember that the last saved preset becomes active; use
preset load <name> when a different one should drive the engine. Maintain
presets through the CLI: preset note (edit note without touching the chain),
preset rename, preset delete.

## Reporting

Return a compact evidence report:

- search query and source (local library or TONE3000 live);
- Tone ID, title, creator, gear, format, tags/Makes, and relevant counts;
- original description plus a plain-language interpretation;
- exact selected models[].id, models[].name, architecture/size, and local
  path;
- why the variant matches, what is inferred, and what remains unknown;
- commands/actions actually performed: search, import, chain set, render, or
  preset save.

## Non-negotiable safeguards

- Take every Tone ID from a current tone search or tone show result. Take
  every Model ID and filename from the current models[] output.
- Never pass a Model ID to tone import; that command expects a Tone ID.
- Never claim that a search, import, chain update, render, or preset succeeded
  without checking its command result.
- Do not treat downloads/favorites as objective sound quality.
- Do not treat description, tags, or a Model name as measured audio evidence.
- Do not put unsupported formats, A1 files, or unverified paths into the live
  chain.
- If the network fails, report the failure, retry with a smaller focused query
  when reasonable, and do not fabricate candidates from memory.
- If tone show says a Tone is not in the local library, import it first or
  state that exact Model names cannot yet be verified locally.
