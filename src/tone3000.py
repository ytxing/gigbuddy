#!/usr/bin/env python3
"""GigBuddy TONE3000 检索层（零本地库依赖，纯 API）

基于公开 Supabase anon key（JWT role=anon，设计上公开给客户端）。
数据源: TONE3000 (原 ToneHunt)，90k+ NAM 模型。

CLI:
    tone3000.py search <query>              # 关键词搜索（官方默认 A1 + Custom）
    tone3000.py top [limit]                 # 全站下载排行
    tone3000.py models <tone_id>            # 列出 tone 的可用模型
    tone3000.py download <tone_id> <dest>   # 下载指定 tone 的模型
    tone3000.py dry <dest> [name...]        # 下载试听干音素材（MIT，mayer/brit/rollin 等）
"""
import json
import http.client
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def slugify(text, maxlen=48):
    """'Fender Super Reverb 1977' -> 'fender-super-reverb-1977' (empty -> 'tone')"""
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s[:maxlen].rstrip("-") or "tone")

API = "https://api.tone3000.com/rest/v1"
KEY = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
       "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imd6eWJpdW9weGtkeGJ5dG5vamRzIiwicm9sZSI6ImFub24iLCJpYXQiOjE3MzgwODIxNjUsImV4cCI6MjA1MzY1ODE2NX0."
       "Gq66BJXjtLsqP2nAGXm9Xb9PAjoeZalWUj66K4nmVSU")

# Canonical values from the public TONE3000 API. The REST mirror used by
# GigBuddy predates the v1 API and still returns ``platform`` and
# ``model_json.architecture`` in some responses, so the helpers below keep
# those aliases at the network boundary instead of making the UI understand
# two incompatible schemas.
GEAR_VALUES = ("amp", "amp-cab", "pedal", "outboard", "cab", "space",
               "experimental")
FORMAT_VALUES = ("nam", "ir", "aida-x", "aa-snapshot", "proteus")
ARCHITECTURE_VALUES = ("1", "2", "custom")
ENGINE_FORMATS = frozenset(("nam", "ir"))


def normalize_gear(value: str | None) -> str | None:
    """Return a canonical API gear value, accepting deprecated aliases."""
    if value is None:
        return None
    value = str(value).strip().lower()
    if not value:
        return None
    # The v1 API normalizes full-rig to amp-cab and treats gear=ir as a
    # deprecated alias. A CAB label is the useful local representation for
    # old rows that only carried that alias.
    return {"full-rig": "amp-cab", "ir": "cab"}.get(value, value)


def normalize_format(value: str | None) -> str | None:
    """Normalize a Tone format token without guessing from architecture."""
    if value is None:
        return None
    value = str(value).strip().lower().replace("_", "-")
    return value or None


def tone_format(tone: dict | None) -> str | None:
    """Read canonical ``format`` with ``platform``/legacy IR compatibility.

    Explicit ``format`` always wins. Only rows without either field use the
    old cab/space convention to infer IR; this prevents ``space + format=nam``
    from being misclassified while keeping pre-v1 local data usable.
    """
    tone = tone or {}
    if tone.get("format") not in (None, ""):
        return normalize_format(tone.get("format"))
    if tone.get("platform") not in (None, ""):
        return normalize_format(tone.get("platform"))
    raw_gear = str(tone.get("gear") or "").strip().lower()
    if raw_gear == "ir" or raw_gear in {"cab", "space"}:
        return "ir"
    return None


def is_ir_tone(tone: dict | None) -> bool:
    """Whether a Tone should be handled as an impulse-response pack."""
    tone = tone or {}
    fmt = tone_format(tone)
    if fmt is not None:
        return fmt == "ir"
    return str(tone.get("gear") or "").strip().lower() in {"cab", "space", "ir"}


def normalize_architecture(value: str | None) -> str | None:
    """Map legacy architecture labels to API Architecture enum values."""
    if value is None:
        return None
    raw = str(value).strip().lower().replace("_", "").replace("-", "")
    return {
        "1": "1", "a1": "1", "wavenet": "1",
        "2": "2", "a2": "2", "slimmablecontainer": "2",
        "custom": "custom",
    }.get(raw)


def _legacy_architecture(value: str | None) -> str | None:
    """Preserve the old UI's labels while exposing architecture_version."""
    return {"1": "WaveNet", "2": "SlimmableContainer", "custom": "Custom"}.get(value)


