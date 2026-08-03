# GigBuddy v2 tickets

Spec: docs/SPEC-v2.md

Work blockers-first. Each ticket is agent-ready: context, task, acceptance, edges.

```
T1 library.py (SQLite + CLI)      — no deps
T2 download/import → DB           — blocked by T1
T3 TUI rework (library browser)   — blocked by T1, T2
T4 gigbuddy skill → gigbuddy CLI  — blocked by T1, T2
T5 README + tests wrap-up         — blocked by T3, T4
```
