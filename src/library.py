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
import json
import shutil
import sqlite3
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import tone3000

__version__ = "0.1.0a3"

ROOT = Path(__file__).resolve().parent.parent


def _to_rel_path(path: str) -> str:
    """存储用（REQ-035 portable）：项目根内的路径 → 相对根（data/...）；
    根外路径（自定义外部文件）保持绝对。"""
    p = Path(path)
    try:
        return str(p.resolve().relative_to(ROOT.resolve()))
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
        return str(ROOT / p)
    try:
        if not p.resolve().is_relative_to(ROOT.resolve()):
            idx = str(p).index("data/tones/")
            rebased = ROOT / str(p)[idx:]
            if rebased.exists():
                return str(rebased)
    except ValueError:
        pass
    return str(p)
DB_FILE = ROOT / "data" / "gigbuddy.db"
CHAIN_FILE = ROOT / "data" / "live_chain.json"  # same path as tui/live.py (engine protocol)
TONES_DIR = ROOT / "data" / "tones"             # same as tui/live.py

# All 23 TONE3000 search fields (minus search-level `total_count`) + 2 local columns.
TONE_COLUMNS = [
    "id", "title", "description", "tags", "gear", "makes", "platform",
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
    platform            TEXT,
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
    architecture TEXT,
    local_path   TEXT
);
CREATE TABLE IF NOT EXISTS presets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT UNIQUE NOT NULL,
    note        TEXT,
    chain_json  TEXT NOT NULL,  -- model/IR refs plus gain, master, quality
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
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_FILE, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    # WAL is deliberately left as a deployment decision; keep rollback-journal
    # semantics until a real TUI/import workload demonstrates a need for it.
    conn.executescript(SCHEMA)
    # CREATE TABLE IF NOT EXISTS does not evolve an existing database. The
    # semantic filename column was added after the first schema, so make that
    # one additive upgrade safe for users with an older local library.
    model_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(models)").fetchall()
    }
    if "name" not in model_columns:
        conn.execute("ALTER TABLE models ADD COLUMN name TEXT")
        conn.commit()
    return conn


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
    sql = ("INSERT INTO models (id, tone_id, model_url, name, architecture, local_path) "
           "VALUES (:id, :tone_id, :model_url, :name, :architecture, :local_path) "
           "ON CONFLICT(id) DO UPDATE SET tone_id=excluded.tone_id, "
           "model_url=excluded.model_url, name=excluded.name, "
           "architecture=excluded.architecture, local_path=excluded.local_path")
    # Keep direct/older callers source-compatible while the name column is
    # optional for records created before TONE3000 exposed semantic filenames.
    params = {**m, "name": m.get("name")}
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
               offset: int = 0) -> list[dict]:
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
            where.append("gear = ?")
            args.append(gear)
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
        sql += " ORDER BY downloads_count DESC"
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

    kind="amp" → non-IR models (.nam); kind="ir" → IR wavs. The kind filter is
    applied in SQL *before* the LIMIT — filtering afterwards would silently
    drop whole low-download tones (e.g. the Dookie Mod pack) from the picker.
    """
    arch = "m.architecture = 'IR'" if kind == "ir" else "m.architecture IS NOT 'IR'"
    with connect() as conn:
        rows = conn.execute(
            f"SELECT m.id, m.tone_id, m.model_url, m.name, m.architecture, "
            "m.local_path, t.title, t.username, t.gear, t.description, "
            "t.tags, t.makes "
            "FROM models m JOIN tones t ON t.id = m.tone_id "
            f"WHERE m.local_path IS NOT NULL AND {arch} "
            "ORDER BY t.downloads_count DESC, t.id, m.id LIMIT ?",
            (limit,)).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_tone(tone_id: int) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM tones WHERE id = ?", (tone_id,)).fetchone()
        if not row:
            return None
        d = _row_to_dict(row)
        d["models"] = [
            _row_to_dict(r)
            for r in conn.execute("SELECT * FROM models WHERE tone_id = ?", (tone_id,)).fetchall()
        ]
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
    active_paths = sorted(
        path for path in (live.get("model"), live.get("ir")) if path in paths)
    model_ids = {m["id"] for m in models}
    preset_names = []
    for preset in preset_list():
        ch = preset["chain"]
        if ch.get("model_id") in model_ids or ch.get("ir_model_id") in model_ids:
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
    return {
        **plan,
        "removed": len(moved),
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
    download). IR tones (gear=cab) are recorded with architecture="IR".
    """
    row = tone3000.tone_by_id(tone_id)
    if not row:
        if not quiet:
            print(f"TONE3000 has no tone {tone_id}.")
        return None
    is_ir = (row.get("gear") == "cab")
    slug = tone3000.slugify(row.get("title"), 40)
    dest = TONES_DIR / f"{tone_id}-{slug}"
    paths = tone3000.download(tone_id, dest, tag=slug,
                              a2_only=not is_ir, ext="wav" if is_ir else None,
                              return_paths=True, progress=progress, quiet=quiet,
                              model_ids=model_ids)
    row["local_dir"] = _to_rel_path(str(dest))   # REQ-035 portable
    with connect() as conn:
        upsert_tone(conn, row, commit=False)
        for m in paths:
            upsert_model(conn, {
                "id": m["id"], "tone_id": tone_id, "model_url": m["model_url"],
                "name": m.get("name"),
                "architecture": (m["model_json"] or {}).get("architecture") or "IR",
                "local_path": m["local_path"],
            }, commit=False)
        conn.commit()
    if not quiet:
        print(f"Imported tone {tone_id}: {len(paths)} model file(s) -> {TONES_DIR}")
    return get_tone(tone_id)