def normalize_model(model: dict | None) -> dict:
    """Return a model row with canonical ``architecture_version``.

    ``architecture`` remains as a compatibility alias for the existing local
    database and tests; new code should branch on ``architecture_version``.
    """
    row = dict(model or {})
    nested = row.get("model_json") if isinstance(row.get("model_json"), dict) else {}
    # The legacy REST projection returns both ``architecture_version: null``
    # and the old ``architecture`` label.  A non-empty canonical value wins;
    # otherwise retain the legacy label as the compatibility fallback.  A
    # genuine v1 non-NAM row has no legacy label and therefore stays NULL.
    raw_version = row.get("architecture_version")
    if raw_version in (None, ""):
        raw_version = row.get("architecture")
    if raw_version in (None, ""):
        raw_version = nested.get("architecture_version") or nested.get("architecture")
    version = normalize_architecture(raw_version)
    row["architecture_version"] = version
    if row.get("architecture") in (None, "") and version is not None:
        row["architecture"] = _legacy_architecture(version)
    return row


def model_architecture_version(model: dict | None) -> str | None:
    """Read Model architecture, with a legacy label fallback for old rows."""
    row = model or {}
    nested = row.get("model_json") if isinstance(row.get("model_json"), dict) else {}
    raw = row.get("architecture_version")
    if raw in (None, ""):
        raw = row.get("architecture")
    if raw in (None, ""):
        raw = nested.get("architecture_version") or nested.get("architecture")
    return normalize_architecture(raw)


def is_ir_model(model: dict | None, tone: dict | None = None) -> bool:
    """Whether a Model row is a non-NAM IR for a given parent Tone."""
    model = model or {}
    # ``architecture_version`` is the canonical field.  A legacy ``IR``
    # marker may still be present beside it, but must not override an
    # explicit architecture value from the API.
    version = model_architecture_version(model)
    if version is not None:
        return False
    parent_format = tone_format(tone) if tone is not None else None
    nested = model.get("model_json") if isinstance(model.get("model_json"), dict) else {}
    raw_arch = str(
        model.get("architecture") or nested.get("architecture") or ""
    ).strip().lower()
    if parent_format not in (None, "nam", "ir"):
        # An explicit non-NAM Tone format wins over a stale legacy marker.
        return False
    if raw_arch == "ir":
        return True
    path = str(model.get("local_path") or model.get("name") or "").lower()
    if path.endswith(".wav"):
        return True
    # Official Model.architecture_version is null for non-NAM formats. The
    # parent format is the tie-breaker when a legacy response omitted the old
    # architecture label.
    if model.get("architecture_version") in (None, "") and tone is not None:
        return is_ir_tone(tone)
    return False


def is_engine_compatible_model(model: dict | None,
                               tone: dict | None = None) -> bool:
    """Whether a model can be loaded by GigBuddy's current audio engine.

    The engine currently accepts NAM files and WAV impulse responses only.
    Keep the other official TONE3000 formats visible as metadata, but do not
    let them reach a ``model``/``ir`` chain slot.  Rows from the legacy REST
    mirror may omit ``format``; their architecture marker or filename keeps
    that older data usable.
    """
    model = model or {}
    local_path = str(model.get("local_path") or "").lower()

    def local_suffix_matches(expected: str) -> bool:
        # Remote rows have no local path yet, so format metadata is enough to
        # decide whether they may be downloaded.  Once a path exists, the
        # engine-facing slot must agree with the actual file type as well.
        return not local_path or Path(local_path).suffix == expected

    # Model-level IR markers and local WAV paths can coexist with a legacy
    # parent Tone that is labeled NAM (amp-cab packs may contain both).
    if is_ir_model(model, tone):
        return local_suffix_matches(".wav")
    fmt = tone_format(tone) if tone is not None else tone_format(model)
    if fmt == "nam":
        return local_suffix_matches(".nam")
    if fmt == "ir":
        return local_suffix_matches(".wav")
    if fmt is not None:
        return False
    if model_architecture_version(model) is not None:
        return local_suffix_matches(".nam")
    # The legacy Supabase projection can omit both ``name`` and the old
    # architecture marker.  If the parent Tone also has no canonical format,
    # retain the historical amp path instead of hiding a loadable NAM row.
    # Explicit non-NAM formats have already returned False above.
    if tone is not None and tone_format(tone) is None:
        expected = ".wav" if is_ir_tone(tone) else ".nam"
        return local_suffix_matches(expected)
    path = local_path or str(model.get("name") or "").lower()
    return path.endswith((".nam", ".wav"))


