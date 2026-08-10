"""Tests for the install-time TONE3000 login gate."""

import io

import tone3000

from scripts import ensure_tone3000_login as installer_login


def test_existing_login_skips_prompt(monkeypatch):
    output = io.StringIO()
    monkeypatch.setattr(tone3000, "access_token", lambda: "access")

    result = installer_login.ensure_login(
        output=output, error=io.StringIO(), input_stream=io.StringIO("n\n"))

    assert result == 0
    assert "login found" in output.getvalue().lower()
    assert "Log in now" not in output.getvalue()


def test_declining_login_skips_starter_presets(monkeypatch):
    output = io.StringIO()
    monkeypatch.setattr(
        tone3000, "access_token",
        lambda: (_ for _ in ()).throw(
            tone3000.AuthenticationRequiredError("login required")),
    )

    result = installer_login.ensure_login(
        output=output, error=io.StringIO(), input_stream=io.StringIO("n\n"),
    )

    assert result == installer_login.SKIP_STARTER_PRESETS
    assert "Skipping starter presets" in output.getvalue()


def test_accepting_login_runs_oauth(monkeypatch):
    calls = []
    monkeypatch.setattr(
        tone3000, "access_token",
        lambda: (_ for _ in ()).throw(
            tone3000.AuthenticationRequiredError("login required")),
    )

    result = installer_login.ensure_login(
        output=io.StringIO(), error=io.StringIO(), input_stream=io.StringIO("\n"),
        login_fn=lambda: calls.append(True) or {"access_token": "access"},
    )

    assert result == 0
    assert calls == [True]


def test_missing_terminal_requires_explicit_skip(monkeypatch):
    error = io.StringIO()
    monkeypatch.setattr(
        tone3000, "access_token",
        lambda: (_ for _ in ()).throw(
            tone3000.AuthenticationRequiredError("login required")),
    )
    monkeypatch.setattr(installer_login, "_prompt_stream",
                        lambda: (None, False))

    result = installer_login.ensure_login(output=io.StringIO(), error=error)

    assert result == installer_login.NO_INTERACTIVE_TERMINAL
    assert "--skip-presets" in error.getvalue()
