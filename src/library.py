"""GigBuddy tone library: local SQLite mirror of TONE3000 metadata + `gigbuddy` CLI.

The DB at data/gigbuddy.db is the durable asset external agents query through the
`gigbuddy` CLI (or SQLite directly — schema documented in docs/library-schema.md).
Every search_tones_a2 field is preserved in `tones`; rows imported by id are
assembled from the same sources (tones_counts + users + tone_tags/tags +
tone_makes/makes) so they carry the same 23 fields as search results.

Field coverage note: search_tones_a2 also returns `total_count`, a search-level
aggregation (same value on every row, not a tone attribute) — intentionally dropped.

CLI:
    gigbuddy tone list [--gear amp|cab|amp-cab] [--limit N] [--json] [--query Q]
    gigbuddy tone search <query> [--limit N] [--json]   # TONE3000, then import prompt
    gigbuddy tone show <id> [--json]
    gigbuddy tone import <id>                             # T1: metadata only; download wired in T2
    gigbuddy chain get
    gigbuddy chain set '<json>'                           # write data/live_chain.json (engine hot-swaps)
"""
import argparse
import filecmp
import json
import math
import os
import shutil
import sqlite3
import sys
import threading
import warnings
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import tone3000
import chain_protocol

__version__ = "1.0.2"

ROOT = Path(__file__).resolve().parent.parent

_IR_SUFFIXES = frozenset({".wav", ".wave", ".flac", ".aif", ".aiff"})
_NAM_SUFFIXES = frozenset({".nam", ".aida-x", ".aa-snapshot", ".proteus"})
_IR_GEAR_TOKENS = frozenset({"cab", "space", "ir"})
_PRESET_UPDATED_UNSET = object()


class PresetConflictError(ValueError):
    """Raised when an edit is based on an older version of a Preset row."""


def model_is_ir(model: dict | None, tone: dict | None = None) -> bool:
    """Classify a local/remote model as an IR without trusting one field.

    Older library rows can have a null architecture, while TONE3000 CAB rows
    often expose no architecture at all.  The file/URL suffix is authoritative
    when present; the parent tone is the final fallback for architecture-less
    CAB/IR rows.  WaveNet, SlimmableContainer, and other NAM architectures stay
    on the AMP side.
    """
    model = model if isinstance(model, dict) else {}
    tone = tone if isinstance(tone, dict) else {}
    model_format = str(model.get("format") or "").strip().casefold()
    if model_format:
        return model_format == "ir"

    architecture = str(
        model.get("architecture_version") or model.get("architecture") or ""
    ).strip().casefold()
    if architecture == "ir":
        return True
    canonical_tone_format = str(
        (tone or {}).get("format") or ""
    ).strip().casefold()
    if canonical_tone_format:
        return canonical_tone_format == "ir"
    if str((tone or {}).get("platform") or "").strip().casefold() == "ir":
        return True
    if architecture in {
        "1", "2", "custom", "a1", "a2", "wavenet",
        "wave", "slimmablecontainer",
    }:
        return False
    for key in ("local_path", "name", "model_url", "url"):
        raw = str(model.get(key) or "").strip()
        if not raw:
            continue
        source = raw.split("?", 1)[0].casefold()
        suffix = Path(source).suffix
        if suffix in _IR_SUFFIXES:
            return True
        if suffix in _NAM_SUFFIXES:
            return False
    return str(tone.get("gear") or "").strip().casefold() in _IR_GEAR_TOKENS


def _to_rel_path(path: str) -> str:
    """存储用（REQ-035 portable）：项目根内的路径 → 相对根（data/...）；
    根外路径（自定义外部文件）保持绝对。"""
    p = Path(_to_abs_path(path)).resolve(strict=False)
    try:
        return p.relative_to(ROOT.resolve(strict=False)).as_posix()
    except ValueError:
        return str(p)


def _path_forms(path: str) -> tuple[str, ...]:
    """local_path 查找用的两种存储形式：(相对, 绝对) 去重。

    REQ-035 之后新行存相对（data/tones/...），REQ-035 之前的旧行仍是
    绝对——查找必须两种都试，否则旧库（用户现有 data/gigbuddy.db）里
    链节点点击找不到模型 → detail 被清成空态（REQ-041 根因）。
    """
    rel = _to_rel_path(path)
    abs_ = _to_abs_path(path)
    return (rel, abs_) if rel != abs_ else (rel,)


def _local_path_clause(path: str) -> tuple[str, tuple[str, ...]]:
    """local_path 等值查找子句（兼容新旧两种存储格式）。"""
    forms = _path_forms(path)
    marks = ",".join("?" for _ in forms)
    return f"local_path IN ({marks})", forms


def _to_abs_path(path: str) -> str:
    """读取用：相对路径 → 项目根下绝对；绝对路径原样（旧数据兼容）。

    旧机器/已移动的根外绝对路径：按 data/tones/ 段重基到当前 ROOT
    （文件存在才重基，否则原样返回让调用方报错）。
    """
    p = Path(path)
    if not p.is_absolute():
        return str((ROOT / p).resolve(strict=False))
    try:
        resolved = p.resolve(strict=False)
        if not resolved.is_relative_to(ROOT.resolve(strict=False)):
            idx = str(resolved).find("data/tones/")
            if idx < 0:
                return str(resolved)
            rebased = (ROOT / str(resolved)[idx:]).resolve(strict=False)
            if rebased.exists():
                return str(rebased)
        return str(resolved)
    except OSError:
        return str(p)
DB_FILE = ROOT / "data" / "gigbuddy.db"
CHAIN_FILE = ROOT / "data" / "live_chain.json"  # same path as tui/live.py (engine protocol)
TONES_DIR = ROOT / "data" / "tones"             # same as tui/live.py

_IMPORT_LOCKS: dict[str, threading.Lock] = {}
_IMPORT_LOCKS_GUARD = threading.Lock()
_SCHEMA_READY: set[Path] = set()
_SCHEMA_READY_LOCK = threading.Lock()
_SCHEMA_TABLES = frozenset({"tones", "models", "presets", "settings"})

# All 23 TONE3000 search fields (minus search-level `total_count`) + 2 local columns.
TONE_COLUMNS = [
    "id", "title", "description", "tags", "gear", "makes", "format", "platform",
    "downloads_count", "favorites_count", "a1_models_count", "a2_models_count",
    "custom_models_count", "username", "avatar_url", "user_id", "images",
    "model_name", "created_at", "updated_at", "published_at",
    "has_model_with_url", "irs_count", "models_count",
    "imported_at", "local_dir",
]
JSON_COLUMNS = {"tags", "makes", "images"}  # stored as JSON text

SCHEMA = """
CREATE TABLE IF NOT EXISTS tones (
    id                  INTEGER PRIMARY KEY,
    title               TEXT,
    description         TEXT,
    tags                TEXT,          -- JSON array
    gear                TEXT,
    makes               TEXT,          -- JSON array
    format              TEXT,
    platform            TEXT,          -- deprecated TONE3000 alias
    downloads_count     INTEGER,
    favorites_count     INTEGER,
    a1_models_count     INTEGER,
    a2_models_count     INTEGER,
    custom_models_count INTEGER,
    username            TEXT,
    avatar_url          TEXT,
    user_id             TEXT,
    images              TEXT,          -- JSON array
    model_name          TEXT,
    created_at          TEXT,
    updated_at          TEXT,
    published_at        TEXT,
    has_model_with_url  INTEGER,
    irs_count           INTEGER,
    models_count        INTEGER,
    imported_at         TEXT,
    local_dir           TEXT
);
CREATE TABLE IF NOT EXISTS models (
    id           INTEGER PRIMARY KEY,
    tone_id      INTEGER NOT NULL REFERENCES tones(id),
    model_url    TEXT,
    name         TEXT,          -- TONE3000 models.name（网页/zip 下载文件名，语义命名）
    architecture TEXT,                -- legacy backend token
    architecture_version TEXT,        -- canonical TONE3000 architecture
    local_path   TEXT,
    local_size   INTEGER,
    local_sha256 TEXT
);
CREATE TABLE IF NOT EXISTS presets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT UNIQUE NOT NULL,
    note        TEXT,
    chain_json  TEXT NOT NULL,  -- ordered Slot refs plus gain, master, quality
    created_at  TEXT,
    updated_at  TEXT
);
CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT
);
CREATE INDEX IF NOT EXISTS idx_tones_title   ON tones(title);
CREATE INDEX IF NOT EXISTS idx_tones_gear    ON tones(gear);
CREATE INDEX IF NOT EXISTS idx_tones_dl      ON tones(downloads_count DESC);
CREATE INDEX IF NOT EXISTS idx_models_tone   ON models(tone_id);
"""


# ---- DB access -----------------------------------------------------------

def connect() -> sqlite3.Connection:
    """Open a configured local connection and create the schema if needed.

    These pragmas are connection-scoped unless SQLite documents otherwise, so
    every caller (the TUI, CLI, and external agents) gets the same integrity
    and lock-wait behavior.
    """
    db_path = Path(DB_FILE).resolve(strict=False)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    # WAL is deliberately left as a deployment decision; keep rollback-journal
    # semantics until a real TUI/import workload demonstrates a need for it.
    with _SCHEMA_READY_LOCK:
        schema_ready = db_path in _SCHEMA_READY
        if schema_ready:
            tables = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'")
            }
            # The database may have been removed and recreated at the same
            # path while this process was alive. Keep the fast path, but do
            # not let the process-local cache hide a fresh empty database.
            schema_ready = _SCHEMA_TABLES <= tables
        if not schema_ready:
            conn.executescript(SCHEMA)
            # CREATE TABLE IF NOT EXISTS does not evolve an existing database.
            # Keep additive upgrades on the one-time initialization path.
            tone_columns = {
                row[1] for row in conn.execute(
                    "PRAGMA table_info(tones)").fetchall()
            }
            if "format" not in tone_columns:
                conn.execute("ALTER TABLE tones ADD COLUMN format TEXT")
            model_columns = {
                row[1] for row in conn.execute(
                    "PRAGMA table_info(models)").fetchall()
            }
            migrations = {
                "name": "ALTER TABLE models ADD COLUMN name TEXT",
                "architecture_version": (
                    "ALTER TABLE models ADD COLUMN architecture_version TEXT"),
                "local_size": "ALTER TABLE models ADD COLUMN local_size INTEGER",
                "local_sha256": "ALTER TABLE models ADD COLUMN local_sha256 TEXT",
            }
            for column, statement in migrations.items():
                if column not in model_columns:
                    conn.execute(statement)
            conn.commit()
            _SCHEMA_READY.add(db_path)
    return conn


def database_change_token() -> tuple:
    """Return cheap filesystem signals for UI refresh gating."""
    def stat_token(path: Path) -> tuple:
        try:
            stat = path.stat()
        except OSError:
            return (0, 0, 0)
        return (stat.st_ino, stat.st_size, stat.st_mtime_ns)

    return stat_token(Path(DB_FILE)) + stat_token(Path(TONES_DIR))