def normalize_tone(tone: dict | None) -> dict:
    """Return a Tone row using v1 names while retaining old aliases."""
    row = dict(tone or {})
    raw_gear = row.get("gear")
    row["gear"] = normalize_gear(raw_gear)
    if row.get("format") in (None, ""):
        legacy = normalize_format(row.get("platform"))
        if legacy is not None:
            row["format"] = legacy
    else:
        row["format"] = normalize_format(row.get("format"))
    # v1 returns taxonomy values as objects ({id, name}), while the public
    # Supabase projection returns plain names.  The local schema intentionally
    # stores the stable names, so accept both shapes at the boundary.
    for field in ("tags", "makes"):
        values = row.get(field)
        if isinstance(values, list):
            row[field] = [
                item.get("name") if isinstance(item, dict) else item
                for item in values
            ]

    user = row.get("user")
    if isinstance(user, dict):
        # Keep the official embedded user object for callers that need it,
        # while filling the flat legacy columns used by the TUI.
        if row.get("username") in (None, ""):
            row["username"] = user.get("username")
        if row.get("avatar_url") in (None, ""):
            row["avatar_url"] = user.get("avatar_url")
        if row.get("user_id") in (None, ""):
            row["user_id"] = user.get("id")
        if row.get("user_url") in (None, ""):
            row["user_url"] = user.get("url")
    elif row.get("username") is not None:
        # Legacy search rows expose flattened user fields only.  Materialize
        # the same small EmbeddedUser shape so local imports remain coherent.
        row["user"] = {
            "id": row.get("user_id"),
            "username": row.get("username"),
            "avatar_url": row.get("avatar_url"),
            "url": row.get("user_url"),
        }
    sizes = row.get("sizes")
    if isinstance(sizes, list):
        row["sizes"] = [
            item.get("name") if isinstance(item, dict) else item
            for item in sizes
        ]
    # Keep a supplied platform value readable for old callers, but do not
    # synthesize the deprecated alias on canonical v1 responses.
    # Deprecated gear=ir implies format=ir only when no explicit format was
    # supplied. The canonical gear remains cab for local routing.
    if str(raw_gear or "").strip().lower() == "ir" and row.get("format") is None:
        row["format"] = row["platform"] = "ir"
    return row


ROOT = Path(__file__).resolve().parent.parent
VERIFIED_FILE = ROOT / "data" / "verified_users.json"
_verified_cache: set[str] | None = None
_verified_write_lock = threading.Lock()

# tone3000.com sits behind Cloudflare: the default urllib UA gets the
# __next_error__ page, but a full browser UA passes through.
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/126.0 Safari/537.36")


def verified_users() -> set[str]:
    """Usernames carrying TONE3000's "Verified Profiles" badge.

    The badge is rendered from server-side data the public REST API does not
    expose (users table has no flag), so the list is mirrored from the website
    author pages into data/verified_users.json; verify_username() adds entries
    on demand and scripts/fetch_verified_users.py refreshes the whole set.
    Falls back to empty on missing/corrupt file so callers never break.
    """
    global _verified_cache
    if _verified_cache is None:
        try:
            with VERIFIED_FILE.open() as fh:
                _verified_cache = set(json.load(fh).get("users") or [])
        except (OSError, ValueError, TypeError, KeyError):
            _verified_cache = set()
    return _verified_cache


def is_verified(username: str | None) -> bool:
    """Return whether a username is present in the persisted positive cache."""
    name = str(username or "").lower()
    return bool(name) and name in verified_users()


