#!/usr/bin/env python3
"""批量导入 handoff 精选音色库（2026-08-02 TONE3000 音色链精选推荐）。

按 handoff 的六个分组导入；metadata 1:1 取自 TONE3000 API（不改写任何字段），
模型文件保留 model_url 原始 basename（见 src/tone3000.py download）。
用法: python3 scripts/import_handoff.py [--retry-failed]
"""
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import library

# SSL read 可能不遵守 urllib 的 timeout，用 socket 级超时兜底（导入可重入：已下载文件跳过）
socket.setdefaulttimeout(30)

# 分组顺序与 handoff 一致（id 以 handoff 表格为准）
GROUPS = [
    ("Mayer 清音组",        [4658, 29285, 30435, 5691, 19, 51649]),
    ("RHCP 组",             [38981, 51310, 1789, 2694, 10912]),
    ("Green Day 组",        [78832, 43379, 45809]),
    ("经典吉他大师组",       [77706, 65578, 72145, 53601, 6379, 1071, 33505, 31267, 6233, 38613, 26459]),
    ("Pedal 组",            [45294, 5933, 26841]),
    ("IR 配套（cab）",       [27465, 51086, 45022, 45023]),
]
ALL_IDS = [i for _, ids in GROUPS for i in ids]

FAILED_LOG = Path(__file__).resolve().parent.parent / ".scratch" / "import-failed.txt"


def run(ids, label):
    failed = []
    for n, tid in enumerate(ids, 1):
        t = None
        for attempt in (1, 2):  # 重试一次；已下载文件自动跳过，重试安全
            try:
                t = library.import_tone(tid)
                break
            except Exception as e:
                print(f"[{label}] tone {tid} attempt {attempt} 失败: {e}")
                time.sleep(1)
        if t is None:
            print(f"[{label}] ✗ {tid}: TONE3000 无此音色或下载失败")
            failed.append((tid, "not-found-or-download-failed"))
            continue
        print(f"[{label}] ✓ {n}/{len(ids)} tone {tid} | {t['title']} "
              f"({t['gear']}) {t['username']} | {len(t['models'])} models")
        time.sleep(0.2)
    return failed


def main():
    failed_all = []
    for label, ids in GROUPS:
        print(f"\n===== {label}（{len(ids)} 个）=====")
        failed_all += run(ids, label)
    print(f"\n完成。共 {len(ALL_IDS)} 个音色，失败 {len(failed_all)} 个：{failed_all}")
    FAILED_LOG.parent.mkdir(parents=True, exist_ok=True)
    FAILED_LOG.write_text("\n".join(f"{tid} {msg}" for tid, msg in failed_all) or "(none)")
    return 1 if failed_all else 0


if __name__ == "__main__":
    sys.exit(main())