# ---- chain file (engine protocol, unchanged from tui/live.py) ------------

def chain_get() -> dict:
    """Current chain config ({} if missing/broken).

    REQ-035 portable：model/ir 相对路径读取时还原为项目根下绝对。
    """
    try:
        cfg = json.loads(CHAIN_FILE.read_text())
    except Exception:
        return {}
    for key in ("model", "ir"):
        if cfg.get(key):
            cfg[key] = _to_abs_path(cfg[key])
    return cfg


def chain_set(cfg: dict) -> None:
    """Write chain config atomically (tmp+rename; engine hot-swaps within 0.3s).

    REQ-035 portable：model/ir 路径写入时转相对项目根。
    """
    cfg = dict(cfg)
    for key in ("model", "ir"):
        if cfg.get(key):
            cfg[key] = _to_rel_path(cfg[key])
    tmp = CHAIN_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
    tmp.rename(CHAIN_FILE)


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


def preset_save(name: str, note: str | None = None, *, set_active: bool = True) -> dict:
    """Snapshot the current live chain as a named preset.

    model/ir paths that belong to the library are stored as logic references
    (model_id) so renames/migrations never break a preset; arbitrary paths are
    kept verbatim. Overwrites a preset with the same name.
    """
    name = name.strip()
    if not name:
        raise ValueError("Preset name cannot be empty.")
    cfg = chain_get()
    chain = {
        "model_id": _model_id_for_path(cfg.get("model")),
        "model_path": cfg.get("model"),
        "ir_model_id": _model_id_for_path(cfg.get("ir")),
        "ir_path": cfg.get("ir"),
        "gain": cfg.get("gain", 1.0),
        "master": cfg.get("master", 1.0),
        "quality": cfg.get("quality", 1.0),
    }
    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        conn.execute(
            "INSERT INTO presets (name, note, chain_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET note=COALESCE(excluded.note, presets.note), "
            "chain_json=excluded.chain_json, updated_at=excluded.updated_at",
            (name, note, json.dumps(chain, ensure_ascii=False), now, now))
        if set_active:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES ('active_preset', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (name,),
            )
        conn.commit()
    return preset_get(name)


