# Changelog

## [Unreleased]

## [1.2.4] - 2026-08-16

Full release note: [GigBuddy v1.2.4](docs/releases/v1.2.4.md).

### Added

- Seeded and imported Presets now carry each NAM Slot's recommended output
  calibration (`output_gain_db = -18 - metadata.loudness` dB, matching the
  realtime engine), so fresh installs are calibrated out of the box.
- The 20 built-in Presets are now distributed as repository JSON. GigBuddy
  registers them without an initialization download, prepares missing models
  in the background, reports per-Preset availability, and keeps built-in rows
  read-only. See the [built-in Preset guide](docs/built-in-presets.md).

### Fixed

- Interrupted Preset writes now recover from the committed SQLite row on the
  next explicit catalog refresh, while uncertain JSON versions are preserved in
  quarantine.
- Duplicate untracked user Presets converge into quarantine instead of warning
  forever, and built-in Presets remain visible when stale model metadata is
  unsupported.
- Bundled registration and editable Preset reconciliation now share one file
  lock and one catalog snapshot, preventing a late repository refresh from
  racing a user save or quarantining against different source generations.
- Editable Preset file checks now include device and ctime identity, so an
  equal-size edit that restores the previous mtime is still detected before
  publication; legacy tracking tokens migrate on the next catalog refresh.
- Failed upgrades preserve preexisting runtime and data directories even when
  `git fetch` fails, and existing custom data links are reused automatically.
- Every post-clone installer failure now enters the same one-shot rollback path;
  an existing non-GigBuddy directory is rejected before rollback is armed and
  is never mistaken for a partial clone.
- Non-interactive installs can proceed only with explicit `--skip-presets`, and
  command installation replaces only physically internal links or the exact
  generated wrapper; external links and user scripts that merely mention the
  install path are preserved.
- Failed CLI preparation lists each unavailable built-in Preset, and the
  uninstaller now provides `-h` / `--help` usage.
- Failed built-in model preparation now leaves rows retryable as `UNAVAILABLE`,
  and delayed Preset highlights are ignored while the TUI screen is closing.
- Late preparation workers are scoped to the exact chain semantics and Tone
  sources they started with, so a same-model Preset replacement cannot inherit
  stale `PREPARING` or `UNAVAILABLE` state, including after a plain catalog
  refresh.
- Failed engine upgrades restore the previous Eigen tree and report incomplete
  recovery instead of silently accepting it.
- The uninstaller resolves relative command links and generated-wrapper parent
  aliases before deciding ownership, while preserving external, malformed, and
  custom commands.

## [1.2.3] - 2026-08-14

Full release note: [GigBuddy v1.2.3](docs/releases/v1.2.3.md).

### Fixed

- The one-line installer now prints the complete output, command, and exit
  status when a command fails.

## [1.2.2] - 2026-08-14

Full release note: [GigBuddy v1.2.2](docs/releases/v1.2.2.md).

### Added

- Added three calibrated shareable Preset examples and a maintenance script
  for applying NAM loudness calibration.

### Fixed

- Decimal-valued TONE3000 model labels such as `G5.0` and `0.25in` are no
  longer mistaken for filenames.

## [1.2.1] - 2026-08-14

Full release note: [GigBuddy v1.2.1](docs/releases/v1.2.1.md).

### Fixed

- The user installer now upgrades old tag-based checkouts even when their
  configured fetch refspec names a retired release tag.

## [1.2.0] - 2026-08-14

Full release note: [GigBuddy v1.2.0](docs/releases/v1.2.0.md).

This release covers the work since `v1.1.4`.

### Added

- Remote and local Tone Pack management with a shared LOCAL workflow.
- Optional `gigbuddy.json` Pack manifests and direct-file local Pack scanning.
- A customer-facing [tone file management guide](docs/tone-file-management.md).

### Changed

- TONE3000 search now follows the official default `trending` + A2 view for an
  empty query and keeps one official ranked stream while filtering to A2/IR.
- Remote imports preserve semantic model filenames, support partial installs,
  reuse matching files, and write portable Pack metadata.

### Fixed