def verify_username(username: str, *, timeout: float = 8.0) -> bool | None:
    """Check the "Verified Profiles" badge for one author, live.

    The badge is server-rendered and the REST API exposes no flag, so the
    author page is fetched (full browser UA passes Cloudflare) and scanned for
    the badge's "Verified Profiles" tooltip string. Returns True/False on
    success (True is persisted into verified_users.json so later lookups are
    free); None on network/parse failure — callers then keep showing no badge
    and may retry on the next detail view.
    """
    name = str(username or "").lower()
    if not name:
        return False
    if name in verified_users():
        return True
    req = urllib.request.Request(
        f"https://www.tone3000.com/{name}?_data",
        headers={"User-Agent": BROWSER_UA,
                 "Accept": "text/html,application/xhtml+xml"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            html = response.read(2 * 1024 * 1024)  # marker sits early in page
    except Exception:
        return None
    ok = b"Verified Profiles" in html
    if ok:
        _remember_verified(name)
    return ok


def _remember_verified(name: str) -> None:
    """Persist a freshly confirmed verification (atomic file replace)."""
    global _verified_cache
    with _verified_write_lock:
        if _verified_cache is None:
            verified_users()
        _verified_cache.add(name)
        try:
            data = json.loads(VERIFIED_FILE.read_text())
        except (OSError, ValueError, TypeError):
            data = {"note": "TONE3000 'Verified Profiles' usernames."}
        users = set(data.get("users") or []) | {name}
        data["users"] = sorted(users)
        data["verified_at"] = time.strftime("%Y-%m-%d")
        tmp = VERIFIED_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n")
        tmp.replace(VERIFIED_FILE)


def _open_json(req):
    """Read a TONE3000 API response with bounded retry for dropped TLS links."""
    transient = (urllib.error.URLError, ssl.SSLError, TimeoutError,
                 ConnectionResetError, http.client.IncompleteRead)
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                return json.loads(response.read())
        except transient:
            if attempt == 2:
                raise
            time.sleep(0.5 * (2 ** attempt))


def _get(url, **params):
    if params:
        url += "?" + "&".join(f"{k}={urllib.parse.quote(str(v), safe=',.')}" for k, v in params.items())
    req = urllib.request.Request(url, headers={
        "apikey": KEY, "Authorization": f"Bearer {KEY}", "content-profile": "public"})
    return _open_json(req)


def _post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"apikey": KEY, "Authorization": f"Bearer {KEY}",
                                          "content-profile": "public", "Content-Type": "application/json"},
                                 method="POST")
    return _open_json(req)


def search(query="", page_size=50, order_by="trending", gear_filters=None,
           usernames=None, tag_names=None, make_names=None, page_number=1,
           architecture_filter=None):
    """Search tones through the public legacy RPC.

    ``architecture_filter`` follows the official API enum: ``"1"`` (A1),
    ``"2"`` (A2), or ``"custom"``.  ``None`` deliberately forwards an omitted
    filter, whose documented legacy default is A1 + Custom and which also
    keeps non-NAM tones such as IR in the result set.  The old RPC is retained
    because the desktop app has no OAuth token for the v1 REST endpoint.

    Sorting uses the official values (trending / newest / best-match /
    downloads-all-time; empty query is the trending stream).

    gear_filters: None 或合法值列表 — ["amp"] / ["cab"] / ["amp-cab"]（TONE3000 值域，无 "ir"）
    usernames: 作者名列表（精确），tag_names: 标签名列表（精确），
    make_names: 设备/Make 名列表（精确）—— 与 query 叠加过滤
    """
    if architecture_filter is not None:
        architecture_filter = normalize_architecture(architecture_filter)
        if architecture_filter is None:
            raise ValueError("architecture_filter must be 1, 2, or custom")
    body = {
        "query_term": query, "page_number": page_number, "page_size": page_size,
        "order_by": order_by, "tag_names": tag_names, "make_names": make_names,
        "gear_filters": gear_filters, "is_calibrated": False, "size_filters": None,
        "usernames": usernames}
    # An omitted query parameter is semantically different from an explicit
    # architecture value in the official API (omitted => A1 + Custom).
    if architecture_filter is not None:
        body["architecture_filter"] = architecture_filter
    rows = _post(f"{API}/rpc/search_tones_a2", body)
    return [normalize_tone(row) for row in rows]


_RANKING_TONE_FIELDS = (
    "id,title,gear,format,platform,downloads_count,favorites_count,"
    "a1_models_count,a2_models_count,custom_models_count,irs_count,"
    "models_count,created_at,user_id"
)