def chain_change_token() -> tuple:
    """Return cheap filesystem signals for live-chain refresh gating."""
    try:
        stat = Path(CHAIN_FILE).stat()
    except OSError:
        return (0, 0, 0)
    return (stat.st_ino, stat.st_size, stat.st_mtime_ns)


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for c in JSON_COLUMNS:
        if d.get(c):
            try:
                d[c] = json.loads(d[c])
            except (TypeError, json.JSONDecodeError):
                d[c] = None
    # REQ-035 portable：DB 存相对项目根，读取统一还原为绝对
    for c in ("local_path", "local_dir"):
        if d.get(c):
            d[c] = _to_abs_path(d[c])
    return d


def upsert_tone(conn: sqlite3.Connection, row: dict, *, commit: bool = True) -> None:
    """Insert or update one tone row; every TONE3000 field is stored (JSON cols as text).

    ``commit=False`` lets an importer group a tone and all of its models into
    one transaction while preserving the simple auto-commit behavior for CLI
    and direct callers.
    """
    row = {k: row.get(k) for k in TONE_COLUMNS}
    row["imported_at"] = datetime.now(timezone.utc).isoformat()
    if row.get("local_dir"):
        row["local_dir"] = _to_rel_path(row["local_dir"])
    for c in JSON_COLUMNS:
        if isinstance(row.get(c), (list, dict)):
            row[c] = json.dumps(row[c], ensure_ascii=False)
    cols = list(row.keys())
    qmarks = ",".join("?" * len(cols))
    update = ",".join(f"{c}=excluded.{c}" for c in cols if c != "id")
    sql = (f"INSERT INTO tones ({','.join(cols)}) VALUES ({qmarks}) "
           f"ON CONFLICT(id) DO UPDATE SET {update}")
    conn.execute(sql, [row[c] for c in cols])
    if commit:
        conn.commit()


def upsert_model(conn: sqlite3.Connection, m: dict, *, commit: bool = True) -> None:
    """Insert or update one model row (local_path refreshed on re-import).

    REQ-035 portable：local_path 存相对项目根（data/tones/...）。
    """
    m = dict(m)
    if m.get("local_path"):
        m["local_path"] = _to_rel_path(m["local_path"])
    sql = ("INSERT INTO models (id, tone_id, model_url, name, architecture, "
           "architecture_version, local_path, local_size, local_sha256) "
           "VALUES (:id, :tone_id, :model_url, :name, :architecture, "
           ":architecture_version, :local_path, :local_size, :local_sha256) "
           "ON CONFLICT(id) DO UPDATE SET tone_id=excluded.tone_id, "
           "model_url=excluded.model_url, name=excluded.name, "
           "architecture=excluded.architecture, "
           "architecture_version=excluded.architecture_version, "
           "local_path=excluded.local_path, local_size=excluded.local_size, "
           "local_sha256=excluded.local_sha256")
    # Keep direct/older callers source-compatible while the name column is
    # optional for records created before TONE3000 exposed semantic filenames.
    params = {**m, "name": m.get("name"),
              "architecture_version": m.get("architecture_version"),
              "local_size": m.get("local_size"),
              "local_sha256": m.get("local_sha256")}
    conn.execute(sql, params)
    if commit:
        conn.commit()


# ---- queries -------------------------------------------------------------

def list_tones(gear: str | None = None, limit: int | None = None,
               query: str | None = None, author: str | None = None,
               tag: str | None = None, has_files: bool = False,
               authors: Sequence[str] | None = None,
               tags: Sequence[str] | None = None,
               makes: Sequence[str] | None = None,
               model_ids: Sequence[int] | None = None,
               offset: int = 0, sort_by: str = "downloads") -> list[dict]:
    """List library tones. has_files=True keeps only tones with downloaded files
    (metadata-only rows don't count as "local" — the UI treats them as remote)."""
    author_values = list(authors or ())
    if author and author not in author_values:
        author_values.append(author)
    tag_values = list(tags or ())
    if tag and tag not in tag_values:
        tag_values.append(tag)
    make_values = list(makes or ())
    model_id_values = list(model_ids or ())
    with connect() as conn:
        sql = "SELECT * FROM tones"
        where, args = [], []
        if has_files:
            where.append("EXISTS (SELECT 1 FROM models m "
                         "WHERE m.tone_id = tones.id AND m.local_path IS NOT NULL)")
        if gear:
            gear_token = str(gear).strip().casefold()
            if gear_token == "full-rig":
                gear_token = "amp-cab"
            if gear_token == "ir":
                where.append(
                    "(LOWER(COALESCE(format, platform, '')) = 'ir' OR "
                    "(gear IN ('cab', 'space', 'ir') AND "
                    "COALESCE(format, platform) IS NULL))")
            else:
                where.append("gear = ?")
                args.append(gear_token)
        if author_values:
            marks = ",".join("?" for _ in author_values)
            where.append(f"username IN ({marks})")
            args.extend(author_values)
        if tag_values:
            marks = ",".join("?" for _ in tag_values)
            where.append("EXISTS (SELECT 1 FROM json_each(tones.tags) "
                         f"WHERE json_each.value IN ({marks}))")
            args.extend(tag_values)
        if make_values:
            marks = ",".join("?" for _ in make_values)
            where.append("EXISTS (SELECT 1 FROM json_each(tones.makes) "
                         f"WHERE json_each.value IN ({marks}))")
            args.extend(make_values)
        if model_id_values:
            marks = ",".join("?" for _ in model_id_values)
            where.append("EXISTS (SELECT 1 FROM models m "
                         "WHERE m.tone_id = tones.id "
                         f"AND m.id IN ({marks}))")
            args.extend(model_id_values)
        if query:
            where.append("(title LIKE ? OR username LIKE ? OR description LIKE ?)")
            pat = f"%{query}%"
            args += [pat, pat, pat]
        if where:
            sql += " WHERE " + " AND ".join(where)
        order_by = {
            "title": "LOWER(COALESCE(title, '')) ASC, id ASC",
            "added-desc": "COALESCE(imported_at, '') DESC, id DESC",
            "added-asc": "COALESCE(imported_at, '') ASC, id ASC",
            "downloads": "downloads_count DESC",
        }.get(str(sort_by), "downloads_count DESC")
        sql += f" ORDER BY {order_by}"
        if limit:
            sql += " LIMIT ?"
            args.append(limit)
            if offset:
                sql += " OFFSET ?"
                args.append(max(0, int(offset)))
        elif offset:
            sql += " LIMIT -1 OFFSET ?"
            args.append(max(0, int(offset)))
        return [_row_to_dict(r) for r in conn.execute(sql, args).fetchall()]


def list_local_models(kind: str = "amp", limit: int = 2000) -> list[dict]:
    """Downloaded model files with metadata (for pickers).

    kind="amp" → non-IR models (.nam); kind="ir" → IR wavs. Classification is
    applied before the returned-row limit so architecture-less legacy IR rows
    cannot consume the AMP page and disappear from the picker.
    """
    want_ir = kind == "ir"
    max_rows = max(0, int(limit))
    if max_rows == 0:
        return []
    with connect() as conn:
        rows = conn.execute(
            f"SELECT m.id, m.tone_id, m.model_url, m.name, m.architecture, "
            "m.architecture_version, "
            "m.local_path, t.title, t.username, t.gear, t.description, "
            "t.tags, t.makes, t.format, t.platform "
            "FROM models m JOIN tones t ON t.id = m.tone_id "
            "WHERE m.local_path IS NOT NULL "
            "ORDER BY t.downloads_count DESC, t.id, m.id").fetchall()
    models = []
    for row in rows:
        model = _row_to_dict(row)
        tone = {key: model.get(key) for key in ("gear", "format", "platform")}
        classification_model = {
            key: model.get(key) for key in (
                "architecture", "architecture_version", "local_path",
                "name", "model_url", "url")
        }
        is_ir = model_is_ir(classification_model, tone)
        if is_ir != want_ir:
            continue
        # A1 (WaveNet) 是废弃架构：AMP picker 不提供旧模型，本地库老数据
        # 也只算下载状态，不进入浏览/使用路径。
        if not want_ir and tone3000._is_a1_model(classification_model):
            continue
        models.append(model)
        if len(models) >= max_rows:
            break
    return models


