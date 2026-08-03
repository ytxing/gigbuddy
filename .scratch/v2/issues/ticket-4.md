# T4: gigbuddy skill → gigbuddy CLI (external agent path)

Blocked by: T1, T2
Blocks: T5

## Context
SPEC-v2 §4. The Claude Code skill (.claude/skills/gigbuddy/SKILL.md) drives the library through the
stable CLI (`gigbuddy tone ...`, `gigbuddy chain ...`) instead of ad-hoc python calls; chain
changes via live_chain.json (file handoff to engine, unchanged).

## Task
- Update SKILL.md workflow: search → `gigbuddy tone search`; import → `gigbuddy tone import`;
  local lookup → `gigbuddy tone list`; chain edits → `gigbuddy chain set`.
- Keep anti-invention rules (tone ids from real search output).

## Acceptance
- [x] Skill steps use only gigbuddy CLI + file protocol (no direct src/ imports)
      (offline render keeps src/render.py — it is not a library access path)
- [x] A dry-run conversation flow works: search → import → chain set → engine hot-swap
      (verified end to end: real search output → idempotent import → show with local
      files → chain set/get round-trip; engine protocol unchanged from v1)