def _ranked_tones(order: str, limit: int) -> list[dict]:
    """Read ranking rows with a narrow fallback for the legacy view.

    The current public tones_counts view predates the official format column.
    It still exposes platform and all model counters, so only drop format
    after a schema-level 400; do not hide unrelated network errors.
    """
    try:
        rows = _get(f"{API}/tones_counts", select=_RANKING_TONE_FIELDS,
                    order=order, limit=limit)
    except urllib.error.HTTPError as exc:
        if exc.code not in (400, 404):
            raise
        legacy_fields = _RANKING_TONE_FIELDS.replace("format,", "")
        rows = _get(f"{API}/tones_counts", select=legacy_fields,
                    order=order, limit=limit)
    _attach_usernames(rows)
    return [normalize_tone(row) for row in rows]


def top(limit=50):
    """tones_counts 排行（下载/收藏）"""
    return _ranked_tones("downloads_count.desc", limit)


def top_favorites(limit=50):
    """收藏排行：search_tones_a2 RPC 无收藏排序（400），走 tones_counts 聚合表。

    行形状与 search 结果兼容。REQ-023：行缺 username/avatar_url（此前表格
    显示 @?）——按 user_id 批量联查 users 补上（一次 in 过滤请求）。
    """
    return _ranked_tones("favorites_count.desc", limit)


def top_creators(sort_by="tones", page_size=100, page_number=1):
    """Official TONE3000 creator leaderboard.

    The website's ``/top-creators`` page reads ``user_public_counts`` directly;
    use the same stable aggregate fields instead of rebuilding creator totals
    from arbitrary pages of tone search results.
    """
    column = {
        "tones": "public_tones_count",
        "downloads": "downloads_count",
        "favorites": "favorites_count",
        "models": "public_models_count",
    }.get(sort_by, "public_tones_count")
    page_size = max(1, int(page_size))
    page_number = max(1, int(page_number))
    return _get(
        f"{API}/user_public_counts",
        select="*",
        order=f"{column}.desc,username.asc",
        limit=page_size,
        offset=(page_number - 1) * page_size,
        **{column: "gt.0"},
    )


def _attach_usernames(rows: list[dict]) -> None:
    """按 user_id 批量联查 users，就地补 username/avatar_url（REQ-023）。"""
    user_ids = sorted({r.get("user_id") for r in rows if r.get("user_id")},
                      key=str)
    if not user_ids:
        return
    users = _get(f"{API}/users", id=f"in.({','.join(str(i) for i in user_ids)})",
                 select="id,username,avatar_url", limit=len(user_ids))
    by_id = {str(u["id"]): u for u in users}
    for row in rows:
        u = by_id.get(str(row.get("user_id")))
        if u:
            row["username"] = u.get("username")
            row["avatar_url"] = u.get("avatar_url")


def user(username: str) -> dict | None:
    """用户资料（bio/display_name/avatar/verified 依据等）；查不到返回 None。

    TOP CREATORS 聚焦作者行时展示作者信息用（REQ-012）；verified 徽章仍走
    verify_username 的独立判定。
    """
    rows = _get(f"{API}/users", username=f"eq.{username}", limit=1)
    return rows[0] if rows else None


def models(tone_id, a2_only=None):
    """List a tone's models with official architecture semantics.

    ``a2_only`` is a compatibility switch used by the pre-v1 caller.  ``True``
    selects A2 only, ``False`` returns every model, and ``None`` (the official
    default when the architecture query is omitted) selects A1 + Custom plus
    non-NAM models.  IR and other non-NAM models have a null
    ``architecture_version``.

    name 取 models 表顶层字段（TONE3000 网页/zip 下载的文件名即此 name 原样）。
    The canonical v1 field is selected directly; the JSONB projection remains
    as a fallback for old REST rows that only expose ``model_json.architecture``.
    """
    try:
        ms = _get(
            f"{API}/models", tone_id=f"eq.{tone_id}",
            select=("id,created_at,updated_at,user_id,model_url,name,size,"
                    "tone_id,architecture_version"), limit=300)
    except urllib.error.HTTPError as exc:
        # Older deployments did not expose the top-level field. Keep the
        # fallback narrow so a real network failure is not hidden by a second
        # request.
        if exc.code not in (400, 404):
            raise
        ms = _get(f"{API}/models", tone_id=f"eq.{tone_id}",
                  select=("id,created_at,updated_at,user_id,model_url,name,size,"
                          "tone_id,"
                          "architecture_version:model_json->>architecture_version,"
                          "architecture:model_json->>architecture"), limit=300)
    ms = [normalize_model(m) for m in ms]
    if a2_only is False:
        return ms
    if a2_only is True:
        return [m for m in ms if model_architecture_version(m) == "2"]
    return [m for m in ms if model_architecture_version(m) != "2"]