def get_tone(tone_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM tones WHERE id = ?", (tone_id,)).fetchone()
        if not row:
            return None
        d = _row_to_dict(row)
        local_models = []
        for r in conn.execute(
                "SELECT * FROM models WHERE tone_id = ?",
                (tone_id,)).fetchall():
            m = _row_to_dict(r)
            # A1 架构的产品要求：不浏览、不使用（下载路径同样过滤）。
            if not tone3000._is_a1_model(m):
                local_models.append(m)
        d["models"] = local_models
        return d


def _empty_uninstall_plan() -> dict:
    return {"tone_ids": [], "models": [], "bytes": 0,
            "active_paths": [], "preset_names": [], "outside_paths": []}


def _uninstall_plan_for_models(models: list[dict]) -> dict:
    """Shared plan builder: downloaded model rows → file/dependency summary.

    Used by both tone-level and model-level uninstall so the two entry points
    report identical blocks (active chain / unmanaged paths / preset refs).
    """
    tone_ids = sorted({int(m["tone_id"]) for m in models})
    # DB 行可能是相对（REQ-035 后）或绝对（旧行）：统一绝对化再与链比较，
    # 否则新格式库的活动链拦截会漏判（相对路径对不上链上的绝对路径）。
    paths = {_to_abs_path(m["local_path"])
             for m in models if m.get("local_path")}
    live = chain_get()
    live_paths = {
        slot.get("path")
        for slot in live.get("slots", [])
        if isinstance(slot, dict) and slot.get("path")
    }
    active_paths = sorted(path for path in live_paths if path in paths)
    model_ids = {m["id"] for m in models}
    preset_names = []
    for preset in preset_list():
        ch = preset.get("chain")
        if not isinstance(ch, dict):
            continue
        if any(
            isinstance(slot, dict)
            and (
                slot.get("model_id") in model_ids
                or (
                    slot.get("path")
                    and _to_abs_path(slot["path"]) in paths
                )
            )
            for slot in ch.get("slots", [])
        ):
            preset_names.append(preset["name"])
    root = TONES_DIR.resolve()
    outside_paths = []
    total_bytes = 0
    for path in paths:
        p = Path(path)
        try:
            if not p.resolve().is_relative_to(root):
                outside_paths.append(path)
                continue
        except OSError:
            outside_paths.append(path)
            continue
        try:
            total_bytes += p.stat().st_size
        except OSError:
            pass
    return {
        "tone_ids": tone_ids,
        "models": models,
        "bytes": total_bytes,
        "active_paths": active_paths,
        "preset_names": sorted(preset_names),
        "outside_paths": sorted(outside_paths),
    }


def local_uninstall_plan(tone_ids: list[int]) -> dict:
    """Describe files and dependencies affected by uninstalling local tones."""
    ids = sorted({int(tone_id) for tone_id in tone_ids})
    if not ids:
        return _empty_uninstall_plan()
    marks = ",".join("?" for _ in ids)
    with connect() as conn:
        rows = conn.execute(
            f"SELECT id, tone_id, name, local_path FROM models "
            f"WHERE tone_id IN ({marks}) AND local_path IS NOT NULL ORDER BY tone_id, id",
            ids,
        ).fetchall()
    return _uninstall_plan_for_models([dict(row) for row in rows])


def local_uninstall_models_plan(model_ids: list[int]) -> dict:
    """Describe files and dependencies affected by uninstalling local models.

    REQ-038: tone details / pack install 二级菜单按模型粒度卸载——同一
    tone 内只卸选中的模型，其余模型与 tone 元数据保留。
    """
    ids = sorted({int(model_id) for model_id in model_ids})
    if not ids:
        return _empty_uninstall_plan()
    marks = ",".join("?" for _ in ids)
    with connect() as conn:
        rows = conn.execute(
            f"SELECT id, tone_id, name, local_path FROM models "
            f"WHERE id IN ({marks}) AND local_path IS NOT NULL ORDER BY tone_id, id",
            ids,
        ).fetchall()
    return _uninstall_plan_for_models([dict(row) for row in rows])


def _uninstall_files(plan: dict, *, allow_preset_references: bool,
                     replan_ids: list[int], replan) -> dict:
    """Move plan["models"] files to app trash and clear their local DB paths.

    Metadata remains available for dependency reporting and reinstallation.
    Files outside TONES_DIR and files used by the active chain are never moved.
    ``replan(replan_ids)`` re-checks mutable dependencies immediately before
    touching files (the confirmation modal may have been open for a while).
    """
    if plan["active_paths"]:
        raise ValueError("Cannot uninstall files used by the active chain.")
    if plan["outside_paths"]:
        raise ValueError("Cannot uninstall files outside the managed tone library.")
    if plan["preset_names"] and not allow_preset_references:
        raise ValueError("Cannot uninstall files referenced by presets without confirmation.")
    if not plan["models"]:
        return {**plan, "removed": 0, "trash_dir": None}

    op_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8]
    trash_dir = TONES_DIR.parent / ".trash" / op_id
    trash_dir.mkdir(parents=True, exist_ok=False)
    moved: list[tuple[Path, Path]] = []
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "files": [],
        "missing": [],
    }
    try:
        current = replan(replan_ids)
        if current["active_paths"]:
            raise ValueError("Cannot uninstall files used by the active chain.")
        if current["outside_paths"]:
            raise ValueError("Cannot uninstall files outside the managed tone library.")
        if current["preset_names"] and not allow_preset_references:
            raise ValueError(
                "Cannot uninstall files referenced by presets without confirmation.")
        plan = current
        for model in plan["models"]:
            # 相对存储行（REQ-035 后）需绝对化：Path(相对) 会按 CWD 解析
            source = Path(_to_abs_path(model["local_path"]))
            if not source.is_file():
                manifest["missing"].append({
                    "model_id": model["id"], "tone_id": model["tone_id"],
                    "source": str(source),
                })
                continue
            target = trash_dir / f"{model['id']}-{source.name}"
            shutil.move(str(source), str(target))
            moved.append((source, target))
            manifest["files"].append({
                "model_id": model["id"], "tone_id": model["tone_id"],
                "source": str(source), "trash": str(target),
            })
        (trash_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        model_ids = [model["id"] for model in plan["models"]]
        marks = ",".join("?" for _ in model_ids)
        tone_marks = ",".join("?" for _ in plan["tone_ids"])
        with connect() as conn:
            conn.execute(
                f"UPDATE models SET local_path = NULL WHERE id IN ({marks})", model_ids)
            conn.execute(
                f"UPDATE tones SET local_dir = NULL WHERE id IN ({tone_marks}) "
                "AND NOT EXISTS (SELECT 1 FROM models m "
                "WHERE m.tone_id = tones.id AND m.local_path IS NOT NULL)",
                plan["tone_ids"],
            )
            conn.commit()
    except Exception:
        for source, target in reversed(moved):
            source.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                shutil.move(str(target), str(source))
        shutil.rmtree(trash_dir, ignore_errors=True)
        raise
    removed_model_ids = [int(item["model_id"]) for item in manifest["files"]]
    removed_tone_ids = sorted({int(item["tone_id"]) for item in manifest["files"]})
    return {
        **plan,
        "removed": len(moved),
        "removed_model_ids": removed_model_ids,
        "removed_tone_ids": removed_tone_ids,
        "missing": len(manifest["missing"]),
        "trash_dir": str(trash_dir),
    }


def local_uninstall_tones(tone_ids: list[int], *,
                          allow_preset_references: bool = False) -> dict:
    """Move downloaded files to app trash and clear their local DB paths.

    Metadata remains available for dependency reporting and reinstallation.
    Files outside TONES_DIR and files used by the active chain are never moved.
    """
    plan = local_uninstall_plan(tone_ids)
    return _uninstall_files(plan, allow_preset_references=allow_preset_references,
                            replan_ids=plan["tone_ids"],
                            replan=local_uninstall_plan)


def local_uninstall_models(model_ids: list[int], *,
                           allow_preset_references: bool = False) -> dict:
    """Move selected models' files to trash and clear their local DB paths.

    REQ-038: 模型粒度卸载（tone details / pack install 二级菜单的 u 键），
    语义与 tone 级一致：元数据保留、活动链/库外文件拦截、preset 引用需
    确认；tone 全部模型卸空时 local_dir 一并清空。
    """
    plan = local_uninstall_models_plan(model_ids)
    return _uninstall_files(
        plan, allow_preset_references=allow_preset_references,
        replan_ids=[m["id"] for m in plan["models"]],
        replan=local_uninstall_models_plan)


# ---- import ---------------------------------------------------------------

def _import_lock(directory: Path) -> threading.Lock:
    """Serialize imports targeting one tone directory within this process."""
    key = str(directory.resolve(strict=False))
    with _IMPORT_LOCKS_GUARD:
        return _IMPORT_LOCKS.setdefault(key, threading.Lock())


def _seed_import_directory(source: Path, staging: Path) -> None:
    """Copy existing files so staging writes cannot mutate the destination."""
    if not source.exists():
        return
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        target = staging / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        # The downloader may overwrite a zero-byte or stale staged file. A
        # hard link would make that write visible through the destination too,
        # defeating the import rollback boundary.
        shutil.copy2(path, target)


def _restore_replaced_files(replaced: list[tuple[Path, Path]]) -> None:
    for target, backup in reversed(replaced):
        try:
            if backup.is_file():
                os.replace(backup, target)
        except OSError:
            pass


def _publish_import_files(
        paths: list[dict], staging: Path,
        destination: Path) -> tuple[list[dict], list[Path], list[tuple[Path, Path]]]:
    """Publish only this import's staged artifacts and return owned files."""
    published: list[Path] = []
    replaced: list[tuple[Path, Path]] = []
    records: list[dict] = []
    try:
        for source_record in paths:
            record = dict(source_record)
            source = Path(_to_abs_path(record["local_path"]))
            try:
                relative = source.relative_to(staging)
            except ValueError:
                # Keep compatibility with lightweight download test doubles
                # that return a path without actually writing the artifact.
                relative = Path(source.name)
            staged = staging / relative
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if staged.is_file():
                if target.exists():
                    if filecmp.cmp(staged, target, shallow=False):
                        staged.unlink()
                    else:
                        backup = staging / f".rollback-{uuid4().hex}-{target.name}"
                        shutil.copy2(target, backup)
                        os.replace(staged, target)
                        replaced.append((target, backup))
                else:
                    os.replace(staged, target)
                    published.append(target)
            record["local_path"] = str(target)
            records.append(record)
    except Exception:
        _remove_owned_files(published)
        _restore_replaced_files(replaced)
        raise
    return records, published, replaced


def _remove_owned_files(paths: list[Path]) -> None:
    for path in reversed(paths):
        try:
            path.unlink()
        except OSError:
            pass


def _existing_import_models(tone_id: int) -> list[dict]:
    with connect() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT id, model_url, name, local_path, local_size, local_sha256 "
            "FROM models WHERE tone_id = ?", (tone_id,)).fetchall()]

def backfill_tone_usernames(*, quiet: bool = True) -> int:
    """历史数据回填：username 为占位（'tone3000'）或空的 tone 重新联查补真名。

    REQ-023：早期导入链路未存 username（tone_by_id 联查 users 是后来加的）。
    对每个缺失 tone 一次网络请求；返回回填个数。现有新导入不再需要。
    """
    with connect() as conn:
        missing = [r["id"] for r in conn.execute(
            "SELECT id FROM tones "
            "WHERE username IS NULL OR username = '' OR username = 'tone3000'")]
    fixed = 0
    for tone_id in missing:
        row = tone3000.tone_by_id(tone_id)
        if not row or not row.get("username"):
            continue
        with connect() as conn:
            conn.execute(
                "UPDATE tones SET username = ?, avatar_url = ?, user_id = ? "
                "WHERE id = ?",
                (row.get("username"), row.get("avatar_url"),
                 row.get("user_id"), tone_id))
            conn.commit()
        fixed += 1
    if not quiet:
        print(f"Backfilled username for {fixed} tone(s)")
    return fixed


def import_tone(tone_id: int, progress=None, *, quiet: bool = False,
                model_ids: list[int] | None = None) -> dict | None:
    """Full import: fetch metadata → download model files → persist tone + models rows.

    model_ids limits the download to specific models (partial install); the metadata
    row is always complete. Returns the stored tone row (with models) or None if
    TONE3000 has no such tone. Files land in data/tones/<tone_id>-<title-slug>/ and
    keep TONE3000's semantic model name (models.name — same naming as the site's zip
    download). IR tones (gear=cab/space) are recorded with architecture="IR".
    """
    row = tone3000.tone_by_id(tone_id)
    if not row:
        if not quiet:
            print(f"TONE3000 has no tone {tone_id}.")
        return None
    is_ir = model_is_ir({}, row)
    slug = tone3000.slugify(row.get("title"), 40)
    dest = TONES_DIR / f"{tone_id}-{slug}"
    staging = TONES_DIR / f".{dest.name}.import-{uuid4().hex}"
    with _import_lock(dest):
        staging.mkdir(parents=True, exist_ok=False)
        published: list[Path] = []
        replaced: list[tuple[Path, Path]] = []
        try:
            _seed_import_directory(dest, staging)
            existing_models = _existing_import_models(tone_id)
            # A pack install is model-granular.  Fetch the complete remote set
            # so selected A1/WaveNet rows are available as well as A2 and IR.
            paths = tone3000.download(tone_id, staging, tag=slug,
                                      a2_only=False,
                                      ext="wav" if is_ir else None,
                                      return_paths=True, progress=progress, quiet=quiet,
                                      model_ids=model_ids,
                                      existing_records=existing_models)
            dest.mkdir(parents=True, exist_ok=True)
            paths, published, replaced = _publish_import_files(paths, staging, dest)
            row["local_dir"] = _to_rel_path(str(dest))   # REQ-035 portable
            with connect() as conn:
                upsert_tone(conn, row, commit=False)
                for m in paths:
                    upsert_model(conn, {
                        "id": m["id"], "tone_id": tone_id, "model_url": m["model_url"],
                        "name": m.get("name"),
                        "architecture": (
                            (m["model_json"] or {}).get("architecture")
                            or ("IR" if Path(m.get("local_path") or "").suffix.lower()
                                in {".wav", ".wave", ".flac", ".aif", ".aiff"}
                                else None)
                        ),
                        "architecture_version": (
                            (m["model_json"] or {}).get("architecture_version")
                        ),
                        "local_path": m["local_path"],
                        "local_size": m.get("local_size"),
                        "local_sha256": m.get("local_sha256"),
                    }, commit=False)
                conn.commit()
        except Exception:
            _remove_owned_files(published)
            _restore_replaced_files(replaced)
            shutil.rmtree(staging, ignore_errors=True)
            raise
        else:
            shutil.rmtree(staging, ignore_errors=True)
    if not quiet:
        print(f"Imported tone {tone_id}: {len(paths)} model file(s) -> {TONES_DIR}")
    return get_tone(tone_id)


