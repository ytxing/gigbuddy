# Changelog

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