def download(tone_id, dest_dir, tag=None, a2_only=None, ext=None,
             return_paths=False, progress=None, quiet=False, model_ids=None):
    """Download models to ``dest_dir`` with the official architecture default.

    ``a2_only=None`` follows the API default (A1 + Custom plus non-NAM),
    ``False`` downloads every architecture/model, and ``True`` is retained for
    legacy A2-only callers. ``ext`` can force an IR suffix.

    文件名优先采用 models.name；旧响应没有 name 时采用 model_url 的 basename。
    不添加序号、不改写语义名称。model_ids 限制只下载指定模型（部分安装）。
    progress 回调接收 (completed, total, filename)。
    return_paths=True 时返回供 library 持久化的记录。
    quiet=True 供 TUI 等已有状态栏的调用方关闭 stdout 输出。
    """
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    ms = models(tone_id, a2_only=a2_only)
    if model_ids:
        ms = [m for m in ms if m["id"] in set(model_ids)]
    if not ms:
        if not quiet:
            print(f"[{tone_id}] 无匹配模型")
        return [] if return_paths else 0
    got = 0
    records = []
    for i, m in enumerate(sorted(ms, key=lambda x: x["id"]), 1):
        # 文件名优先采用 models.name 原样（TONE3000 网页 zip 命名规则，保留
        # 空格和语义参数）；旧 API 响应没有 name 时回退到 URL basename，避免
        # 把用户看到的原始文件名改成无意义的 model-<id>。
        fname = m.get("name")
        if not fname:
            url_path = urllib.parse.urlparse(m.get("model_url") or "").path
            fname = Path(urllib.parse.unquote(url_path)).name
        fname = fname or f"model-{m['id']}"
        # Use the requested pack suffix when supplied; otherwise derive the
        # engine suffix from each Model. This keeps mixed legacy amp-cab packs
        # correct even when their parent Tone is labeled NAM.
        version = model_architecture_version(m)
        if ext:
            desired_suffix = f".{ext.lstrip('.')}".lower()
        elif is_ir_model(m):
            desired_suffix = ".wav"
        elif version is not None:
            desired_suffix = ".nam"
        else:
            desired_suffix = None
        if desired_suffix:
            current_suffix = Path(fname).suffix.lower()
            if current_suffix in (".nam", ".wav"):
                if current_suffix != desired_suffix:
                    fname = f"{fname[:-len(current_suffix)]}{desired_suffix}"
            else:
                fname = f"{fname}{desired_suffix}"
        out = dest / fname
        if progress:
            progress(i - 1, len(ms), fname)
        if out.exists() and out.stat().st_size > 0:
            got += 1
        else:
            for attempt in range(3):  # 网络中断（IncompleteRead）重试
                try:
                    req = urllib.request.Request(m["model_url"])
                    with urllib.request.urlopen(req, timeout=60) as r:
                        data = r.read()
                    out.write_bytes(data)
                    break
                except Exception as e:
                    if attempt == 2:
                        raise
                    print(f"[{tone_id}] {fname} 下载中断({e})，重试 {attempt + 2}/3", flush=True)
                    time.sleep(1)
            got += 1
            time.sleep(0.1)
        if progress:
            progress(i, len(ms), fname)
        if return_paths:
            records.append({
                "id": m["id"],
                "created_at": m.get("created_at"),
                "updated_at": m.get("updated_at"),
                "user_id": m.get("user_id"),
                "tone_id": m.get("tone_id", tone_id),
                "model_url": m["model_url"],
                "name": m.get("name"),
                "size": m.get("size"),
                "architecture_version": m.get("architecture_version"),
                "model_json": {"architecture": m.get("architecture")}
                              if m.get("architecture") else None,
                "local_path": str(out),
            })
    if not quiet:
        print(f"[{tone_id}] 下载 {got}/{len(ms)} -> {dest}")
    return records if return_paths else got


