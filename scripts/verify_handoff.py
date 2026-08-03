#!/usr/bin/env python3
"""验证 handoff 精选音色库导入完整性（metadata 原样 + 模型文件在盘）。

对照 handoff 的 32 个 tone_id 逐一检查：DB 行存在、模型文件存在且非空、
gear/作者与 TONE3000 API 当前值一致。用法: python3 scripts/verify_handoff.py
"""
import json
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import library
import tone3000

socket.setdefaulttimeout(30)


def tone_api(tid):
    """tone_by_id with retry（TONE3000 API 偶发 SSL/IncompleteRead 断连）"""
    for attempt in (1, 2, 3):
        try:
            return tone3000.tone_by_id(tid)
        except Exception as e:
            if attempt == 3:
                raise
            time.sleep(2)

GROUPS = [
    ("Mayer 清音组",   [4658, 29285, 30435, 5691, 19, 51649]),
    ("RHCP 组",        [38981, 51310, 1789, 2694, 10912]),
    ("Green Day 组",   [78832, 43379, 45809]),
    ("经典吉他大师组",  [77706, 65578, 72145, 53601, 6379, 1071, 33505, 31267, 6233, 38613, 26459]),
    ("Pedal 组",       [45294, 5933, 26841]),
    ("IR 配套（cab）",  [27465, 51086, 45022, 45023]),
]
ALL_IDS = [i for _, ids in GROUPS for i in ids]


def main() -> int:
    problems = []
    total_models = 0
    for label, ids in GROUPS:
        print(f"\n===== {label} =====")
        for tid in ids:
            t = library.get_tone(tid)
            if not t:
                print(f"  ✗ {tid}: 不在库中")
                problems.append(f"{tid}: missing from DB")
                continue
            ms = t["models"] or []
            total_models += len(ms)
            missing = [m for m in ms if not (m.get("local_path") and Path(m["local_path"]).is_file()
                                             and Path(m["local_path"]).stat().st_size > 0)]
            # 与 API 现场比对 metadata（title/gear/username 原样性）
            api = tone_api(tid)
            drift = ""
            if api:
                for k in ("title", "gear", "username"):
                    if api.get(k) != t.get(k):
                        drift += f" [{k}: db={t.get(k)!r} api={api.get(k)!r}]"
            mark = "✗" if (missing or drift) else "✓"
            print(f"  {mark} {tid} | {t['title']} | gear={t['gear']} | @{t['username']} "
                  f"| dl={t.get('downloads_count')} | {len(ms)} models | 缺失文件 {len(missing)}")
            if drift:
                problems.append(f"{tid}: metadata drift{drift}")
            for m in missing:
                problems.append(f"{tid}: missing file {m.get('local_path')}")
    print(f"\n共 {len(ALL_IDS)} 个音色、{total_models} 个模型记录。")
    if problems:
        print(f"发现问题 {len(problems)} 项:")
        for p in problems:
            print("  -", p)
        return 1
    print("全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