# ---- chain file (canonical v0.2 engine protocol) --------------------------

def chain_get() -> dict:
    """Return the current chain, treating only a missing file as empty.

    Return canonical ``slots[]`` with absolute in-memory paths; legacy
    ``model/ir`` is read-only normalized by the protocol boundary.

    A present but malformed chain must remain an error.  Returning ``{}`` for
    that case makes callers such as ``preset_save`` and uninstall dependency
    checks silently operate on a fake zero-Slot chain.
    """
    try:
        CHAIN_FILE.stat()
    except FileNotFoundError:
        return {}
    return chain_protocol.read_chain_file(CHAIN_FILE, root=ROOT)


def chain_set(cfg: dict) -> None:
    """Write chain config atomically (tmp+rename; engine hot-swaps within 0.3s).

    Validate and atomically write canonical ``slots[]`` through the shared
    protocol boundary.
    """
    chain_protocol.write_chain_file(CHAIN_FILE, cfg, root=ROOT)


# ---- presets (named chain snapshots, logic references into the library) ----

def _model_id_for_path(path: str) -> int | None:
    """Reverse-lookup models.local_path → model id (None if not a library file)."""
    if not path:
        return None
    clause, forms = _local_path_clause(path)
    with connect() as conn:
        row = conn.execute(
            f"SELECT id FROM models WHERE {clause}", forms).fetchone()
        return row["id"] if row else None


def tone_title_for_path(path: str) -> str | None:
    """Tone title owning the model at `path` (None if not a library file).

    Used by the TUI chain panel to show the human tone name above the raw
    filename of the active amp/IR.
    """
    if not path:
        return None
    clause, forms = _local_path_clause(path)
    with connect() as conn:
        row = conn.execute(
            f"SELECT t.title FROM models m JOIN tones t ON t.id = m.tone_id "
            f"WHERE m.{clause}", forms).fetchone()
        return row["title"] if row else None


def _model_path(model_id: int) -> str | None:
    """Resolve a library model id to its current local_path (follows renames).

    DB 存相对 → 返回绝对（REQ-035 portable）。
    """
    with connect() as conn:
        row = conn.execute(
            "SELECT local_path FROM models WHERE id = ?", (model_id,)).fetchone()
        return _to_abs_path(row["local_path"]) if row and row["local_path"] else None


def _installed_model_path(model_id: int) -> str | None:
    """Return a model path only when its database record and file agree."""
    path = _model_path(model_id)
    return path if path and Path(path).is_file() else None


def _setting_get(key: str) -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None


def _setting_set(key: str, value: str | None) -> None:
    with connect() as conn:
        if value is None:
            conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        else:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
        conn.commit()


def preset_current() -> str | None:
    """Return the active preset shared by the TUI and CLI."""
    name = _setting_get("active_preset")
    return name if name and preset_get(name) else None


def preset_set_active(name: str | None) -> None:
    """Set the active preset, rejecting names that do not exist."""
    if name is not None and not preset_get(name):
        raise ValueError(f"Preset '{name}' not found.")
    _setting_set("active_preset", name)


_PRESET_DEFAULTS = {"gain": 1.0, "master": 1.0, "quality": 1.0}
_PRESET_SLOT_GAIN_DEFAULT_DB = 0.0
_PRESET_SLOT_GAIN_MIN_DB = -24.0
_PRESET_SLOT_GAIN_MAX_DB = 24.0


def _preset_number(value: object, name: str, lower: float, upper: float) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Preset {name} must be a finite number")
    if not math.isfinite(value) or not lower <= value <= upper:
        raise ValueError(f"Preset {name} must be between {lower} and {upper}")
    return value


def _preset_model_id(value: object, index: int) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"Preset Slot {index + 1:02d} model_id is invalid")
    return value


def _preset_slot_gains(item: dict, index: int) -> dict[str, int | float]:
    """Validate Slot trims and omit zero defaults from legacy-shaped presets."""
    values: dict[str, int | float] = {}
    for key in ("input_gain_db", "output_gain_db"):
        value = _preset_number(
            item.get(key, _PRESET_SLOT_GAIN_DEFAULT_DB),
            f"Slot {index + 1:02d} {key}",
            _PRESET_SLOT_GAIN_MIN_DB,
            _PRESET_SLOT_GAIN_MAX_DB,
        )
        if value != _PRESET_SLOT_GAIN_DEFAULT_DB:
            values[key] = value
    return values


def _preset_note_value(note: str | None) -> str:
    if note is None:
        return ""
    if not isinstance(note, str):
        raise ValueError("Preset note must be a string")
    return note if note.strip() else ""


def _preset_storage_path(path: object, index: int | None = None) -> str:
    if not isinstance(path, str) or not path.strip():
        label = f" Slot {index + 1:02d}" if index is not None else ""
        raise ValueError(f"Preset{label} path must be a non-empty string")
    # New writes are portable. Legacy absolute paths are accepted in memory and
    # become relative only when an explicit save/overwrite occurs.
    return _to_rel_path(_to_abs_path(path))


def _preset_slot_ref(item: dict, index: int, *, legacy: bool = False) -> dict:
    if not isinstance(item, dict):
        raise ValueError(f"Preset Slot {index + 1:02d} must be an object")
    if not legacy and "path" not in item:
        raise ValueError(f"Preset Slot {index + 1:02d} must contain path")
    model_id = _preset_model_id(item.get("model_id"), index)
    gains = _preset_slot_gains(item, index)
    raw_path = item.get("path")
    if raw_path is None:
        # Bypassed slots keep the model reference with no active file: the
        # engine skips them until the user activates the slot.
        candidate = item.get("candidate")
        if candidate is not None:
            return {"model_id": model_id, "path": None, **gains,
                    "candidate": _preset_storage_path(candidate),
                    "bypass": True}
        if model_id is not None and not legacy and not item.get("bypass"):
            raise ValueError(
                f"Preset Slot {index + 1:02d} cannot have model_id without path")
        return {"model_id": model_id, "path": None, **gains,
                **({"bypass": True} if item.get("bypass") else {})}
    return {"model_id": model_id, **gains,
            "path": _preset_storage_path(raw_path, index)}


def _legacy_slot(raw: dict, index: int, id_key: str,
                 path_keys: tuple[str, ...]) -> dict | None:
    model_id = raw.get(id_key)
    raw_path = next((raw[key] for key in path_keys if key in raw), None)
    if model_id is None and raw_path is None:
        return None
    # Very old records sometimes retained only the logical id. Resolve it for
    # the in-memory view when possible; an unresolved id remains load-invalid.
    if raw_path is None and model_id is not None:
        raw_path = _model_path(_preset_model_id(model_id, index))
    return _preset_slot_ref({"model_id": model_id, "path": raw_path}, index,
                            legacy=True)


def _canonical_preset_chain(raw: object) -> dict:
    """Parse a stored snapshot into the in-memory canonical Preset shape.

    This function is deliberately read-only. It does not update SQLite, so
    legacy rows remain untouched until an explicit save or overwrite.
    """
    if not isinstance(raw, dict):
        raise ValueError("Preset chain must be an object")
    slots: list[dict] = []
    if "slots" in raw:
        legacy_keys = {"model", "ir", "model_id", "model_path",
                       "ir_model_id", "ir_path"}
        if legacy_keys.intersection(raw):
            warnings.warn(
                "Preset contains slots and legacy model/ir fields; slots take precedence",
                RuntimeWarning,
                stacklevel=2,
            )
        raw_slots = raw["slots"]
        if not isinstance(raw_slots, list) or len(raw_slots) > 6:
            raise ValueError("Preset slots must contain between 0 and 6 items")
        slots = [_preset_slot_ref(item, index)
                 for index, item in enumerate(raw_slots)]
    else:
        for id_key, path_keys in (
                ("model_id", ("model_path", "model")),
                ("ir_model_id", ("ir_path", "ir"))):
            slot = _legacy_slot(raw, len(slots), id_key, path_keys)
            if slot is not None:
                slots.append(slot)
    return {
        "slots": slots,
        "gain": _preset_number(raw.get("gain", _PRESET_DEFAULTS["gain"]),
                                "gain", 0, 10),
        "master": _preset_number(raw.get("master", _PRESET_DEFAULTS["master"]),
                                  "master", 0, 10),
        "quality": _preset_number(
            raw.get("quality", _PRESET_DEFAULTS["quality"]), "quality", 0, 1),
    }


def _preset_row(row: sqlite3.Row) -> dict:
    d = dict(row)
    raw_json = d.pop("chain_json")
    if d.get("note") is None:
        d["note"] = ""
    try:
        d["chain"] = _canonical_preset_chain(json.loads(raw_json))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        # Keep an invalid row visible to the caller; loading/resolution will
        # report the error without ever touching the live chain.
        d["chain"] = None
        d["chain_error"] = str(exc)
    return d


def _live_preset_chain(cfg: dict) -> dict:
    """Convert the live path-only chain to a comparable Preset snapshot."""
    if "slots" in cfg:
        raw_slots = cfg.get("slots", [])
    else:
        # Compatibility for callers passing an old in-memory config directly.
        raw_slots = []
        for key in ("model", "ir"):
            if cfg.get(key) is not None:
                raw_slots.append({"path": cfg[key]})
    slots = []
    for index, item in enumerate(raw_slots):
        if not isinstance(item, dict):
            raise ValueError(f"Live Slot {index + 1:02d} is invalid")
        path = item.get("path")
        slots.append({
            "path": None if path is None else _to_abs_path(path),
            **_preset_slot_gains(item, index),
        })
    return {
        "slots": slots,
        "gain": cfg.get("gain", _PRESET_DEFAULTS["gain"]),
        "master": cfg.get("master", _PRESET_DEFAULTS["master"]),
        "quality": cfg.get("quality", _PRESET_DEFAULTS["quality"]),
    }


def _preset_chain_from_live(cfg: dict) -> dict:
    raw_slots = cfg.get("slots")
    if raw_slots is None:
        raw_slots = []
        for key in ("model", "ir"):
            if cfg.get(key) is not None:
                raw_slots.append({"path": cfg[key]})
    if not isinstance(raw_slots, list) or len(raw_slots) > 6:
        raise ValueError("Live chain must contain between 0 and 6 Slots")
    slots = []
    for index, item in enumerate(raw_slots):
        if not isinstance(item, dict):
            raise ValueError(f"Live Slot {index + 1:02d} is invalid")
        path = item.get("path")
        if path is None:
            candidate = item.get("candidate")
            slots.append({
                # 与 active 槽一致：candidate 路径反查 model id（preset 槽
                # 以 Model ID 为身份；外部文件反查不到时为 None）。
                "model_id": (_model_id_for_path(candidate)
                             if candidate else None),
                "path": None,
                **_preset_slot_gains(item, index),
                **({"candidate": _preset_storage_path(candidate),
                    "bypass": True} if candidate else {}),
            })
            continue
        slots.append({
            "model_id": _model_id_for_path(path),
            **_preset_slot_gains(item, index),
            "path": _preset_storage_path(path, index),
        })
    return _canonical_preset_chain({
        "slots": slots,
        "gain": cfg.get("gain", _PRESET_DEFAULTS["gain"]),
        "master": cfg.get("master", _PRESET_DEFAULTS["master"]),
        "quality": cfg.get("quality", _PRESET_DEFAULTS["quality"]),
    })