def preset_get(name: str) -> dict | None:
    """One preset row (chain_json parsed); None if not found."""
    with connect() as conn:
        row = conn.execute("SELECT * FROM presets WHERE name = ?", (name,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["chain"] = json.loads(d.pop("chain_json"))
        return d


def preset_list() -> list[dict]:
    """All presets, newest first."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM presets ORDER BY updated_at DESC").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["chain"] = json.loads(d.pop("chain_json"))
            out.append(d)
        return out


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


def preset_rename(old_name: str, new_name: str) -> dict:
    """Rename a preset and keep the active pointer attached to it."""
    new_name = new_name.strip()
    if not new_name:
        raise ValueError("Preset name cannot be empty.")
    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        if not conn.execute("SELECT 1 FROM presets WHERE name = ?", (old_name,)).fetchone():
            raise ValueError(f"Preset '{old_name}' not found.")
        if old_name != new_name and conn.execute(
                "SELECT 1 FROM presets WHERE name = ?", (new_name,)).fetchone():
            raise ValueError(f"Preset '{new_name}' already exists.")
        conn.execute(
            "UPDATE presets SET name = ?, updated_at = ? WHERE name = ?",
            (new_name, now, old_name),
        )
        conn.execute(
            "UPDATE settings SET value = ? WHERE key = 'active_preset' AND value = ?",
            (new_name, old_name),
        )
        conn.commit()
    return preset_get(new_name)


def preset_update_note(name: str, note: str | None) -> dict:
    """Replace a preset note without changing its chain snapshot."""
    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        cur = conn.execute(
            "UPDATE presets SET note = ?, updated_at = ? WHERE name = ?",
            (note or None, now, name),
        )
        if not cur.rowcount:
            raise ValueError(f"Preset '{name}' not found.")
        conn.commit()
    return preset_get(name)


def _resolved_preset_chain(preset: dict) -> dict:
    ch = preset["chain"]
    return {
        "model": _model_path(ch["model_id"]) if ch.get("model_id")
        else (_to_abs_path(ch["model_path"]) if ch.get("model_path") else None),
        "ir": _model_path(ch["ir_model_id"]) if ch.get("ir_model_id")
        else (_to_abs_path(ch["ir_path"]) if ch.get("ir_path") else None),
        "gain": ch.get("gain", 1.0),
        "master": ch.get("master", 1.0),
        "quality": ch.get("quality", 1.0),
    }


def preset_resolved_chain(name: str) -> dict:
    """Return a preset chain with library IDs resolved to current local paths."""
    p = preset_get(name)
    if not p:
        raise ValueError(f"Preset '{name}' not found.")
    return _resolved_preset_chain(p)


def preset_is_dirty(name: str | None = None, chain: dict | None = None) -> bool:
    """Whether the live chain differs from a preset's effective settings."""
    name = name or preset_current()
    p = preset_get(name) if name else None
    if not p:
        return True
    current = chain_get() if chain is None else chain
    expected = _resolved_preset_chain(p)
    actual = {
        "model": current.get("model"),
        "ir": current.get("ir"),
        "gain": current.get("gain", 1.0),
        "master": current.get("master", 1.0),
        "quality": current.get("quality", 1.0),
    }
    return actual != expected


def preset_load(name: str) -> dict | None:
    """Apply a preset to the live chain (engine hot-swaps within ~0.3s).

    Model ids resolve to their current library paths; a preset whose files are
    gone raises ValueError naming the missing file. Returns the written config.
    """
    p = preset_get(name)
    if not p:
        raise ValueError(f"Preset '{name}' not found.")
    ch = p["chain"]
    cfg: dict = {"gain": ch.get("gain", 1.0), "master": ch.get("master", 1.0),
                 "quality": ch.get("quality", 1.0)}

    model = _model_path(ch["model_id"]) if ch.get("model_id") else ch.get("model_path")
    if not model or not Path(model).is_file():
        why = f"(model_id {ch['model_id']} unresolved)" if ch.get("model_id") else "(no model)"
        raise ValueError(f"Preset '{name}': model file missing -> {model or why}")
    cfg["model"] = model

    ir = _model_path(ch["ir_model_id"]) if ch.get("ir_model_id") else ch.get("ir_path")
    if ir:
        if not Path(ir).is_file():
            raise ValueError(f"Preset '{name}': IR file missing -> {ir}")
        cfg["ir"] = ir
    else:
        # preset 没有 CAB：显式置 null 让引擎移除旧 IR——键缺失时引擎不会
        # 更新（apply_chain 只处理存在的键），旧 CAB 会残留并误显示在链上
        cfg["ir"] = None
    # 输入源（乐器/干声文件+播放状态）不属于 preset 内容：加载 preset 保留当前输入源
    cur = chain_get()
    if isinstance(cur.get("input"), dict):
        cfg["input"] = cur["input"]
    chain_set(cfg)
    preset_set_active(name)
    return cfg


# Built-in catalog, resolved from exact local model ids at seed time.
# (name, note, amp_model_id, ir_model_id|None)
SEED_CHAINS = [
    ("band-guitar-rhcp", "Band Gear · Guitar · John Frusciante: Marshall Major 200 full rig", 383442, None),
    ("band-guitar-green-day", "Band Gear · Guitar · Billie Joe: Marshall 1959BJA full rig", 684630, None),
    ("band-bass-rhcp", "Band Gear · Bass · Flea: Gallien-Krueger RB800 direct", 419198, None),
    ("band-bass-green-day", "Band Gear · Bass · Green Day style: Ampeg SVT-CL pushed direct (approximation)", 382795, None),
    ("classic-guitar-beano", "Classic Pairing · Guitar · 1966 Marshall Bluesbreaker + G12 Alnico full rig", 677999, None),
    ("classic-guitar-vox-ef86", "Classic Pairing · Guitar · Vox AC30/4 EF86 + 2x12 Alnico full rig", 383682, None),
    ("classic-guitar-jtm45", "Classic Pairing · Guitar · Marshall JTM45 + Marshall Greenback 4x12", 494341, 239163),
    ("classic-guitar-fender-super", "Classic Pairing · Guitar · Fender Super Reverb 1977 full rig", 379720, None),
    ("classic-bass-gk-rb800", "Classic Pairing · Bass · Gallien-Krueger RB800 direct", 419198, None),
    ("classic-bass-ampeg-svt", "Classic Pairing · Bass · Ampeg SVT-CL clean direct", 382790, None),
]


def preset_group(name: str) -> tuple[str, str]:
    """Derive TUI grouping from the catalog name prefix; no schema fields."""
    parts = name.split("-", 2)
    if len(parts) >= 2 and parts[0] in {"band", "classic"}:
        category = "Band Gear" if parts[0] == "band" else "Classic Pairing"
        instrument = {"guitar": "Guitar", "bass": "Bass"}.get(parts[1])
        if instrument:
            return category, instrument
    return "Custom", "Other"


def _first_local_model(tone_id: int, ir: bool = False) -> int | None:
    """First downloaded model id of a tone (amp: non-IR first; ir: IR wavs)."""
    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM models WHERE tone_id = ? AND local_path IS NOT NULL "
            "AND architecture = ? ORDER BY id LIMIT 1",
            (tone_id, "IR" if ir else "SlimmableContainer")).fetchone()
        return row["id"] if row else None


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
            "SELECT id, tone_id, name, architecture, local_path FROM models "
            "WHERE tone_id = ? AND local_path IS NOT NULL ORDER BY id",
            (row["tone_id"],)).fetchall()
        return [_row_to_dict(r) for r in rows]


