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
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import tone3000

ROOT = Path(__file__).resolve().parent.parent
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
    chain_json  TEXT NOT NULL,  -- {"model_id", "model_path", "ir_model_id", "ir_path", "gain", "master"}
    created_at  TEXT,
    updated_at  TEXT
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
    """Insert or update one model row (local_path refreshed on re-import)."""
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
               tag: str | None = None, has_files: bool = False) -> list[dict]:
    """List library tones. has_files=True keeps only tones with downloaded files
    (metadata-only rows don't count as "local" — the UI treats them as remote)."""
    with connect() as conn:
        sql = "SELECT * FROM tones"
        where, args = [], []
        if has_files:
            where.append("EXISTS (SELECT 1 FROM models m "
                         "WHERE m.tone_id = tones.id AND m.local_path IS NOT NULL)")
        if gear:
            where.append("gear = ?")
            args.append(gear)
        if author:
            where.append("username = ?")
            args.append(author)
        if tag:
            where.append("EXISTS (SELECT 1 FROM json_each(tones.tags) "
                         "WHERE json_each.value = ?)")
            args.append(tag)
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


# ---- import ---------------------------------------------------------------

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
    row["local_dir"] = str(dest)
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
    """Current chain config ({} if missing/broken)."""
    try:
        return json.loads(CHAIN_FILE.read_text())
    except Exception:
        return {}


def chain_set(cfg: dict) -> None:
    """Write chain config atomically (tmp+rename; engine hot-swaps within 0.3s)."""
    tmp = CHAIN_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
    tmp.rename(CHAIN_FILE)


# ---- presets (named chain snapshots, logic references into the library) ----

def _model_id_for_path(path: str) -> int | None:
    """Reverse-lookup models.local_path → model id (None if not a library file)."""
    if not path:
        return None
    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM models WHERE local_path = ?", (path,)).fetchone()
        return row["id"] if row else None


def tone_title_for_path(path: str) -> str | None:
    """Tone title owning the model at `path` (None if not a library file).

    Used by the TUI chain panel to show the human tone name above the raw
    filename of the active amp/IR.
    """
    if not path:
        return None
    with connect() as conn:
        row = conn.execute(
            "SELECT t.title FROM models m JOIN tones t ON t.id = m.tone_id "
            "WHERE m.local_path = ?", (path,)).fetchone()
        return row["title"] if row else None


def _model_path(model_id: int) -> str | None:
    """Resolve a library model id to its current local_path (follows renames)."""
    with connect() as conn:
        row = conn.execute(
            "SELECT local_path FROM models WHERE id = ?", (model_id,)).fetchone()
        return row["local_path"] if row else None


def preset_save(name: str, note: str | None = None) -> dict:
    """Snapshot the current live chain as a named preset.

    model/ir paths that belong to the library are stored as logic references
    (model_id) so renames/migrations never break a preset; arbitrary paths are
    kept verbatim. Overwrites a preset with the same name.
    """
    cfg = chain_get()
    chain = {
        "model_id": _model_id_for_path(cfg.get("model")),
        "model_path": cfg.get("model"),
        "ir_model_id": _model_id_for_path(cfg.get("ir")),
        "ir_path": cfg.get("ir"),
        "gain": cfg.get("gain", 1.0),
        "master": cfg.get("master", 1.0),
    }
    now = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        conn.execute(
            "INSERT INTO presets (name, note, chain_json, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET note=excluded.note, "
            "chain_json=excluded.chain_json, updated_at=excluded.updated_at",
            (name, note, json.dumps(chain, ensure_ascii=False), now, now))
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
        conn.commit()
        return cur.rowcount > 0


def preset_load(name: str) -> dict | None:
    """Apply a preset to the live chain (engine hot-swaps within ~0.3s).

    Model ids resolve to their current library paths; a preset whose files are
    gone raises ValueError naming the missing file. Returns the written config.
    """
    p = preset_get(name)
    if not p:
        raise ValueError(f"Preset '{name}' not found.")
    ch = p["chain"]
    cfg: dict = {"gain": ch.get("gain", 1.0), "master": ch.get("master", 1.0)}

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
    chain_set(cfg)
    return cfg


# Built-in recommendation chains (docs/tone-chain-recommendations.md), resolved
# from the local library at seed time: (name, note, amp_tone_id, ir_tone_id|None)
SEED_CHAINS = [
    ("mayer-clean",  "John Mayer 清音链：Two-Rock SSS + Fender DR Mix Ready",
     4658, 27465),
    ("classic-rock", "经典摇滚链：JCM800 2203 + Marshall 1960BV",
     1071, 51086),
    ("british",      "英式链：VOX AC30 CH + Celestion V30 Mesa 4x12",
     31267, 45023),
    ("slash",        "Slash 链：1959 BRBS SIR #36 + Marshall 1960BV",
     6379, 51086),
    ("rhcp-greenday", "RHCP/GreenDay 链：Marshall Major 200（自带箱体，免 IR）",
     51310, None),
]


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
    with connect() as conn:
        row = conn.execute(
            "SELECT tone_id FROM models WHERE local_path = ?", (path,)).fetchone()
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


def preset_seed() -> int:
    """Create the built-in recommendation presets from the local library.

    Chains whose amp/IR are not in the library are skipped with a warning
    (never fails the rest). Returns the number of presets written.
    """
    made = 0
    for name, note, amp_tone, ir_tone in SEED_CHAINS:
        amp_id = _first_local_model(amp_tone)
        if amp_id is None:
            print(f"[preset seed] 跳过 {name}: tone {amp_tone} 无本地模型")
            continue
        chain = {
            "model_id": amp_id,
            "model_path": _model_path(amp_id),
            "ir_model_id": None, "ir_path": None,
            "gain": 0.8, "master": 0.8,
        }
        if ir_tone is not None:
            ir_id = _first_local_model(ir_tone, ir=True)
            if ir_id is None:
                print(f"[preset seed] 跳过 {name}: IR tone {ir_tone} 无本地 IR")
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
              + (f" + ir model {ir_id}" if ir_tone is not None else " (免 IR)"))
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
    p = argparse.ArgumentParser(prog="gigbuddy", description="GigBuddy tone library CLI")
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
    pd = psub.add_parser("delete", help="delete a preset")
    pd.add_argument("name")
    psub.add_parser("seed", help="create the built-in recommendation presets")

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
                for p in presets:
                    ch = p["chain"]
                    amp = ch.get("model_id") or ch.get("model_path") or "—"
                    ir = ch.get("ir_model_id") or ch.get("ir_path") or "bypass"
                    print(f"{p['name']:<16} | amp {amp} | ir {ir} | gain {ch.get('gain')} "
                          f"master {ch.get('master')}"
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
            print(f"gain       {ch.get('gain')}   master {ch.get('master')}")
        elif args.preset_cmd == "delete":
            if preset_delete(args.name):
                print(f"Preset '{args.name}' deleted.")
            else:
                print(f"Preset '{args.name}' not found.")
                return 1
        elif args.preset_cmd == "seed":
            n = preset_seed()
            print(f"Seeded {n}/{len(SEED_CHAINS)} presets.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