def preset_save(name: str, note: str | None = None, *, set_active: bool = True) -> dict:
    """Snapshot the current live chain as a canonical ordered Slot Preset."""
    name = name.strip()
    if not name:
        raise ValueError("Preset name cannot be empty.")
    chain = _preset_chain_from_live(chain_get())
    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        existing = conn.execute(
            "SELECT note FROM presets WHERE name = ?", (name,)).fetchone()
        stored_note = (_preset_note_value(existing["note"])
                       if note is None and existing else _preset_note_value(note))
        conn.execute(
            "INSERT INTO presets (name, note, chain_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET note=excluded.note, "
            "chain_json=excluded.chain_json, updated_at=excluded.updated_at",
            (name, stored_note, json.dumps(chain, ensure_ascii=False), now, now))
        if set_active:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES ('active_preset', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (name,),
            )
        conn.commit()
    return preset_get(name)


def preset_get(name: str) -> dict | None:
    """Return one Preset with its chain parsed to the in-memory Slot shape."""
    with connect() as conn:
        row = conn.execute("SELECT * FROM presets WHERE name = ?", (name,)).fetchone()
        return _preset_row(row) if row else None


def preset_get_by_id(preset_id: int) -> dict | None:
    """Return one Preset by its immutable SQLite identity."""
    if isinstance(preset_id, bool) or not isinstance(preset_id, int):
        raise ValueError("preset id must be an integer")
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM presets WHERE id = ?", (preset_id,)
        ).fetchone()
        return _preset_row(row) if row else None


def _preset_id_for_name(name: str) -> int:
    """Resolve a compatibility name once, then let mutations use the id."""
    preset = preset_get(name)
    if not preset:
        raise ValueError(f"Preset '{name}' not found.")
    preset_id = preset.get("id")
    if isinstance(preset_id, bool) or not isinstance(preset_id, int):
        raise ValueError(f"Preset '{name}' has no stable id.")
    return preset_id


def preset_list() -> list[dict]:
    """All Presets, newest first; invalid rows remain inspectable."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM presets ORDER BY updated_at DESC").fetchall()
        return [_preset_row(row) for row in rows]


def preset_delete(name: str) -> bool:
    """Delete one preset; False if it did not exist."""
    with connect() as conn:
        cur = conn.execute("DELETE FROM presets WHERE name = ?", (name,))
        active = conn.execute(
            "SELECT value FROM settings WHERE key='active_preset'").fetchone()
        if cur.rowcount and active and active["value"] == name:
            conn.execute("DELETE FROM settings WHERE key='active_preset'")
        conn.commit()
        return cur.rowcount > 0


def preset_delete_by_id(preset_id: int) -> dict[str, object]:
    """Delete one immutable preset row and report stale targets explicitly.

    The TUI captures the SQLite id when opening confirmation.  Rechecking and
    deleting by that id prevents an external delete/recreate race from
    deleting a different preset that happens to reuse the old name.
    """
    if isinstance(preset_id, bool) or not isinstance(preset_id, int):
        raise ValueError("preset id must be an integer")
    with connect() as conn:
        row = conn.execute(
            "SELECT id, name FROM presets WHERE id = ?", (preset_id,)
        ).fetchone()
        if row is None:
            return {"id": preset_id, "name": None, "deleted": False,
                    "stale": True}
        name = row["name"]
        cur = conn.execute("DELETE FROM presets WHERE id = ?", (preset_id,))
        active = conn.execute(
            "SELECT value FROM settings WHERE key='active_preset'"
        ).fetchone()
        if cur.rowcount and active and active["value"] == name:
            conn.execute("DELETE FROM settings WHERE key='active_preset'")
        conn.commit()
        return {"id": preset_id, "name": name, "deleted": cur.rowcount > 0,
                "stale": cur.rowcount == 0}


def preset_rename(old_name: str, new_name: str) -> dict:
    """Compatibility wrapper; the actual mutation is id-scoped."""
    return preset_rename_by_id(_preset_id_for_name(old_name), new_name)


def preset_rename_by_id(preset_id: int, new_name: str) -> dict:
    """Rename exactly the captured Preset row and keep active state attached."""
    if isinstance(preset_id, bool) or not isinstance(preset_id, int):
        raise ValueError("preset id must be an integer")
    new_name = new_name.strip()
    if not new_name:
        raise ValueError("Preset name cannot be empty.")
    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        row = conn.execute(
            "SELECT name FROM presets WHERE id = ?", (preset_id,)).fetchone()
        if row is None:
            raise ValueError(f"Preset id {preset_id} no longer exists.")
        old_name = row["name"]
        if old_name != new_name and conn.execute(
                "SELECT 1 FROM presets WHERE name = ?", (new_name,)).fetchone():
            raise ValueError(f"Preset '{new_name}' already exists.")
        conn.execute(
            "UPDATE presets SET name = ?, updated_at = ? WHERE id = ?",
            (new_name, now, preset_id),
        )
        conn.execute(
            "UPDATE settings SET value = ? WHERE key = 'active_preset' AND value = ?",
            (new_name, old_name),
        )
        conn.commit()
    return preset_get_by_id(preset_id)


def preset_update_note(name: str, note: str | None) -> dict:
    """Compatibility wrapper; the actual mutation is id-scoped."""
    return preset_update_note_by_id(_preset_id_for_name(name), note)


def preset_update_note_by_id(preset_id: int, note: str | None) -> dict:
    """Replace one captured Preset note without changing its chain snapshot."""
    if isinstance(preset_id, bool) or not isinstance(preset_id, int):
        raise ValueError("preset id must be an integer")
    now = datetime.now(timezone.utc).isoformat()
    note = _preset_note_value(note)
    with connect() as conn:
        cur = conn.execute(
            "UPDATE presets SET note = ?, updated_at = ? WHERE id = ?",
            (note, now, preset_id),
        )
        if not cur.rowcount:
            raise ValueError(f"Preset id {preset_id} no longer exists.")
        conn.commit()
    return preset_get_by_id(preset_id)


def preset_update_draft(name: str, chain: dict, note: str | None = None,
                        *, expected_updated_at: str | None | object =
                        _PRESET_UPDATED_UNSET) -> dict:
    """Compatibility wrapper; the actual mutation is id-scoped."""
    return preset_update_draft_by_id(
        _preset_id_for_name(name), chain, note,
        expected_updated_at=expected_updated_at)


def preset_update_draft_by_id(
        preset_id: int, chain: dict, note: str | None = None, *,
        expected_updated_at: str | None | object = _PRESET_UPDATED_UNSET) -> dict:
    """Persist a Preset Edit draft for exactly one captured row.

    The draft uses the same canonical ``slots[]`` shape as ``preset_save``;
    resolving files is intentionally deferred to ``preset_load`` so an edit
    can retain a visible missing reference for later repair.
    """
    if isinstance(preset_id, bool) or not isinstance(preset_id, int):
        raise ValueError("preset id must be an integer")
    canonical = _canonical_preset_chain(chain)
    stored_note = _preset_note_value(note)
    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        params = [stored_note, json.dumps(canonical, ensure_ascii=False), now,
                  preset_id]
        where = "id = ?"
        if expected_updated_at is not _PRESET_UPDATED_UNSET:
            where += " AND updated_at IS ?"
            params.append(expected_updated_at)
        cur = conn.execute(
            "UPDATE presets SET note = ?, chain_json = ?, updated_at = ? "
            f"WHERE {where}", params)
        if not cur.rowcount:
            if expected_updated_at is not _PRESET_UPDATED_UNSET:
                exists = conn.execute(
                    "SELECT 1 FROM presets WHERE id = ?", (preset_id,)
                ).fetchone()
                if exists:
                    raise PresetConflictError(
                        "preset changed externally; reopen it before saving")
            raise ValueError(f"Preset id {preset_id} no longer exists.")
        conn.commit()
    return preset_get_by_id(preset_id)


def _validate_preset_file(path: str, index: int) -> str:
    candidate = Path(_to_abs_path(path))
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to((ROOT / "data" / "tones").resolve(strict=False))
    except (OSError, ValueError) as exc:
        raise ValueError(
            f"Slot {index + 1:02d} file missing or outside data/tones: {path}") from exc
    if not resolved.is_file():
        raise ValueError(f"Slot {index + 1:02d} file is not regular: {path}")
    if resolved.suffix.lower() not in {".nam", ".wav"}:
        raise ValueError(f"Slot {index + 1:02d} has unsupported file format: {path}")
    return str(resolved)


def _resolve_preset_slot(slot: dict, index: int) -> str | None:
    model_id = slot.get("model_id")
    saved_path = slot.get("path")
    if model_id is None and saved_path is None:
        return None
    candidates: list[str] = []
    if model_id is not None:
        current_path = _model_path(model_id)
        if current_path:
            candidates.append(current_path)
    if saved_path:
        candidates.append(saved_path)
    seen: set[str] = set()
    errors: list[str] = []
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            return _validate_preset_file(candidate, index)
        except ValueError as exc:
            errors.append(str(exc))
    if not candidates:
        raise ValueError(
            f"Slot {index + 1:02d} model file missing (model_id {model_id})")
    raise ValueError(
        f"Slot {index + 1:02d} model file missing or unsupported: "
        + " | ".join(errors))


def _resolved_preset_chain(preset: dict) -> dict:
    if not isinstance(preset.get("chain"), dict):
        error = preset.get("chain_error", "invalid chain")
        raise ValueError(f"Preset '{preset['name']}' is invalid: {error}")
    ch = preset["chain"]
    slots = []
    errors = []
    for index, slot in enumerate(ch["slots"]):
        if slot.get("bypass"):
            # Bypassed slot: keep the model reference, no active file. The
            # recovery candidate (stored path, or resolved from model_id)
            # rides along so the UI can show BYPASS with the model name
            # instead of an empty slot.
            model_id = slot.get("model_id")
            candidate = slot.get("candidate") or (
                _model_path(model_id) if model_id is not None else None)
            slots.append({
                "model_id": model_id,
                "path": None,
                **_preset_slot_gains(slot, index),
                **({"candidate": _preset_storage_path(candidate)}
                   if candidate else {}),
            })
            continue
        try:
            slots.append({
                "model_id": slot.get("model_id"),
                **_preset_slot_gains(slot, index),
                "path": _resolve_preset_slot(slot, index),
            })
        except ValueError as exc:
            errors.append(str(exc))
            slots.append({
                "model_id": slot.get("model_id"),
                **_preset_slot_gains(slot, index),
                "path": None,
            })
    if errors:
        raise ValueError(f"Preset '{preset['name']}' cannot be loaded: "
                         + "; ".join(errors))
    return {"slots": slots,
            "gain": ch.get("gain", _PRESET_DEFAULTS["gain"]),
            "master": ch.get("master", _PRESET_DEFAULTS["master"]),
            "quality": ch.get("quality", _PRESET_DEFAULTS["quality"])}


def preset_resolved_chain(name: str) -> dict:
    """Return a preset chain with library IDs resolved to current local paths."""
    return preset_resolved_chain_by_id(_preset_id_for_name(name))


def preset_resolved_chain_by_id(preset_id: int) -> dict:
    """Resolve the captured Preset row without falling back to its name."""
    p = preset_get_by_id(preset_id)
    if not p:
        raise ValueError(f"Preset id {preset_id} no longer exists.")
    return _resolved_preset_chain(p)


def preset_is_dirty(name: str | None = None, chain: dict | None = None,
                    *, preset_id: int | None = None) -> bool:
    """Whether the live chain differs from a Preset's effective Slot snapshot."""
    if preset_id is not None:
        p = preset_get_by_id(preset_id)
        name = p.get("name") if p else None
    else:
        name = name or preset_current()
        p = preset_get(name) if name else None
    if not p:
        return True
    current = chain_get() if chain is None else chain
    try:
        expected = _resolved_preset_chain(p)
        actual = _live_preset_chain(current)
    except (TypeError, ValueError, KeyError):
        return True
    expected_paths = [slot["path"] for slot in expected["slots"]]
    actual_paths = [slot["path"] for slot in actual["slots"]]
    expected_gains = [
        (slot.get("input_gain_db", _PRESET_SLOT_GAIN_DEFAULT_DB),
         slot.get("output_gain_db", _PRESET_SLOT_GAIN_DEFAULT_DB))
        for slot in expected["slots"]
    ]
    actual_gains = [
        (slot.get("input_gain_db", _PRESET_SLOT_GAIN_DEFAULT_DB),
         slot.get("output_gain_db", _PRESET_SLOT_GAIN_DEFAULT_DB))
        for slot in actual["slots"]
    ]
    return (actual_paths != expected_paths
            or actual_gains != expected_gains
            or actual["gain"] != expected["gain"]
            or actual["master"] != expected["master"]
            or actual["quality"] != expected["quality"])