def tone_by_id(tone_id, with_models=False):
    """Full metadata for one tone via REST (no RPC needed).

    Assembles a dict shaped identically to search_tones_a2 rows (23 fields;
    total_count is a search-level aggregation and stays excluded):
    tones_counts (+users for username/avatar_url, +tone_tags/tags, +tone_makes/makes
    for the tag/make name arrays, +models for model_name). Returns None if the
    tone does not exist (or was deleted).
    """
    rows = _get(f"{API}/tones_counts", id=f"eq.{tone_id}", limit=1)
    if not rows:
        return None
    t = normalize_tone(rows[0])
    if t.get("is_deleted"):
        return None
    # username / avatar_url live on the users table
    try:
        u = _get(f"{API}/users", id=f"eq.{t['user_id']}",
                 select="id,username,avatar_url,url", limit=1)
    except urllib.error.HTTPError as exc:
        # The legacy users view has no EmbeddedUser.url column.  Keep the
        # fallback narrow and synthesize the public profile route below.
        if exc.code not in (400, 404):
            raise
        u = _get(f"{API}/users", id=f"eq.{t['user_id']}",
                 select="id,username,avatar_url", limit=1)
    if u:
        t["username"] = u[0].get("username")
        t["avatar_url"] = u[0].get("avatar_url")
        t["user"] = dict(u[0])
        t["user_url"] = u[0].get("url") or (
            f"https://www.tone3000.com/{u[0]['username']}"
            if u[0].get("username") else None)
        t["user"]["url"] = t["user_url"]
    # tags: tone_tags join -> names
    ids = [r["tag_id"] for r in _get(f"{API}/tone_tags", tone_id=f"eq.{tone_id}", select="tag_id", limit=300)]
    if ids:
        chunks = ",".join(str(i) for i in ids)
        t["tags"] = [r["name"] for r in _get(f"{API}/tags", id=f"in.({chunks})", select="name", limit=300)]
    else:
        t["tags"] = []
    # makes: tone_makes join -> names
    ids = [r["make_id"] for r in _get(f"{API}/tone_makes", tone_id=f"eq.{tone_id}", select="make_id", limit=300)]
    if ids:
        chunks = ",".join(str(i) for i in ids)
        t["makes"] = [r["name"] for r in _get(f"{API}/makes", id=f"in.({chunks})", select="name", limit=300)]
    else:
        t["makes"] = []
    # model_name: first model's metadata name (NAM A2 metadata carries it)
    ms = _get(
        f"{API}/models", tone_id=f"eq.{tone_id}",
        select="id,name,model_json->metadata->>name", limit=50)
    t["model_name"] = None
    for m in ms:
        name = m.get("name") or m.get("metadata_name")
        if name:
            t["model_name"] = name
            break
    if not t.get("url"):
        t["url"] = (f"https://www.tone3000.com/tones/"
                     f"{slugify(t.get('title'))}-{tone_id}")
    return normalize_tone(t)


def tones_for_model_ids(model_ids):
    """Resolve exact TONE3000 model IDs to their parent tone search rows.

    ``search_tones_a2`` has no model-id predicate, so this takes the model
    lookup path instead of pretending a numeric ID is a title keyword. Each
    returned tone carries ``matched_model_ids`` for the TUI result label.
    """
    ids = list(dict.fromkeys(int(model_id) for model_id in model_ids))
    if not ids:
        return []
    chunks = ",".join(str(model_id) for model_id in ids)
    models_by_id = {
        int(row["id"]): row for row in _get(
            f"{API}/models", id=f"in.({chunks})", select="id,tone_id", limit=len(ids))
        if row.get("id") is not None and row.get("tone_id") is not None
    }
    matches: dict[int, list[int]] = {}
    for model_id in ids:
        row = models_by_id.get(model_id)
        if row:
            matches.setdefault(int(row["tone_id"]), []).append(model_id)
    tones = []
    for tone_id, matched_ids in matches.items():
        tone = tone_by_id(tone_id)
        if tone:
            tone["matched_model_ids"] = matched_ids
            tones.append(tone)
    return tones


def fmt(t):
    gear = normalize_gear(t.get("gear")) or "?"
    format_ = tone_format(t) or "?"
    return (f"{t['id']:>7} | dl={t.get('downloads_count', 0):>6} fav={t.get('favorites_count', 0):>5} "
            f"a2={t.get('a2_models_count', 0):>3} | {gear:<8} | {format_:<11} | "
            f"{t.get('title', '')[:58]} | @{t.get('username', '')}")