- A1, Custom, and other unsupported model architectures no longer enter the
  visible catalog, download path, Chain, or Preset resolution.
- Search sorting, pagination, duplicate-page handling, stale local files, and
  uninstall boundary checks were corrected.

## 1.1.4 - 2026-08-11

### Added

- Added a panel-by-panel guide covering Library, Tone Chain, Tone Detail,
  Presets, and Audio / Level views.
- Added a unified CPU readout for the TUI and its managed realtime engine.

### Fixed

- Replacing a model resets its Slot input and output trims to neutral defaults,
  while bypass and restore keep the existing model's trims.
- Reduced TUI polling and redraw work by reusing one level/runtime snapshot,
  checking the chain only when its file token changes, throttling catalog
  refreshes, and skipping unchanged visual updates.
- Prevented stale level and playback telemetry from surviving a stopped managed
  engine.

## 1.1.3 - 2026-08-10

### Fixed

- The animated installer banner starts only after the install-location and
  TONE3000 login decisions are complete, so it cannot overwrite either prompt.

## 1.1.2 - 2026-08-10

### Fixed

- The user-level installer keeps the TONE3000 login prompt visible instead of
  clearing it when the animated banner resumes.
- The login gate now runs immediately after the checkout is ready, before uv,
  the Python environment, dependency installation, or starter downloads.
- A missing interactive terminal no longer masquerades as an intentional
  `n` response: installation stops unless `--skip-presets` is explicitly
  provided.
- The checkout installer reports the same explicit `--skip-presets` guidance
  when starter assets cannot be installed without an interactive login.

## 1.1.1 - 2026-08-10

### Fixed

- Installers check for a local TONE3000 session before downloading starter
  models, offer an interactive `Y/n` login prompt, and print the OAuth URL
  while opening the normal system-browser flow.
- Declining login, or installing without a terminal, now skips authenticated
  starter models while allowing public dry-input setup to continue.
- Both uninstall paths remove the persisted TONE3000 session even when
  `--keep-data` preserves downloaded tones and local data.

## 1.1.0 - 2026-08-10

### Added

- Per-Slot input and output trim controls with a bounded range of -24 to +24
  dB.
- Engine-backed NAM output calibration, including the raw recommendation and a
  visible indication when the safe range clamps it.
- Persistent Slot trims across preset save/load, dirty-state checks, undo, and
  redo.
- TONE3000 OAuth 2.0 + PKCE login for the TUI and CLI, local token refresh, and
  explicit logout. The header and remote library views expose the current
  authentication state.

### Fixed

- Managed Slot edits and calibration no longer block the Textual event loop or
  lose the latest user value during a held control.
- Pack model refreshes preserve the current table focus and selection while
  remote metadata arrives.
- Dry-input Play, Stop, and Loop actions remain responsive when the engine is
  starting or unavailable.
- Remote tone rows render before optional download-state enrichment, and
  creator loading is bounded to one official page at a time.
- Library and pack status columns keep stable widths and active markers remain
  aligned with the selected tone or model.
- Clicks stay within their owning pane instead of bubbling into unrelated
  panels or the application-level handler.
- Current dry-input markers, safe Slot I/O editing, double-click reset, and
  stale async view responses now keep their state consistent.

### Release and documentation

- OAuth callback page now carries visible TONE3000 attribution.
- The public documentation describes per-user authentication, request pacing,
  bounded remote loading, creator attribution, and the current TONE3000 API
  terms.
- The bundled agent skill was reviewed against the current CLI and now covers
  login/logout, exact Tone-versus-Model selection, engine compatibility, and
  the same per-user and bounded-download API boundary.
- The remote installer now defaults to the `v1.1.0` tag.
- The local uninstall script removes the PortAudio `.local` build prefix along
  with the other generated runtime artifacts and clears the persisted local
  TONE3000 session on a full uninstall.

### Compatibility

- macOS remains the supported native-audio platform.
- Existing presets and legacy flat `model` / `ir` preset fields remain
  readable and normalize to the current ordered Slot representation.
- The TONE3000 API policy and endpoint scope are maintained by TONE3000; the
  official API documentation and terms remain authoritative.
