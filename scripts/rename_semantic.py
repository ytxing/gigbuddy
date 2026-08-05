#!/usr/bin/env python3
"""把已导入的编码名模型文件重命名为 TONE3000 网页语义名（models.name 原样）。

历史导入（URL basename 编码名）→ 语义名（与网站 zip 下载一致）：
  1. models 表缺 name 列时 ALTER TABLE 补列
  2. 按 tone 查 API 拿 id→name 映射
  3. 文件改名为 <name>.<ext>（name 缺失退回 model_id，防丢文件），更新 local_path
幂等：DB name 与文件名一致的文件自动跳过。用法: python3 scripts/rename_semantic.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import library
import tone3000


def ensure_name_column(conn) -> bool:
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(models)")}
    if "name" in cols:
        return False
    conn.execute("ALTER TABLE models ADD COLUMN name TEXT")
    conn.commit()
    return True


def semantic_name(m, ext) -> str:
    """网页 zip 命名：models.name 原样；缺失时退回 model_id（防丢文件）"""
    base = m.get("name") or f"model-{m['id']}"
    if ext:
        if base.lower().endswith((".nam", ".wav")):
            return base
        return f"{base}.{ext.lstrip('.')}"
    return f"{base}.nam" if not base.lower().endswith((".nam", ".wav")) else base


def main() -> int:
    with library.connect() as conn:
        ensure_name_column(conn)
        rows = [dict(r) for r in conn.execute(
            "SELECT id, tone_id, name, local_path FROM models WHERE local_path IS NOT NULL")]
    if not rows:
        print("没有已下载的模型记录。")
        return 0

    # 按 tone 聚合，批量查 API 补 name
    by_tone = {}
    for r in rows:
        by_tone.setdefault(r["tone_id"], []).append(r)

    renamed = 0
    skipped = 0
    problems = []
    for tid, ms in sorted(by_tone.items()):
        try:
            api = {m["id"]: m.get("name") for m in tone3000.models(tid, a2_only=False)}
        except Exception as e:
            problems.append(f"tone {tid} API 查询失败: {e}")
            time.sleep(1)
            continue
        for r in ms:
            path = Path(r["local_path"])
            if not path.is_file():
                problems.append(f"tone {tid} model {r['id']}: 文件缺失 {path}")
                continue
            name = api.get(r["id"])
            ext = path.suffix.lstrip(".")
            new_name = semantic_name({"id": r["id"], "name": name}, ext or "nam")
            # 已语义化（与期望一致）→ 跳过
            if path.name == new_name:
                skipped += 1
                continue
            new_path = path.with_name(new_name)
            if new_path.exists():
                problems.append(f"tone {tid} model {r['id']}: 目标已存在 {new_path}")
                continue
            path.rename(new_path)
            with library.connect() as conn:
                conn.execute("UPDATE models SET name=?, local_path=? WHERE id=?",
                             (name, str(new_path), r["id"]))
                conn.commit()
            renamed += 1
            print(f"tone {tid} model {r['id']}: {path.name} -> {new_name}")
            time.sleep(0.05)
    print(f"\n重命名 {renamed} 个文件，跳过 {skipped} 个已语义化文件。")
    if problems:
        print("问题:")
        for p in problems:
            print("  -", p)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