# ---- 试听干音素材（TONE3000 网页播放器内置，托管于其 MIT 开源仓库） ----
# 来源: github.com/tone-3000/neural-amp-modeler-wasm (MIT, © Steven Atkinson)
DRY_INPUTS_BASE = ("https://raw.githubusercontent.com/tone-3000/"
                   "neural-amp-modeler-wasm/refs/heads/main/ui/public/inputs")
DRY_INPUTS = {
    # 吉他（26）
    "brit": "Brit - Guitar.wav", "celestial": "Celestial - Guitar.wav",
    "cream": "Cream - Guitar.wav", "decapitated": "Decapitated - Guitar.wav",
    "fast-thrash": "Fast Thrash - Guitar.wav", "fear": "Fear - Guitar.wav",
    "groove-thrash": "Groove Thrash - Guitar.wav", "hammer-lead": "Hammer Lead - Guitar.wav",
    "harmonics": "Harmonics - Guitar.wav", "honky": "Honky - Guitar.wav",
    "hotrod": "Hotrod - Guitar.wav", "jazz-hop": "Jazz Hop - Guitar.wav",
    "jazz-trot": "Jazz Trot - Guitar.wav", "john": "John - Guitar.wav",
    "lunar": "Lunar - Guitar.wav", "mayer": "Mayer - Guitar.wav",
    "metalcore": "Metalcore - Guitar.wav", "pluck": "Pluck - Guitar.wav",
    "pop-punk": "Pop Punk - Guitar.wav", "power": "Power - Guitar.wav",
    "power-thrash": "Power Thrash - Guitar.wav", "progression": "Progression -  Guitar.wav",
    "raid": "Raid - Guitar.wav", "rotary": "Rotary - Guitar.wav",
    "slide-lead": "Slide Lead - Guitar.wav", "smooth": "Smooth - Guitar.wav",
    "stroke": "Stroke - Guitar.wav", "tomb": "Tomb - Guitar.wav",
    # 贝斯（7）
    "downtown": "Downtown - Bass.wav", "drivin": "Drivin' - Bass.wav",
    "frogger": "Frogger - Bass.wav", "garden": "Garden - Bass.wav",
    "rollin": "Rollin' - Bass.wav", "smokin": "Smokin' - Bass.wav",
}


def fetch_dry_inputs(dest_dir, names=None, progress=None):
    """下载 TONE3000 试听干音（MIT）到 dest_dir。names 为 DRY_INPUTS 的 key 列表，缺省全下。
    progress(done, total, fname) 可选回调（每文件一次，done=已下载数）"""
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    keys = names or list(DRY_INPUTS)
    total = len(keys)
    done = 0
    got = 0
    for k in keys:
        fname = DRY_INPUTS[k]
        out = dest / fname
        if out.exists() and out.stat().st_size > 0:
            done += 1
            got += 1
            continue
        url = f"{DRY_INPUTS_BASE}/{urllib.parse.quote(fname)}"
        with urllib.request.urlopen(url, timeout=60) as r:
            out.write_bytes(r.read())
        done += 1
        got += 1
        if progress:
            progress(done, total, fname)
        time.sleep(0.1)
    if progress:
        progress(done, total, None)
    print(f"干音素材 {got}/{total} -> {dest}")
    return got


def fetch_dry_inputs_missing(dest_dir, names=None):
    """返回 dest_dir 中缺失（不存在或为空）的干声素材 key 列表。
    names 为 DRY_INPUTS 的 key 列表，缺省全部"""
    dest = Path(dest_dir)
    keys = names or list(DRY_INPUTS)
    missing = []
    for k in keys:
        out = dest / DRY_INPUTS[k]
        if not out.exists() or out.stat().st_size == 0:
            missing.append(k)
    return missing


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 1
    cmd = args[0]
    if cmd == "search":
        for t in search(args[1] if len(args) > 1 else ""):
            print(fmt(t))
    elif cmd == "top":
        for t in top(int(args[1]) if len(args) > 1 else 50):
            print(fmt(t))
    elif cmd == "models":
        for m in models(args[1]):
            print(f"model {m['id']}: {m['model_url'][:90]}")
    elif cmd == "download":
        download(args[1], args[2], args[3] if len(args) > 3 else None)
    elif cmd == "dry":
        fetch_dry_inputs(args[1], args[2:] or None)
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
