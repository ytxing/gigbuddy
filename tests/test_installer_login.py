"""Tests for the install-time TONE3000 login gate."""

import io
import subprocess
from types import SimpleNamespace
from pathlib import Path

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


def test_prompt_stream_opens_terminal_for_reading(monkeypatch):
    stream = io.StringIO("\n")
    calls = []

    def fake_open(path, mode, *, encoding):
        calls.append((path, mode, encoding))
        if mode != "r":
            raise OSError("the terminal is not seekable")
        return stream

    monkeypatch.setattr(
        installer_login, "sys", SimpleNamespace(
            stdin=SimpleNamespace(isatty=lambda: False),
        ),
    )
    monkeypatch.setattr(installer_login, "open", fake_open, raising=False)

    result = installer_login._prompt_stream()

    assert result == (stream, True)
    assert calls == [("/dev/tty", "r", "utf-8")]


def test_installers_check_login_before_creating_runtime_environment():
    repo_root = Path(__file__).resolve().parents[1]
    for relative_path in ("scripts/install.sh", "install.sh"):
        script = (repo_root / relative_path).read_text(encoding="utf-8")
        assert script.index("Checking TONE3000 login") < script.index(
            "Creating Python environment")

    user_installer = (repo_root / "scripts/install.sh").read_text(
        encoding="utf-8")
    assert user_installer.index("Checking TONE3000 login") < user_installer.index(
        "\nstart_banner\n")
    assert user_installer.index("if confirm_install_start; then") < user_installer.index(
        "\nstart_banner\n")
    assert "Continue with the installation? [Y/n]" in user_installer
    assert "printf '==> %s\\n' \"$1\" >>\"${STATUS_FILE:?}\"" in user_installer
    assert "BANNER_STARTED=0" in user_installer


def test_user_installer_fetches_tags_despite_a_legacy_tag_refspec(tmp_path):
    """A retired tag in a shallow checkout must not block an upgrade."""
    remote = tmp_path / "remote.git"
    source = tmp_path / "source"
    checkout = tmp_path / "checkout"

    def git(*args, cwd=None):
        return subprocess.run(
            ["git", *args], cwd=cwd, check=True, text=True,
            capture_output=True,
        )

    git("init", "--bare", remote)
    git("init", source)
    git("-C", source, "config", "user.email", "test@example.com")
    git("-C", source, "config", "user.name", "GigBuddy test")
    (source / "README").write_text("fixture\n", encoding="utf-8")
    git("-C", source, "add", "README")
    git("-C", source, "commit", "-m", "fixture")
    git("-C", source, "tag", "-a", "v1.2.1", "-m", "fixture")
    git("-C", source, "remote", "add", "origin", remote)
    git("-C", source, "push", "origin", "HEAD", "--tags")

    git("init", checkout)
    git("-C", checkout, "remote", "add", "origin", remote)
    git("-C", checkout, "config", "remote.origin.fetch",
        "+refs/tags/v1.1.1:refs/tags/v1.1.1")
    git("-C", checkout, "fetch", "--quiet", "--force", "origin",
        "+refs/tags/*:refs/tags/*")
    git("-C", checkout, "rev-parse", "--verify", "refs/tags/v1.2.1^{tag}")

    script = (Path(__file__).resolve().parents[1] / "scripts" / "install.sh").read_text(
        encoding="utf-8")
    assert 'fetch --quiet --force origin "+refs/tags/*:refs/tags/*"' in script
    assert "fetch --quiet --tags --force origin" not in script