def preset_load(name: str) -> dict | None:
    """Compatibility wrapper; the actual load is id-scoped."""
    return preset_load_by_id(_preset_id_for_name(name))


def preset_load_by_id(preset_id: int) -> dict | None:
    """Atomically resolve and apply exactly one captured Preset snapshot."""
    if isinstance(preset_id, bool) or not isinstance(preset_id, int):
        raise ValueError("preset id must be an integer")
    p = preset_get_by_id(preset_id)
    if not p:
        raise ValueError(f"Preset id {preset_id} no longer exists.")
    resolved = _resolved_preset_chain(p)
    # Resolve every Slot before constructing or writing the replacement. A
    # missing later Slot must leave the current live file untouched.
    cur = chain_get()
    cfg: dict = {
        "slots": [
            {"path": slot["path"],
             **_preset_slot_gains(slot, index),
             **({"candidate": slot["candidate"]}
                if slot.get("candidate") else {})}
            for index, slot in enumerate(resolved["slots"])
        ],
        "gain": resolved["gain"],
        "master": resolved["master"],
        "quality": resolved["quality"],
        "mute": cur.get("mute", False),
    }
    if isinstance(cur.get("input"), dict):
        cfg["input"] = cur["input"]
    chain_set(cfg)
    preset_set_active(p["name"])
    return chain_get()


# Built-in catalog, resolved from exact local model ids at seed time.
# (name, note, amp_model_id, ir_model_id|None)
# Built-in catalog, resolved from exact local model ids at seed time.
# (name, note, model_ids) — model_ids follow the chain signal order:
# [drive/fuzz pedal, amp, cabinet IR]; a one- or two-slot chain is fine.
# Every slot pair was verified against TONE3000: model ids, gain settings,
# and popularity (downloads/favorites, verified authors preferred).
SEED_CHAINS = [
    ("fender-super-reverb-ts9",
     "Fender Super Reverb 1977 (TONE3000 official, 140K downloads) driven by an "
     "Ibanez TS9 Tube Screamer at Drive 7 / Tone 7 / Level 7 - massive clean "
     "headroom pushed into a tight SRV-style overdrive.",
     [(381611, False), (379720, False)]),
    ("fender-deluxe-reverb-morning-glory",
     "Fender Deluxe Reverb '65 Reissue (69K downloads) with a JHS Morning Glory "
     "v3 at Drive 2 / Tone 8 - transparent blackface sparkle pushed into "
     "edge-of-breakup.",
     [(419178, False), (383453, False)]),
    ("fender-twin-reverb-bd2",
     "Fender '65 Twin Reverb with Celestion G12-65 (17K downloads) and a Boss "
     "Blues Driver BD-2 at Gain 900 / Tone 900 / Volume 1200 - big clean amp "
     "with 60s blues drive.",
     [(379749, False), (381338, False)]),
    ("fender-tweed-deluxe-5e3",
     "1960 Fender Tweed Deluxe 5E3 (official) - narrow-panel 1x12 combo, "
     "cranked vintage compression and overdrive, no pedal needed.",
     [(383783, False)]),
    ("fender-deluxe-reverb-klon",
     "Fender '65 Deluxe Reverb Reissue boosted with a J. Rockett Archer "
     "(Klon-style) - blackface sparkle into singing sustain at the edge of "
     "breakup.",
     [(382101, False)]),
    ("vox-ac30-ef86-ts808",
     "Vox AC30/4 1961 Fawn EF86 (Amalgam Audio, verified, 30K downloads) with "
     "an Ibanez TS808 at Hot / Level 6 / OD 6 / Tone 1 - EF86 chime pushed "
     "into classic British overdrive.",
     [(493737, False), (383682, False)]),
    ("marshall-jcm800-klon",
     "Marshall JCM800 with a Klon Centaur and a Marshall 1960BV cabinet "
     "(SM57 x2) - Klon-driven high-gain rock in one capture.",
     [(381187, False)]),
    ("marshall-jcm800-ds1",
     "Marshall JCM800 2203 Modified (2dor, verified, 38K downloads) with a "
     "Boss DS-1 at Tone 5 / Volume 10 / Gain 7 - the classic 80s "
     "distortion-into-marshall combination.",
     [(381655, False), (567079, False)]),
    ("marshall-plexi-ts9",
     "Marshall JMP-50 Lead 1969 Plexi (Amalgam Audio, verified, 33K downloads) "
     "with an Ibanez TS9 at Drive 0 / Tone 6 / Level 6 used as a clean boost - "
     "golden-era Plexi crunch.",
     [(381614, False), (418470, False)]),
    ("marshall-1959bja-greenback",
     "Marshall 1959BJA high input stage 4 into a Marshall 1960TV Greenback "
     "4x12 (M201) - Green Day / Billie Joe territory.",
     [(670781, False), (656897, False)]),
    ("frusciante-big-muff-major",
     "John Frusciante (RHCP) chain: Electro-Harmonix Op-Amp Big Muff at "
     "Volume 6 / Tone 2 / Sustain 5 into a Marshall Major 200 (1982B cabinet) - "
     "fuzz poured into a 200W Plexi.",
     [(413497, False), (383442, False)]),
    ("hendrix-fuzz-face-plexi",
     "Jimi Hendrix chain: Dunlop Hendrix Fuzz Face (mid cut) into a Marshall "
     "JMP-50 1969 Plexi - the 1969 Woodstock fuzz-into-plexi sound.",
     [(420077, False), (418470, False)]),
    ("page-tonebender-plexi",
     "Led Zeppelin chain: Boss TB-2w Tone Bender at 01-9V into a Marshall "
     "JMP-50 1969 Plexi - Page-style fuzz lead.",
     [(432892, False), (418470, False)]),
    ("ampeg-svt-cl",
     "Ampeg SVT-CL 300W all-tube with a 6x10 cabinet (TONE3000 official, "
     "41K downloads) - harmonically rich classic rock bass.",
     [(379984, False)]),
    ("ampeg-svt-cl-preamp",
     "Ampeg SVT-CL bass preamp capture (Deathblossom Audio, verified) into an "
     "Ampeg SVT 8x10 cabinet (SM57) - studio-grade preamp front end with the "
     "classic all-tube bass cabinet tone.",
     [(382790, False), (80912, False)]),
    ("ampeg-b15-fliptop",
     "1960s Ampeg B-15 Fliptop (TONE3000 official) - Motown vintage bass "
     "combo tone.",
     [(379842, False)]),
    ("gk-rb800",
     "Gallien-Krueger RB800 at G5.0 (Arlington Audio, 7.4K downloads) - "
     "Flea-style transistor bass punch with the pack's Mid Contour tone-switch "
     "IR (bypassed by default - shaping, not a cabinet IR; activate for the "
     "switchable-cab character).",
     [(419198, False), (72421, True)]),
    ("hartke-lh1000",
     "Hartke LH1000 rack bass head into a Gallien-Krueger 4x10 cabinet "
     "(Studio Amp Captures, 11K downloads) - modern rock bass.",
     [(413046, False)]),
    ("darkglass-b7k-ultra",
     "Darkglass Microtubes B7K Ultra (official Darkglass account) into an "
     "Aguilar DB751 and DG412ES cabinet - light to medium modern bass drive.",
     [(413366, False)]),
    ("darkglass-alpha-omega",
     "Darkglass Alpha Omega fuzz channel (official Darkglass account) into an "
     "Aguilar DB751 and DG412ES cabinet (SM7B) - heavy modern metal bass.",
     [(418277, False)]),
]

# The catalog stores model IDs so a preset remains tied to one exact capture.
# Bootstrap still needs the parent tone ID in order to download a missing
# model. These mappings were resolved from TONE3000's public ``models`` table
# and are deliberately kept separate from the canonical preset payload.
STARTER_MODEL_TONES = {
    381611: 36827,   # Ibanez TS9
    379720: 19,      # Fender Super Reverb 1977
    419178: 2805,    # JHS Morning Glory v3
    383453: 51649,   # Fender Deluxe Reverb '65
    379749: 12505,   # Boss BD-2
    381338: 35497,   # Fender '65 Twin + Celestion
    383783: 54580,   # Fender Tweed Deluxe 5E3
    382101: 43185,   # Fender DR + Klon
    493737: 30104,   # Ibanez TS808
    383682: 53601,   # Vox AC30/4 EF86
    381187: 33296,   # Marshall JCM800 + Klon
    381655: 38560,   # Boss DS-1
    567079: 44209,   # Marshall JCM800 2203 Modified
    381614: 36827,   # Ibanez TS9 (clean boost)
    418470: 65578,   # Marshall JMP-50 1969 Plexi
    670781: 76884,   # Marshall 1959BJA
    656897: 75087,   # Marshall 1960TV Greenback cab
    413497: 37818,   # EHX Op-Amp Big Muff
    383442: 51310,   # Marshall Major 200
    420077: 1531,    # Dunlop Hendrix Fuzz Face
    432892: 2316,    # Boss TB-2w Tone Bender
    379984: 28202,   # Ampeg SVT-CL
    382790: 45809,   # Ampeg SVT-CL Preamp
    80912: 1708,     # Ampeg SVT 8x10 cabinet IR (SM57)
    379842: 26828,   # Ampeg B-15 Fliptop
    419198: 2694,    # GK RB800
    72421: 64815,    # GK RB800 Mid Contour cabinet IR
    413046: 5867,    # Hartke LH1000
    413366: 27698,   # Darkglass B7K Ultra
    418277: 27697,   # Darkglass Alpha Omega
}


