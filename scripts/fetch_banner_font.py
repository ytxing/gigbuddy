#!/usr/bin/env python3
"""Fetch figlet fonts from patorjk.com and render banner lines for install.sh.

patorjk.com hosts the TAAG web tool; its font files live in the patorjk/figlet.js
repo that the site loads from (the site itself exposes no direct font download
URLs). This script tries the site's likely paths first, then falls back to the
official figlet.js GitHub mirror. Both .flf (flf2a) and .tlf (tlf2a, e.g.
"Small Block") formats are supported.

Usage:
  fetch_banner_font.py <font> <text> [--version <ver>] [--version-font <name>]
                       [--out <file>]

Examples:
  fetch_banner_font.py Rebel GIGBUDDY
  fetch_banner_font.py Rebel GIGBUDDY --version v0.1.0-alpha.8 --version-font "Small Block"
"""

import argparse
import os
import shutil
import sys
import tempfile
import urllib.request

LOCAL_FONTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")

FONT_URLS = [
    # patorjk.com candidates first (the site's own layout), then the figlet.js
    # GitHub mirror that actually serves the site's fonts.
    "https://patorjk.com/software/taag/fonts/{name}.flf",
    "https://patorjk.com/software/taag/fonts/{name}.tlf",
    "https://patorjk.com/software/taag/fonts/{name}",
    "https://raw.githubusercontent.com/patorjk/figlet.js/main/fonts/{name}.flf",
    "https://raw.githubusercontent.com/patorjk/figlet.js/main/fonts/{name}.tlf",
]


def fetch_font(name: str) -> str:
    """Resolve a font file (flf or tlf): bundled scripts/fonts/ first, then
    patorjk.com and its figlet.js mirror."""
    for ext in (".flf", ".tlf"):
        local = os.path.join(LOCAL_FONTS, name + ext)
        if os.path.exists(local):
            print(f"[fetch] bundled {local}", file=sys.stderr)
            return local
    for tmpl in FONT_URLS:
        url = tmpl.format(name=urllib.parse.quote(name))
        try:
            with urllib.request.urlopen(url, timeout=15) as resp:
                data = resp.read()
            if len(data) < 100:
                continue
            head = data[:80].decode("utf-8", "ignore")
            if head.startswith(("flf2a", "tlf2a")):
                path = os.path.join(tempfile.gettempdir(), f"gb-{name}.flf")
                with open(path, "wb") as fh:
                    fh.write(data)
                print(f"[fetch] {url} -> {path}", file=sys.stderr)
                return path
        except Exception:
            continue
    raise SystemExit(f"could not fetch font {name!r} from patorjk.com or its mirror")


def load_font(path: str):
    """Parse a flf/tlf font file into {code: [rows]}."""
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().split("\n")
    hdr = lines[0].split()
    height, comment_lines = int(hdr[1]), int(hdr[5])
    idx = 1 + comment_lines
    chars = {}
    for code in range(32, 127):
        glyph = []
        for _ in range(height):
            line = lines[idx]
            idx += 1
            if line.endswith("@@"):
                line = line[:-2]
            elif line.endswith("@"):
                line = line[:-1]
            glyph.append(line.replace("$", " "))
        chars[code] = glyph
    return chars, height


def render(text: str, path: str) -> list[str]:
    chars, height = load_font(path)
    rows = [""] * height
    for ch in text:
        glyph = chars.get(ord(ch), chars[32])
        for r in range(height):
            rows[r] += glyph[r]
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("font", help="figlet font name, e.g. Rebel, Small Block")
    ap.add_argument("text", help="text to render, e.g. GIGBUDDY")
    ap.add_argument("--version", help="version string rendered to the right, bottom-aligned")
    ap.add_argument("--version-font", default="Small Block", help="font for the version (default: Small Block)")
    ap.add_argument("--gap", type=int, default=2, help="columns between text and version (default: 2)")
    ap.add_argument("--out", help="write the python LINES list to this file (default: stdout)")
    args = ap.parse_args()

    main_font = fetch_font(args.font)
    main_rows = render(args.text, main_font)
    while main_rows and not main_rows[-1].strip():
        main_rows.pop()          # drop trailing blank rows (figlet padding)
    h = len(main_rows)
    w = max(len(r.rstrip()) for r in main_rows)

    if args.version:
        ver_font = fetch_font(args.version_font)
        ver_rows = render(args.version, ver_font)
        while ver_rows and not ver_rows[-1].strip():
            ver_rows.pop()       # drop trailing blank rows so it aligns flush
        vh = len(ver_rows)
        vw = max(len(r.rstrip()) for r in ver_rows)
        # bottom-align: version occupies the last vh rows of the main block
        gap = " " * args.gap
        lines = []
        for i in range(h):
            row = main_rows[i].rstrip().ljust(w)
            if i >= h - vh:
                row += gap + ver_rows[i - (h - vh)].rstrip().ljust(vw)
            lines.append(row.rstrip())
    else:
        lines = [r.rstrip() for r in main_rows]

    out = "(\n" + "\n".join(f"  {line!r}," for line in lines) + "\n)"
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(out + "\n")
        print(f"written: {args.out}", file=sys.stderr)
    else:
        print(out)
    print(f"# {len(lines)} rows x {max(len(l) for l in lines)} cols", file=sys.stderr)
    return 0


if __name__ == "__main__":
    import urllib.parse  # noqa: PLC0415 (used in fetch_font)
    sys.exit(main())
