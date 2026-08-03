#!/usr/bin/env python3
"""GigBuddy TONE3000 检索层（零本地库依赖，纯 API）

基于公开 Supabase anon key（JWT role=anon，设计上公开给客户端）。
数据源: TONE3000 (原 ToneHunt)，90k+ NAM 模型。

CLI:
    tone3000.py search <query>              # 关键词搜索（A2 架构）
    tone3000.py top [limit]                 # 全站下载排行
    tone3000.py models <tone_id>            # 列出 tone 的 A2 模型
    tone3000.py download <tone_id> <dest>   # 下载全部 A2 .nam 到目录
    tone3000.py dry <dest> [name...]        # 下载试听干音素材（MIT，mayer/brit/rollin 等）
"""
import json
import re
import sys
import time
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


def _get(url, **params):
    if params:
        url += "?" + "&".join(f"{k}={urllib.parse.quote(str(v), safe=',.')}" for k, v in params.items())
    req = urllib.request.Request(url, headers={
        "apikey": KEY, "Authorization": f"Bearer {KEY}", "content-profile": "public"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def _post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"apikey": KEY, "Authorization": f"Bearer {KEY}",
                                          "content-profile": "public", "Content-Type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def search(query="", page_size=50, order_by="trending", gear_filters=None,
           usernames=None, tag_names=None):
    """search_tones_a2 RPC：关键词 + A2 架构 + 排序（trending / newest / best-match /
    downloads-all-time，对齐 tone3000.com 官网排序；空查询即 trending 流）

    gear_filters: None 或合法值列表 — ["amp"] / ["cab"] / ["amp-cab"]（TONE3000 值域，无 "ir"）
    usernames: 作者名列表（精确），tag_names: 标签名列表（精确）—— 与 query 叠加过滤
    """
    return _post(f"{API}/rpc/search_tones_a2", {
        "query_term": query, "page_number": 1, "page_size": page_size,
        "order_by": order_by, "tag_names": tag_names, "make_names": None,
        "gear_filters": gear_filters, "is_calibrated": False, "size_filters": None,
        "usernames": usernames, "architecture_filter": "2"})


def top(limit=50):
    """tones_counts 排行（下载/收藏）"""
    return _get(f"{API}/tones_counts",
                select="id,title,gear,downloads_count,favorites_count,a2_models_count,platform",
                order="downloads_count.desc", limit=limit)


def models(tone_id, a2_only=True):
    """tone 的全部模型；a2_only=True 时过滤 A2 (SlimmableContainer)。IR 等非 A2 音色传 False

    name 取 models 表顶层字段（TONE3000 网页/zip 下载的文件名即此 name 原样）。
    用 JSONB 投影只取 architecture，不拉全量 model_json（多模型 tone 的全量
    响应可达 19MB+、耗时 60s+，见 scripts/import_handoff.py 踩坑记录）。
    """
    ms = _get(f"{API}/models", tone_id=f"eq.{tone_id}",
              select="id,model_url,name,architecture:model_json->>architecture", limit=300)
    if not a2_only:
        return ms
    return [m for m in ms if m.get("architecture") == "SlimmableContainer"]


def download(tone_id, dest_dir, tag=None, a2_only=True, ext=None,
             return_paths=False, progress=None, quiet=False, model_ids=None):
    """下载 tone 的模型到 dest_dir；a2_only=False 下载全部（含 IR wav）；ext 强制扩展名

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
        # ext is used for IR downloads. Preserve an existing .nam/.wav suffix
        # instead of producing names such as cab.wav.wav.
        if ext:
            suffix = f".{ext.lstrip('.')}".lower()
            if not fname.lower().endswith((".nam", ".wav")):
                fname = f"{fname}{suffix}"
        elif not fname.lower().endswith((".nam", ".wav")):
            fname = f"{fname}.nam"
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
            records.append({"id": m["id"], "tone_id": tone_id, "model_url": m["model_url"],
                            "name": m.get("name"),
                            "model_json": {"architecture": m.get("architecture")}
                                          if m.get("architecture") else None,
                            "local_path": str(out)})
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
    t = dict(rows[0])
    if t.get("is_deleted"):
        return None
    # username / avatar_url live on the users table
    u = _get(f"{API}/users", id=f"eq.{t['user_id']}", select="username,avatar_url", limit=1)
    t["username"] = (u[0].get("username") if u else None)
    t["avatar_url"] = (u[0].get("avatar_url") if u else None)
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
    ms = _get(f"{API}/models", tone_id=f"eq.{tone_id}",
              select="id,name:model_json->metadata->>name", limit=50)
    t["model_name"] = None
    for m in ms:
        if m.get("name"):
            t["model_name"] = m["name"]
            break
    return t


def fmt(t):
    return (f"{t['id']:>7} | dl={t.get('downloads_count', 0):>6} fav={t.get('favorites_count', 0):>5} "
            f"a2={t.get('a2_models_count', 0):>3} | {t.get('gear', '?'):<8} | {t.get('title', '')[:58]} | @{t.get('username', '')}")


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


def fetch_dry_inputs(dest_dir, names=None):
    """下载 TONE3000 试听干音（MIT）到 dest_dir。names 为 DRY_INPUTS 的 key 列表，缺省全下"""
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    keys = names or list(DRY_INPUTS)
    got = 0
    for k in keys:
        fname = DRY_INPUTS[k]
        out = dest / fname
        if out.exists() and out.stat().st_size > 0:
            got += 1
            continue
        url = f"{DRY_INPUTS_BASE}/{urllib.parse.quote(fname)}"
        with urllib.request.urlopen(url, timeout=60) as r:
            out.write_bytes(r.read())
        got += 1
        time.sleep(0.1)
    print(f"干音素材 {got}/{len(keys)} -> {dest}")
    return got


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