def preset_group(name: str) -> tuple[str, str]:
    """Derive TUI grouping from the catalog name prefix; no schema fields."""
    parts = name.split("-", 2)
    if len(parts) >= 2 and parts[0] in {"band", "classic"}:
        category = "Band Gear" if parts[0] == "band" else "Classic Pairing"
        instrument = {"guitar": "Guitar", "bass": "Bass"}.get(parts[1])
        if instrument:
            return category, instrument
    # v0.2 catalog: brand-prefixed names (fender/vox/marshall = guitar,
    # ampeg/gk/hartke/darkglass = bass).
    if parts[0] in {"fender", "vox", "marshall"}:
        return "Classic Amplifiers", "Guitar"
    if parts[0] in {"ampeg", "gk", "hartke", "darkglass"}:
        return "Classic Amplifiers", "Bass"
    return "Custom", "Other"


def bootstrap_starter_presets(*, quiet: bool = False,
                              replace: bool = False) -> dict[str, object]:
    """Download the exact starter models required by ``SEED_CHAINS``.

    ``preset_seed`` intentionally remains local-only and idempotent. This
    boundary performs the network work needed by a fresh checkout, then seeds
    only models that are present and valid. A failed download is reported in
    the result instead of creating a preset that cannot be loaded.
    """
    grouped: dict[int, list[int]] = {}
    unresolved: list[int] = []
    for _name, _note, model_ids in SEED_CHAINS:
        for entry in model_ids:
            # SEED_CHAINS 存 (model_id, is_ir) 元组；is_ir 标记仅用于 preset
            # 槽位类型，下载按 model id 归组即可。
            model_id = entry[0] if isinstance(entry, tuple) else entry
            tone_id = STARTER_MODEL_TONES.get(model_id)
            if tone_id is None:
                unresolved.append(model_id)
                continue
            grouped.setdefault(tone_id, [])
            if model_id not in grouped[tone_id]:
                grouped[tone_id].append(model_id)

    imported: list[int] = []
    failed: list[dict[str, object]] = []
    for tone_id, model_ids in grouped.items():
        missing = [
            model_id for model_id in model_ids
            if not _installed_model_path(model_id)
        ]
        if not missing:
            continue
        try:
            imported_tone = import_tone(
                tone_id, quiet=quiet, model_ids=missing)
        except Exception as exc:
            failed.append({"tone_id": tone_id, "model_ids": missing,
                           "error": str(exc)})
            continue
        if imported_tone is None:
            failed.append({"tone_id": tone_id, "model_ids": missing,
                           "error": "tone not found"})
            continue
        unresolved_files = [
            model_id for model_id in missing
            if not _installed_model_path(model_id)
        ]
        if unresolved_files:
            failed.append({"tone_id": tone_id, "model_ids": unresolved_files,
                           "error": "requested model was not downloaded"})
        else:
            imported.append(tone_id)

    written = preset_seed(replace=replace, quiet=quiet)
    result = {
        "imported_tone_ids": imported,
        "presets": written,
        "failed": failed,
        "unresolved_model_ids": sorted(set(unresolved)),
    }
    if not quiet:
        print(f"Starter assets: {len(imported)} tone(s), "
              f"{written}/{len(SEED_CHAINS)} preset(s)")
        if failed:
            print(f"Starter downloads failed: {len(failed)}")
        if unresolved:
            print(f"Unknown starter model IDs: {sorted(set(unresolved))}")
    return result