def downloaded_model_ids_by_tone() -> dict[int, set[int]]:
    """tone_id → set of locally downloaded model ids (one SQL pass)."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT tone_id, id FROM models WHERE local_path IS NOT NULL").fetchall()
    out: dict[int, set[int]] = {}
    for r in rows:
        out.setdefault(r["tone_id"], set()).add(r["id"])
    return out


def mark_download_state(hits: list[dict]) -> list[dict]:
    """Tag search hits with their local download state by comparing model ids.

    Each hit gains `download_state` in {"all", "partial", "none"} and
    `downloaded` (count of locally downloaded models). Tones with no local
    models are "none" without any API call; tones present locally are compared
    id-by-id against TONE3000's current model list (amp/pedal → A2 models,
    cab → all IRs), queried in parallel.
    """
    local = downloaded_model_ids_by_tone()
    todos = [t for t in hits if t.get("id") in local]
    done: dict[int, tuple[str, int]] = {}
    if todos:
        from concurrent.futures import ThreadPoolExecutor

        def remote_ids(t: dict) -> tuple[int, str, set[int]]:
            try:
                ms = tone3000.models(t["id"], a2_only=False)
            except Exception:
                return t["id"], "partial", set()
            if t.get("gear") == "cab":
                ids = {m["id"] for m in ms}
            else:
                ids = {m["id"] for m in ms
                       if m.get("architecture") == "SlimmableContainer"}
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


def preset_seed(*, replace: bool = False) -> int:
    """Create the built-in recommendation presets from the local library.

    Chains whose amp/IR are not in the library are skipped with a warning
    (never fails the rest). Returns the number of presets written.
    """
    if replace:
        with connect() as conn:
            conn.execute("DELETE FROM presets")
            conn.execute("DELETE FROM settings WHERE key = 'active_preset'")
            conn.commit()
    made = 0
    for name, note, amp_id, ir_id in SEED_CHAINS:
        if not _model_path(amp_id):
            print(f"[preset seed] skipped {name}: model {amp_id} is not available locally")
            continue
        chain = {
            "model_id": amp_id,
            "model_path": _model_path(amp_id),
            "ir_model_id": None, "ir_path": None,
            "gain": 0.8, "master": 0.8,
            "quality": 1.0,
        }
        if ir_id is not None:
            if not _model_path(ir_id):
                print(f"[preset seed] skipped {name}: IR model {ir_id} is not available locally")
                continue
            chain["ir_model_id"] = ir_id
            chain["ir_path"] = _model_path(ir_id)
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
        print(f"[preset seed] {name}: amp model {amp_id}"
              + (f" + ir model {ir_id}" if ir_id is not None else " (IR bypass)"))
    return made


# ---- CLI -----------------------------------------------------------------

def _fmt_table(tones: list[dict]) -> str:
    rows = [
        f"{t['id']:>8} | dl={t.get('downloads_count', 0):>6} fav={t.get('favorites_count', 0):>5} "
        f"a2={t.get('a2_models_count', 0):>3} | {t.get('gear', '?'):<8} | "
        f"{(t.get('title') or '')[:52]:<52} | @{t.get('username') or '?'}"
        for t in tones
    ]
    return "\n".join(rows)


def _fmt_show(t: dict) -> str:
    lines = [
        f"id           {t['id']}",
        f"title        {t.get('title')}",
        f"gear         {t.get('gear')}",
        f"platform     {t.get('platform')}",
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
            arch = m.get("architecture") or "?"
            lines.append(f"  {m['id']} [{arch}] {m.get('local_path') or m.get('model_url')}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # `gigbuddy` is the primary interactive entrypoint. Keep the import lazy so
    # agent-facing CLI commands do not pay the Textual startup cost.
    if not argv:
        from tui.app import main as tui_main
        tui_main([])
        return 0
    if argv[0] == "tui":
        from tui.app import main as tui_main
        tui_main(argv[1:])
        return 0
    if argv[0].split("=", 1)[0] in {"--in", "--out", "--ch", "--no-engine", "--theme"}:
        from tui.app import main as tui_main
        tui_main(argv)
        return 0

    p = argparse.ArgumentParser(prog="gigbuddy", description="GigBuddy tone library CLI")
    p.add_argument("--version", action="version",
                   version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    pt = sub.add_parser("tone", help="tone library operations")
    tsub = pt.add_subparsers(dest="tone_cmd", required=True)
    pl = tsub.add_parser("list", help="list imported tones")
    pl.add_argument("--gear", choices=["amp", "cab", "amp-cab"], help="filter by gear type")
    pl.add_argument("--limit", type=int, help="max rows")
    pl.add_argument("--query", help="text search (title/username/description)")
    pl.add_argument("--json", action="store_true", help="JSON output")
    ps = tsub.add_parser("search", help="search TONE3000 (import with: gigbuddy tone import <id>)")
    ps.add_argument("query")
    ps.add_argument("--gear", choices=["amp", "cab", "amp-cab"], help="filter by gear type")
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
    pseed = psub.add_parser("seed", help="create the built-in preset catalog")
    pseed.add_argument("--replace", action="store_true",
                       help="delete every existing preset before creating the catalog")

    args = p.parse_args(argv)

    if args.cmd == "tone":
        if args.tone_cmd == "list":
            tones = list_tones(args.gear, args.limit, args.query)
            print(json.dumps(tones, ensure_ascii=False, indent=2) if args.json else
                  (_fmt_table(tones) if tones else "No imported tones yet — `gigbuddy tone search <q>` first."))
        elif args.tone_cmd == "search":
            hits = tone3000.search(args.query, page_size=args.limit,
                                   gear_filters=[args.gear] if args.gear else None,
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
            print(json.dumps(chain_get(), ensure_ascii=False, indent=2))
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
                    ch = p["chain"]
                    amp = ch.get("model_id") or ch.get("model_path") or "—"
                    ir = ch.get("ir_model_id") or ch.get("ir_path") or "bypass"
                    marker = ">" if p["name"] == active else " "
                    dirty = " *" if p["name"] == active and preset_is_dirty(active) else ""
                    print(f"{marker} {p['name']:<28}{dirty} | amp {amp} | ir {ir} | "
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
            ch = p["chain"]
            if args.json:
                print(json.dumps(p, ensure_ascii=False, indent=2))
                return 0
            print(f"name       {p['name']}")
            if p.get("note"):
                print(f"note       {p['note']}")
            print(f"created    {p.get('created_at')}")
            print(f"updated    {p.get('updated_at')}")
            print(f"amp model  {ch.get('model_id') or '(external path)'}  -> "
                  f"{ch.get('model_path') or '—'}")
            if ch.get("ir_model_id") or ch.get("ir_path"):
                print(f"IR model   {ch.get('ir_model_id') or '(external path)'}  -> "
                      f"{ch.get('ir_path') or '—'}")
            else:
                print("IR         bypass")
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
            n = preset_seed(replace=args.replace)
            print(f"Seeded {n}/{len(SEED_CHAINS)} presets.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
