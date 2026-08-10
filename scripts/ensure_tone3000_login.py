#!/usr/bin/env python3
"""Ensure a TONE3000 session exists before an installer bootstrap.

Exit status 10 means the user declined or no interactive terminal was
available; installers should continue without starter presets in that case.
"""
from __future__ import annotations

import sys
from collections.abc import Callable
from typing import TextIO

import tone3000


SKIP_STARTER_PRESETS = 10


def _has_usable_login(token_fn: Callable[[], str]) -> bool:
    try:
        token_fn()
    except (tone3000.AuthenticationRequiredError,
            tone3000.Tone3000HTTPError, OSError, ValueError):
        return False
    return True


def _prompt_stream() -> tuple[TextIO | None, bool]:
    """Return an interactive stream, including for ``curl | bash``."""
    if sys.stdin.isatty():
        return sys.stdin, False
    try:
        return open("/dev/tty", "r+", encoding="utf-8"), True
    except OSError:
        return None, False


def ensure_login(*, input_stream: TextIO | None = None,
                 output: TextIO | None = None,
                 error: TextIO | None = None,
                 token_fn: Callable[[], str] | None = None,
                 login_fn: Callable[[], dict] | None = None) -> int:
    """Check the local session and optionally run the normal OAuth flow."""
    output = output or sys.stdout
    error = error or sys.stderr
    token_fn = token_fn or tone3000.access_token
    login_fn = login_fn or tone3000.login

    if _has_usable_login(token_fn):
        print("TONE3000 login found.", file=output)
        return 0

    owned_stream = False
    stream = input_stream
    if stream is None:
        stream, owned_stream = _prompt_stream()
    if stream is None:
        print(
            "No interactive terminal is available; continuing without "
            "starter presets. Run `gigbuddy tone login` and `gigbuddy "
            "preset bootstrap` later.",
            file=error,
        )
        return SKIP_STARTER_PRESETS

    try:
        print("No TONE3000 login was found.", file=output)
        print("Log in now? [Y/n] ", file=output, end="", flush=True)
        answer = stream.readline().strip().casefold()
    finally:
        if owned_stream:
            stream.close()

    if answer in {"n", "no"}:
        print(
            "Skipping starter presets. Run `gigbuddy tone login` and "
            "`gigbuddy preset bootstrap` later.",
            file=output,
        )
        return SKIP_STARTER_PRESETS

    print("Starting TONE3000 login in the system browser.", file=output)
    try:
        login_fn()
    except (tone3000.AuthenticationRequiredError,
            tone3000.Tone3000HTTPError, OSError, ValueError) as exc:
        print(f"TONE3000 login failed: {exc}", file=error)
        return 1
    print("TONE3000 login complete.", file=output)
    return 0


def main() -> int:
    return ensure_login()


if __name__ == "__main__":
    raise SystemExit(main())