def _first_local_model(tone_id: int, ir: bool = False) -> int | None:
    """First downloaded model id of a tone (amp: non-IR first; ir: IR wavs)."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT m.id, m.tone_id, m.model_url, m.name, m.architecture, "
            "m.architecture_version, m.local_path, t.gear, t.format, t.platform "
            "FROM models m JOIN tones t ON t.id = m.tone_id "
            "WHERE m.tone_id = ? AND m.local_path IS NOT NULL ORDER BY m.id",
            (tone_id,)).fetchall()
    for row in rows:
        model = _row_to_dict(row)
        tone = {key: model.get(key) for key in ("gear", "format", "platform")}
        classification_model = {
            key: model.get(key) for key in (
                "architecture", "architecture_version", "local_path",
                "name", "model_url", "url")
        }
        if model_is_ir(classification_model, tone) == ir:
            # A1 (WaveNet) 是废弃架构：amp 默认选中不落到旧模型上。
            if not ir and tone3000._is_a1_model(classification_model):
                continue
            return model["id"]
    return None


def local_models_by_tone(path: str) -> list[dict] | None:
    """Local model files of the tone folder containing `path`.

    Used by the TUI chain panel to step through sibling models of the active
    amp/IR (same tone folder, e.g. the 30 knob settings of a JCM800 capture).
    Returns None when the path is not a library model; otherwise a list of
    model rows (id, name, architecture, local_path) ordered by model id.
    """
    clause, forms = _local_path_clause(path)
    with connect() as conn:
        row = conn.execute(
            f"SELECT tone_id FROM models WHERE {clause}", forms).fetchone()
        if not row:
            return None
        rows = conn.execute(
            "SELECT id, tone_id, name, architecture, architecture_version, local_path "
            "FROM models "
            "WHERE tone_id = ? AND local_path IS NOT NULL ORDER BY id",
            (row["tone_id"],)).fetchall()
        # 链面板的兄弟模型步进同样排除 A1（WaveNet）旧文件。
        return [m for m in (_row_to_dict(r) for r in rows)
                if not tone3000._is_a1_model(m)]


def downloaded_model_ids_by_tone() -> dict[int, set[int]]:
    """tone_id → set of locally downloaded model ids (one SQL pass).

    A1 (WaveNet) 模型是产品过滤掉的废弃架构：本地老数据不计入已下载集合，
    否则下载状态比对与 Files 计数会把它们当成有效文件。
    """
    with connect() as conn:
        rows = conn.execute(
            "SELECT tone_id, id FROM models WHERE local_path IS NOT NULL "
            "AND LOWER(COALESCE(architecture_version, architecture, '')) "
            "NOT IN ('1', 'a1', 'wave', 'wavenet')").fetchall()
    out: dict[int, set[int]] = {}
    for r in rows:
        out.setdefault(r["tone_id"], set()).add(r["id"])
    return out


def mark_download_state(hits: list[dict]) -> list[dict]:
    """Tag search hits with their local download state by comparing model ids.

    Each hit gains `download_state` in {"all", "partial", "none"} and
    `downloaded` (count of locally downloaded models). Tones with no local
    models are "none" without any API call; tones present locally are compared
    id-by-id against TONE3000's current model list (amp/pedal → A1/A2/custom
    model files, cab/space → IR files), queried in parallel.
    """
    local = downloaded_model_ids_by_tone()
    todos = [t for t in hits if t.get("id") in local]
    done: dict[int, tuple[str, int]] = {}
    if todos:
        from concurrent.futures import ThreadPoolExecutor

        def remote_ids(t: dict) -> tuple[int, str, set[int]]:
            try:
                ms = tone3000.models(t["id"], a2_only=False)
            except tone3000.AuthenticationRequiredError:
                raise
            except Exception:
                return t["id"], "partial", set()
            if model_is_ir({}, t):
                ids = {m["id"] for m in ms if model_is_ir(m, t)}
                if not ids:
                    # Older API fixtures may omit both architecture and a
                    # model URL; an IR tone still defines the whole set as IR.
                    ids = {m["id"] for m in ms}
            else:
                # Amp/pedal packs may contain WaveNet (A1) and
                # SlimmableContainer (A2). A1 is product-filtered (never
                # downloaded), so it must not count toward the remote set:
                # including it would make every local install look partial.
                ids = {m["id"] for m in ms
                       if not model_is_ir(m, t)
                       and not tone3000._is_a1_model(m)}
            return t["id"], "all" if ids else "partial", ids

        with ThreadPoolExecutor(max_workers=6) as ex:
            results = list(ex.map(remote_ids, todos))
        for tid, fallback, remote in results:
            local_ids = local[tid]
            if remote:
                state = "all" if local_ids >= remote else "partial"
            else:
                state = fallback
            done[tid] = (state, len(local_ids))
    for t in hits:
        tid = t.get("id")
        if tid in done:
            t["download_state"], t["downloaded"] = done[tid]
        else:
            t["download_state"], t["downloaded"] = "none", 0
    return hits


def preset_seed(*, replace: bool = False, quiet: bool = False) -> int:
    """Create the built-in recommendation presets from the local library.

    Chains whose model slots are not all in the library are skipped with a
    warning (never fails the rest). Returns the number of presets written.
    """
    if replace:
        with connect() as conn:
            conn.execute("DELETE FROM presets")
            conn.execute("DELETE FROM settings WHERE key = 'active_preset'")
            conn.commit()
    made = 0
    for name, note, model_slots in SEED_CHAINS:
        slots = []
        for model_id, bypass in model_slots:
            if bypass:
                # 可选/塑形类 IR：默认 bypass（path=None，引擎跳过），
                # 模型引用保留在 preset 里，激活时按 model_id 解析。
                slots.append({"model_id": model_id, "path": None,
                              "bypass": True})
                continue
            model_path = _installed_model_path(model_id)
            if not model_path:
                if not quiet:
                    print(f"[preset seed] skipped {name}: model {model_id} is not available locally")
                break
            slots.append({"model_id": model_id,
                          "path": _preset_storage_path(model_path)})
        else:
            chain = {
                "slots": slots,
                "gain": 1.0, "master": 1.0, "quality": 1.0,
            }
            chain = _canonical_preset_chain(chain)
            now = datetime.now(timezone.utc).isoformat()
            with connect() as conn:
                conn.execute(
                    "INSERT INTO presets (name, note, chain_json, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(name) DO UPDATE SET note=excluded.note, "
                    "chain_json=excluded.chain_json, updated_at=excluded.updated_at",
                    (name, note, json.dumps(chain, ensure_ascii=False), now, now))
                conn.commit()
            made += 1
            if not quiet:
                print(f"[preset seed] {name}: {len(model_slots)} slot(s)")
    return made


# ---- CLI -----------------------------------------------------------------

def _preset_slot_summary(chain: dict | None) -> str:
    if not isinstance(chain, dict):
        return "invalid"
    labels = []
    for slot in chain.get("slots", []):
        if not isinstance(slot, dict):
            labels.append("INVALID")
            continue
        if slot.get("path") is None:
            labels.append("NONE" if slot.get("model_id") is None
                          else f"#{slot['model_id']}")
        elif slot.get("model_id") is not None:
            labels.append(f"#{slot['model_id']}")
        else:
            labels.append(Path(slot["path"]).name)
    return " > ".join(labels) if labels else "(empty)"

def _fmt_table(tones: list[dict]) -> str:
    rows = [
        f"{t['id']:>8} | dl={t.get('downloads_count', 0):>6} fav={t.get('favorites_count', 0):>5} "
        f"a2={t.get('a2_models_count', 0):>3} | {t.get('gear', '?'):<8} | "
        f"{(t.get('format') or t.get('platform') or '?'):<10} | "
        f"{(t.get('title') or '')[:52]:<52} | @{t.get('username') or '?'}"
        for t in tones
    ]
    return "\n".join(rows)


def _fmt_show(t: dict) -> str:
    lines = [
        f"id           {t['id']}",
        f"title        {t.get('title')}",
        f"gear         {t.get('gear')}",
        f"format       {t.get('format') or t.get('platform')}",
        f"username     {t.get('username')}",
        f"downloads    {t.get('downloads_count')}   favorites {t.get('favorites_count')}",
        f"counts       a1={t.get('a1_models_count')} a2={t.get('a2_models_count')} "
        f"custom={t.get('custom_models_count')} irs={t.get('irs_count')} models={t.get('models_count')}",
        f"model_name   {t.get('model_name')}",
        f"tags         {', '.join(t.get('tags') or [])}",
        f"makes        {', '.join(t.get('makes') or [])}",
        f"created      {t.get('created_at')}",
        f"updated      {t.get('updated_at')}",
        f"published    {t.get('published_at')}",
        f"imported_at  {t.get('imported_at')}",
        f"local_dir    {t.get('local_dir')}",
    ]
    if t.get("description"):
        lines.append(f"description  {t['description']}")
    ms = t.get("models") or []
    if ms:
        lines.append("models:")
        for m in ms:
            arch = (m.get("architecture_version") or m.get("architecture") or "?")
            lines.append(f"  {m['id']} [{arch}] {m.get('local_path') or m.get('model_url')}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="gigbuddy", description="GigBuddy tone library CLI")
    p.add_argument("--version", action="version",
                   version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    pt = sub.add_parser("tone", help="tone library operations")
    tsub = pt.add_subparsers(dest="tone_cmd", required=True)
    tsub.add_parser("login", help="sign in to TONE3000 in the system browser")
    pl = tsub.add_parser("list", help="list imported tones")
    pl.add_argument("--gear", choices=["amp", "amp-cab", "pedal", "outboard",
                                        "cab", "space", "experimental", "full-rig", "ir"],
                    help="filter by gear type")
    pl.add_argument("--limit", type=int, help="max rows")
    pl.add_argument("--query", help="text search (title/username/description)")
    pl.add_argument("--json", action="store_true", help="JSON output")
    ps = tsub.add_parser("search", help="search TONE3000 (import with: gigbuddy tone import <id>)")
    ps.add_argument("query")
    ps.add_argument("--gear", choices=["amp", "amp-cab", "pedal", "outboard",
                                        "cab", "space", "experimental", "full-rig", "ir"],
                    help="filter by gear type")
    ps.add_argument("--author", action="append", help="filter by author username (repeatable)")
    ps.add_argument("--tag", action="append", help="filter by tag name (repeatable)")
    ps.add_argument("--limit", type=int, default=10)
    ps.add_argument("--json", action="store_true")
    psh = tsub.add_parser("show", help="show full metadata for one imported tone")
    psh.add_argument("id", type=int)
    psh.add_argument("--json", action="store_true")
    pi = tsub.add_parser("import", help="download models + persist metadata to the library")
    pi.add_argument("id", type=int)

    pc = sub.add_parser("chain", help="chain file (data/live_chain.json) operations")
    csub = pc.add_subparsers(dest="chain_cmd", required=True)
    csub.add_parser("get", help="print current chain config")
    cset = csub.add_parser("set", help="write chain config")
    cset.add_argument("json", help="JSON object, e.g. '{\"master\": 0.4}'")

    pp = sub.add_parser("preset", help="named chain presets (save/load/manage)")
    psub = pp.add_subparsers(dest="preset_cmd", required=True)
    plist = psub.add_parser("list", help="list presets")
    plist.add_argument("--json", action="store_true", help="JSON output")
    psave = psub.add_parser("save", help="snapshot the current live chain as a preset")
    psave.add_argument("name", help="preset name (unique)")
    psave.add_argument("--note", help="optional description")
    pload = psub.add_parser("load", help="apply a preset to the live chain (engine hot-swap)")
    pload.add_argument("name")
    pshow = psub.add_parser("show", help="show one preset (resolved paths)")
    pshow.add_argument("name")
    pshow.add_argument("--json", action="store_true")
    psub.add_parser("current", help="show the active preset and dirty state")
    prename = psub.add_parser("rename", help="rename a preset")
    prename.add_argument("old_name")
    prename.add_argument("new_name")
    pnote = psub.add_parser("note", help="set a preset note (omit NOTE to clear)")
    pnote.add_argument("name")
    pnote.add_argument("note", nargs="?")
    pd = psub.add_parser("delete", help="delete a preset")
    pd.add_argument("name")
    pseed = psub.add_parser(
        "seed",
        help="download starter models and create the built-in preset catalog",
    )
    pseed.add_argument("--replace", action="store_true",
                       help="delete every existing preset before creating the catalog")
    pseed.add_argument("--local-only", action="store_true",
                       help="create presets only from already-downloaded models")
    psub.add_parser(
        "bootstrap",
        help="download starter models and create the built-in preset catalog",
    )

    args = p.parse_args(argv)

    if args.cmd == "tone":
        if args.tone_cmd == "login":
            try:
                tone3000.login()
            except (tone3000.AuthenticationRequiredError,
                    tone3000.Tone3000HTTPError, OSError, ValueError) as exc:
                print(f"TONE3000 login failed: {exc}", file=sys.stderr)
                return 1
            print("TONE3000 login complete.")
        elif args.tone_cmd == "list":
            tones = list_tones(args.gear, args.limit, args.query)
            print(json.dumps(tones, ensure_ascii=False, indent=2) if args.json else
                  (_fmt_table(tones) if tones else "No imported tones yet — `gigbuddy tone search <q>` first."))
        elif args.tone_cmd == "search":
            gear = args.gear
            if gear == "full-rig":
                gear_values = ["amp-cab"]
            elif gear == "ir":
                gear_values = ["cab", "space"]
            else:
                gear_values = [gear] if gear else None
            hits = tone3000.search(args.query, page_size=args.limit,
                                   gear_filters=gear_values,
                                   usernames=args.author, tag_names=args.tag)
            print(json.dumps(hits, ensure_ascii=False, indent=2) if args.json else _fmt_table(hits))
            if hits and not args.json:
                print("\nImport one with: gigbuddy tone import <id>")
        elif args.tone_cmd == "show":
            t = get_tone(args.id)
            if not t:
                print(f"Tone {args.id} not in local library — import it first (gigbuddy tone import {args.id}).")
                return 1
            print(json.dumps(t, ensure_ascii=False, indent=2) if args.json else _fmt_show(t))
        elif args.tone_cmd == "import":
            t = import_tone(args.id)
            if not t:
                return 1
            print(_fmt_show(t))
    elif args.cmd == "chain":
        if args.chain_cmd == "get":
            try:
                cfg = chain_get()
            except (OSError, UnicodeError,
                    chain_protocol.ChainProtocolError) as exc:
                print(f"Cannot read chain: {exc}", file=sys.stderr)
                return 1
            print(json.dumps(cfg, ensure_ascii=False, indent=2))
        elif args.chain_cmd == "set":
            try:
                cfg = json.loads(args.json)
            except json.JSONDecodeError:
                print(f"Not valid JSON: {args.json}")
                return 1
            if not isinstance(cfg, dict):
                print("chain set expects a JSON object.")
                return 1
            chain_set(cfg)
            print(f"Chain written to {CHAIN_FILE} (engine hot-swaps within ~0.3s).")
    elif args.cmd == "preset":
        if args.preset_cmd == "list":
            presets = preset_list()
            if args.json:
                print(json.dumps(presets, ensure_ascii=False, indent=2))
            elif presets:
                active = preset_current()
                for p in presets:
                    ch = p["chain"] or {}
                    marker = ">" if p["name"] == active else " "
                    dirty = " *" if p["name"] == active and preset_is_dirty(active) else ""
                    print(f"{marker} {p['name']:<28}{dirty} | slots "
                          f"{_preset_slot_summary(ch)} | "
                          f"gain {ch.get('gain')} master {ch.get('master')} "
                          f"quality {ch.get('quality', 1.0)}"
                          + (f" | {p.get('note')}" if p.get("note") else ""))
            else:
                print("No presets yet — `gigbuddy preset save <name>` or `gigbuddy preset seed`.")
        elif args.preset_cmd == "save":
            p = preset_save(args.name, args.note)
            print(f"Preset '{args.name}' saved"
                  + (f" ({p.get('note')})" if p.get("note") else "")
                  + f" — load with: gigbuddy preset load {args.name}")
        elif args.preset_cmd == "load":
            try:
                cfg = preset_load(args.name)
            except ValueError as e:
                print(e)
                return 1
            print(f"Preset '{args.name}' applied -> {CHAIN_FILE} (engine hot-swaps within ~0.3s).")
            print(json.dumps(cfg, ensure_ascii=False, indent=2))
        elif args.preset_cmd == "show":
            p = preset_get(args.name)
            if not p:
                print(f"Preset '{args.name}' not found.")
                return 1
            ch = p["chain"] or {}
            if args.json:
                print(json.dumps(p, ensure_ascii=False, indent=2))
                return 0
            print(f"name       {p['name']}")
            if p.get("note"):
                print(f"note       {p['note']}")
            print(f"created    {p.get('created_at')}")
            print(f"updated    {p.get('updated_at')}")
            print(f"slots      {_preset_slot_summary(ch)}")
            print(f"gain       {ch.get('gain')}   master {ch.get('master')}   "
                  f"quality {ch.get('quality', 1.0)}")
        elif args.preset_cmd == "current":
            name = preset_current()
            if not name:
                print("No active preset.")
            else:
                print(f"{name}{' *' if preset_is_dirty(name) else ''}")
        elif args.preset_cmd == "rename":
            try:
                preset_rename(args.old_name, args.new_name)
            except ValueError as e:
                print(e)
                return 1
            print(f"Preset '{args.old_name}' renamed to '{args.new_name}'.")
        elif args.preset_cmd == "note":
            try:
                preset_update_note(args.name, args.note)
            except ValueError as e:
                print(e)
                return 1
            print(f"Preset '{args.name}' note {'updated' if args.note else 'cleared'}.")
        elif args.preset_cmd == "delete":
            if preset_delete(args.name):
                print(f"Preset '{args.name}' deleted.")
            else:
                print(f"Preset '{args.name}' not found.")
                return 1
        elif args.preset_cmd == "seed":
            if args.local_only:
                n = preset_seed(replace=args.replace)
                print(f"Seeded {n}/{len(SEED_CHAINS)} presets.")
            else:
                result = bootstrap_starter_presets(replace=args.replace)
                failed = result["failed"]
                print(f"Seeded {result['presets']}/{len(SEED_CHAINS)} presets.")
                if failed:
                    print("Some starter assets could not be downloaded; "
                          "run `gigbuddy preset seed` again to retry.",
                          file=sys.stderr)
                    return 1
        elif args.preset_cmd == "bootstrap":
            result = bootstrap_starter_presets()
            failed = result["failed"]
            if failed:
                print("Some starter assets could not be downloaded; "
                      "run `gigbuddy preset bootstrap` again to retry.",
                      file=sys.stderr)
                return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
