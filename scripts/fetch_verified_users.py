#!/usr/bin/env python3
"""Refresh data/verified_users.json from the TONE3000 website.

The site's "Verified Profiles" badge (tricolor checkmark next to the author
name on profile pages) is rendered from server-side data that the public REST
API does not expose — the users table has no flag. Detection: fetch the author
page with a full browser User-Agent (passes Cloudflare, which blocks default
UA/curl) and scan for the badge's "Verified Profiles" tooltip string.

Usage:
    .venv/bin/python scripts/fetch_verified_users.py [username...]
        # without arguments: probe every author in the local library
        # with arguments: probe just those usernames, merged into the list
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import tone3000  # noqa: E402


def main() -> None:
    args = sys.argv[1:]
    if args:
        names = args
    else:
        import library
        names = sorted({r["username"] for r in library.list_tones()
                        if r.get("username")})
    for name in names:
        verdict = tone3000.verify_username(name)
        if verdict is True:
            print(f"{name}: VERIFIED")
        elif verdict is False:
            print(f"{name}: -")
        else:
            print(f"{name}: ? (fetch failed)")
    print(f"-> {tone3000.VERIFIED_FILE}: "
          f"{sorted(tone3000.verified_users())}")


if __name__ == "__main__":
    main()
