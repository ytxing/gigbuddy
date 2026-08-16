"""Tests for the install-time TONE3000 login gate."""

import io
import os
import pty
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

import tone3000

from scripts import ensure_tone3000_login as installer_login


INSTALLER = Path(__file__).resolve().parents[1] / "scripts" / "install.sh"


def _bash_quoted_command(*args):
    result = subprocess.run(
        ["bash", "-c", "printf ' %q' \"$@\"", "bash",
         *(str(arg) for arg in args)],
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout


def _prepare_minimal_user_install(tmp_path, *, legacy_wrapper_exists=False):
    fixture = tmp_path / "fixture"
    (fixture / "scripts").mkdir(parents=True)
    (fixture / "bin").mkdir()
    venv_bin = fixture / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").symlink_to(sys.executable)
    (fixture / "requirements.txt").write_text("", encoding="utf-8")
    (fixture / "pyproject.toml").write_text(
        '[project]\nname = "gigbuddy"\nversion = "1.2.4"\n',
        encoding="utf-8",
    )
    (fixture / "scripts" / "bootstrap.py").write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "root = Path(__file__).resolve().parents[1]\n"
        "(root / 'bootstrap-args').write_text("
        "'\\n'.join(sys.argv[1:]) + '\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )
    source_command = fixture / "bin" / "gigbuddy"
    source_command.write_text(
        "#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    source_command.chmod(0o755)
    if legacy_wrapper_exists:
        legacy_command = venv_bin / "gigbuddy"
        legacy_command.write_text(
            "#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        legacy_command.chmod(0o755)

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    fake_git.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "if [[ ${1:-} == clone ]]; then\n"
        "  for target; do :; done\n"
        "  mkdir -p \"$target\"\n"
        "  cp -R \"$GIGBUDDY_TEST_FIXTURE\"/. \"$target\"/\n"
        "  mkdir -p \"$target/.git\"\n"
        "  exit 0\n"
        "fi\n"
        "exit 64\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    fake_uv = fake_bin / "uv"
    fake_uv.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    fake_uv.chmod(0o755)

    install_root = tmp_path / "install"
    bin_dir = tmp_path / "global-bin"
    env = {
        **os.environ,
        "GIGBUDDY_BIN_DIR": str(bin_dir),
        "GIGBUDDY_HOME": str(install_root),
        "GIGBUDDY_REPO_URL": "https://example.test/gigbuddy.git",
        "GIGBUDDY_TEST_FIXTURE": str(fixture),
        "GIGBUDDY_UV": str(fake_uv),
        "GIGBUDDY_VERBOSE": "1",
        "HOME": str(tmp_path / "home"),
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
    }
    return env, fixture, install_root, bin_dir, fake_bin


def _run_minimal_user_install(env, *options):
    return subprocess.run(
        ["bash", str(INSTALLER), *options],
        env=env,
        stdin=subprocess.DEVNULL,
        text=True,
        capture_output=True,
        start_new_session=True,
    )


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
    assert "Skipping remote model preparation" in output.getvalue()
    assert "built-in Preset catalog" in output.getvalue()


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
    user_installer = (repo_root / "scripts/install.sh").read_text(
        encoding="utf-8")
    banner_start = user_installer.index(
        'if [[ "$SOURCE_CHECKOUT" != 1 ]]; then\n  start_banner\nfi')
    assert user_installer.index("Checking TONE3000 login") < user_installer.index(
        "Creating Python environment")
    assert user_installer.index("Checking TONE3000 login") < banner_start
    assert user_installer.index("if confirm_install_start; then") < banner_start
    assert "Continue with the installation? [Y/n]" in user_installer
    assert "printf '==> %s\\n' \"$1\" >>\"${STATUS_FILE:?}\"" in user_installer
    assert "BANNER_STARTED=0" in user_installer


def test_source_checkout_installer_delegates_to_the_shared_implementation(
        tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    checkout = tmp_path / "checkout"
    scripts = checkout / "scripts"
    scripts.mkdir(parents=True)
    (checkout / "install.sh").write_bytes((repo_root / "install.sh").read_bytes())
    (checkout / "install.sh").chmod(0o755)
    capture = tmp_path / "capture"
    shared = scripts / "install.sh"
    shared.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"${GIGBUDDY_SOURCE_CHECKOUT:-}\" "
        "  \"${GIGBUDDY_HOME:-}\" \"${GIGBUDDY_VERBOSE:-}\" "
        "  \"${GIGBUDDY_PYTHON:-}\" \"$@\" >\"$CAPTURE\"\n",
        encoding="utf-8",
    )
    shared.chmod(0o755)

    result = subprocess.run(
        ["bash", str(checkout / "install.sh"), "--no-engine", "--starter-dry"],
        env={**os.environ, "CAPTURE": str(capture), "PYTHON_BIN": "python-test"},
        text=True, capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert capture.read_text(encoding="utf-8").splitlines() == [
        "1", str(checkout), "1", "python-test", "--no-engine", "--starter-dry",
    ]


def test_shared_installer_source_mode_leaves_git_and_global_commands_untouched(
        tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    checkout = tmp_path / "checkout"
    scripts = checkout / "scripts"
    scripts.mkdir(parents=True)
    (checkout / ".git").mkdir()
    (checkout / "pyproject.toml").write_text(
        '[project]\nname = "gigbuddy"\nversion = "0.0.0"\n',
        encoding="utf-8",
    )
    (checkout / "requirements.txt").write_text("", encoding="utf-8")
    (scripts / "bootstrap.py").write_text("", encoding="utf-8")
    shared = scripts / "install.sh"
    shared.write_bytes((repo_root / "scripts" / "install.sh").read_bytes())
    shared.chmod(0o755)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    git_capture = tmp_path / "git-capture"
    fake_git = fake_bin / "git"
    fake_git.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$@\" >\"$GIT_CAPTURE\"\n"
        "exit 99\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    fake_uname = fake_bin / "uname"
    fake_uname.write_text(
        "#!/usr/bin/env bash\nprintf 'Linux\\n'\n",
        encoding="utf-8",
    )
    fake_uname.chmod(0o755)
    fake_login_python = fake_bin / "login-python"
    fake_login_python.write_text(
        "#!/usr/bin/env bash\nexit 0\n",
        encoding="utf-8",
    )
    fake_login_python.chmod(0o755)
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"${1:-}\" == venv ]]; then\n"
        "  for target; do :; done\n"
        "  mkdir -p \"$target/bin\"\n"
        "  printf '%s\\n' '#!/usr/bin/env bash' "
        "    'printf '\"'\"'%s\\n'\"'\"' \"$@\" >\"$BOOTSTRAP_CAPTURE\"' "
        "    >\"$target/bin/python\"\n"
        "  chmod +x \"$target/bin/python\"\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    bootstrap_capture = tmp_path / "bootstrap-capture"
    global_bin = tmp_path / "global-bin"

    result = subprocess.run(
        ["bash", str(shared), "--skip-dry-inputs", "--no-engine"],
        env={
            **os.environ,
            "BOOTSTRAP_CAPTURE": str(bootstrap_capture),
            "GIGBUDDY_BIN_DIR": str(global_bin),
            "GIGBUDDY_HOME": str(checkout),
            "GIGBUDDY_LOGIN_PYTHON": str(fake_login_python),
            "GIGBUDDY_PYTHON": "missing-runtime-python",
            "GIGBUDDY_SOURCE_CHECKOUT": "1",
            "GIGBUDDY_UV": str(fake_uv),
            "GIGBUDDY_VERBOSE": "1",
            "GIT_CAPTURE": str(git_capture),
            "HOME": str(tmp_path / "home"),
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        },
        text=True, capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert not git_capture.exists()
    assert not global_bin.exists()
    assert "Continue with the installation?" not in result.stdout
    assert bootstrap_capture.read_text(encoding="utf-8").splitlines()[-1:] == [
        "--skip-dry-inputs",
    ]


def test_shared_installer_does_not_interpolate_paths_into_bash_c():
    installer = (Path(__file__).resolve().parents[1] / "scripts" / "install.sh")
    script = installer.read_text(encoding="utf-8")

    assert 'run_quiet bash -c "' not in script


def test_release_metadata_matches_the_standalone_installer_ref():
    repo_root = Path(__file__).resolve().parents[1]
    metadata = tomllib.loads(
        (repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    version = metadata["project"]["version"]
    installer = (repo_root / "scripts" / "install.sh").read_text(
        encoding="utf-8")

    assert metadata["project"]["name"] == "gigbuddy"
    assert f'REPO_REF="${{GIGBUDDY_REF:-v{version}}}"' in installer


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


def test_existing_install_fetch_failure_preserves_preexisting_directories(
        tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    install_root = tmp_path / "install"
    (install_root / ".git").mkdir(parents=True)
    (install_root / "pyproject.toml").write_text(
        '[project]\nname = "gigbuddy"\nversion = "1.2.2"\n',
        encoding="utf-8",
    )
    for directory in (".venv", "data", "third_party"):
        path = install_root / directory
        path.mkdir()
        (path / "keep").write_text("preexisting\n", encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    fake_git.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \" $* \" == *\" rev-parse HEAD \"* ]]; then\n"
        "  printf 'old-head\\n'\n"
        "elif [[ \" $* \" == *\" fetch \"* ]]; then\n"
        "  printf 'fetch failed before checkout\\n' >&2\n"
        "  exit 23\n"
        "fi\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)

    result = subprocess.run(
        ["bash", str(repo_root / "scripts" / "install.sh"),
         "--skip-presets", "--skip-dry-inputs", "--no-engine"],
        env={
            **os.environ,
            "GIGBUDDY_BIN_DIR": str(tmp_path / "global-bin"),
            "GIGBUDDY_HOME": str(install_root),
            "HOME": str(tmp_path / "home"),
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "fetch failed before checkout" in result.stderr
    for directory in (".venv", "data", "third_party"):
        assert (install_root / directory / "keep").read_text(
            encoding="utf-8") == "preexisting\n"


def test_user_installer_prints_the_complete_failed_command_output(tmp_path):
    """The first diagnostic line must survive a command log longer than 40 lines."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    fake_git.write_text(
        "#!/usr/bin/env bash\n"
        "for number in $(seq 1 45); do\n"
        "  printf '<diagnostic-%03d>\\n' \"$number\" >&2\n"
        "done\n"
        "exit 23\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    env = {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "GIGBUDDY_HOME": str(tmp_path / "install"),
        "GIGBUDDY_REPO_URL": "https://example.test/gigbuddy.git",
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
    }

    result = subprocess.run(
        ["bash", str(Path(__file__).resolve().parents[1] / "scripts" / "install.sh"),
         "--skip-presets", "--skip-dry-inputs"],
        env=env, text=True, capture_output=True,
    )

    assert result.returncode != 0
    assert "GigBuddy install failed while running (exit 23):" in result.stderr
    assert "----- command output -----" in result.stderr
    assert "<diagnostic-001>" in result.stderr
    assert "<diagnostic-045>" in result.stderr
    assert "----- end command output -----" in result.stderr


def test_verbose_installer_reports_failed_status_and_shell_escaped_command(
        tmp_path):
    fake_bin = tmp_path / "fake bin"
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    fake_git.write_text(
        "#!/usr/bin/env bash\n"
        "printf 'verbose clone failed\\n' >&2\n"
        "exit 23\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    install_root = tmp_path / "install root"
    repo_ref = "release candidate"
    repo_url = "https://example.test/repo with spaces.git"

    result = subprocess.run(
        ["bash", str(INSTALLER),
         "--skip-presets", "--skip-dry-inputs", "--no-engine"],
        env={
            **os.environ,
            "GIGBUDDY_HOME": str(install_root),
            "GIGBUDDY_REF": repo_ref,
            "GIGBUDDY_REPO_URL": repo_url,
            "GIGBUDDY_VERBOSE": "1",
            "HOME": str(tmp_path / "home"),
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        },
        text=True,
        capture_output=True,
    )

    expected_command = _bash_quoted_command(
        "git", "clone", "--quiet", "--depth", "1", "--branch",
        repo_ref, repo_url, install_root,
    )
    assert result.returncode != 0
    assert (
        "GigBuddy install failed while running (exit 23):"
        f"{expected_command}\n"
    ) in result.stderr
    assert "GigBuddy install failed: command exited with status 23" in (
        result.stderr)
    assert not install_root.exists()


def test_user_installer_rolls_back_a_failed_new_clone(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    fake_git.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"${1:-}\" == clone ]]; then\n"
        "  for target; do :; done\n"
        "  mkdir -p \"$target\"\n"
        "  printf 'partial checkout\\n' >\"$target/partial\"\n"
        "  printf 'clone interrupted\\n' >&2\n"
        "  exit 23\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    install_root = tmp_path / "install"
    env = {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "GIGBUDDY_HOME": str(install_root),
        "GIGBUDDY_REPO_URL": "https://example.test/gigbuddy.git",
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
    }

    result = subprocess.run(
        ["bash", str(Path(__file__).resolve().parents[1] / "scripts" / "install.sh"),
         "--skip-presets", "--skip-dry-inputs"],
        env=env, text=True, capture_output=True,
    )

    assert result.returncode != 0
    assert "clone interrupted" in result.stderr
    assert not install_root.exists()


def test_user_installer_rolls_back_when_clone_metadata_cannot_be_written(
        tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    fake_git.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"${1:-}\" == clone ]]; then\n"
        "  for target; do :; done\n"
        "  mkdir -p \"$target/.gigbuddy-install\"\n"
        "  exit 0\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    install_root = tmp_path / "install"

    result = subprocess.run(
        ["bash", str(Path(__file__).resolve().parents[1] / "scripts" / "install.sh"),
         "--skip-presets", "--skip-dry-inputs", "--no-engine"],
        env={
            **os.environ,
            "GIGBUDDY_HOME": str(install_root),
            "GIGBUDDY_REPO_URL": "https://example.test/gigbuddy.git",
            "HOME": str(tmp_path / "home"),
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "gigbuddy-install" in result.stderr
    assert not install_root.exists()


def test_user_installer_preserves_an_existing_non_checkout_directory(tmp_path):
    install_root = tmp_path / "existing"
    install_root.mkdir()
    marker = install_root / "keep-me"
    marker.write_text("user data\n", encoding="utf-8")

    result = subprocess.run(
        ["bash", str(Path(__file__).resolve().parents[1] / "scripts" / "install.sh"),
         "--skip-presets", "--skip-dry-inputs", "--no-engine"],
        env={
            **os.environ,
            "GIGBUDDY_HOME": str(install_root),
            "HOME": str(tmp_path / "home"),
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "not a GigBuddy checkout" in result.stderr
    assert marker.read_text(encoding="utf-8") == "user data\n"


def test_user_installer_allows_automation_with_explicit_skip_presets(tmp_path):
    env, _, install_root, bin_dir, _ = _prepare_minimal_user_install(tmp_path)

    result = _run_minimal_user_install(
        env, "--skip-presets", "--skip-dry-inputs", "--no-engine")

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "continuing because --skip-presets was explicitly provided" in (
        result.stdout)
    assert (install_root / "bootstrap-args").read_text(
        encoding="utf-8").splitlines() == [
        "--skip-presets", "--skip-dry-inputs",
    ]
    assert "Device not configured" not in result.stderr
    command = bin_dir / "gigbuddy"
    assert command.is_file()
    assert not command.is_symlink()


def test_user_installer_requires_tty_without_explicit_skip_presets(tmp_path):
    env, _, install_root, _, fake_bin = _prepare_minimal_user_install(tmp_path)
    login_capture = tmp_path / "login-capture"
    fake_login_python = fake_bin / "login-python"
    fake_login_python.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >>\"$GIGBUDDY_TEST_LOGIN_CAPTURE\"\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake_login_python.chmod(0o755)
    env.update({
        "GIGBUDDY_LOGIN_PYTHON": str(fake_login_python),
        "GIGBUDDY_TEST_LOGIN_CAPTURE": str(login_capture),
    })

    result = _run_minimal_user_install(
        env, "--skip-dry-inputs", "--no-engine")

    assert result.returncode != 0
    assert "No interactive terminal is available" in result.stderr
    assert "interactive confirmation is required" in result.stderr
    assert "installation cancelled" not in result.stdout
    assert "ensure_tone3000_login.py" in login_capture.read_text(
        encoding="utf-8")
    assert not install_root.exists()


@pytest.mark.parametrize("legacy_wrapper_exists", [True, False])
def test_user_installer_replaces_internal_legacy_command_symlink(
        tmp_path, legacy_wrapper_exists):
    env, _, install_root, bin_dir, _ = _prepare_minimal_user_install(
        tmp_path, legacy_wrapper_exists=legacy_wrapper_exists)
    bin_dir.mkdir()
    command = bin_dir / "gigbuddy"
    legacy_target = install_root / ".venv" / "bin" / "gigbuddy"
    command.symlink_to(legacy_target)

    result = _run_minimal_user_install(
        env, "--skip-presets", "--skip-dry-inputs", "--no-engine")

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert command.is_file()
    assert not command.is_symlink()
    assert command.read_text(encoding="utf-8") == (
        "#!/usr/bin/env bash\n"
        f'exec "{install_root}/bin/gigbuddy" "$@"\n'
    )


def test_user_installer_replaces_owned_generated_wrapper(tmp_path):
    env, _, install_root, bin_dir, _ = _prepare_minimal_user_install(tmp_path)
    bin_dir.mkdir()
    command = bin_dir / "gigbuddy"
    command.write_text(
        "#!/usr/bin/env bash\n"
        f'exec "{install_root}/bin/gigbuddy" "$@"\n',
        encoding="utf-8",
    )

    result = _run_minimal_user_install(
        env, "--skip-presets", "--skip-dry-inputs", "--no-engine")

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert command.read_text(encoding="utf-8") == (
        "#!/usr/bin/env bash\n"
        f'exec "{install_root}/bin/gigbuddy" "$@"\n'
    )


def test_user_installer_preserves_regular_command_that_mentions_install_path(
        tmp_path):
    env, _, install_root, bin_dir, _ = _prepare_minimal_user_install(tmp_path)
    bin_dir.mkdir()
    command = bin_dir / "gigbuddy"
    marker = (
        "#!/usr/bin/env bash\n"
        f"# This external command documents {install_root}.\n"
        "exit 42\n"
    )
    command.write_text(marker, encoding="utf-8")
    command.chmod(0o755)

    result = _run_minimal_user_install(
        env, "--skip-presets", "--skip-dry-inputs", "--no-engine")

    assert result.returncode != 0
    assert "refusing to replace an existing command" in result.stderr
    assert command.read_text(encoding="utf-8") == marker
    assert not install_root.exists()


@pytest.mark.parametrize("external_target_exists", [True, False])
def test_user_installer_refuses_external_command_symlink_without_touching_target(
        tmp_path, external_target_exists):
    env, _, install_root, bin_dir, _ = _prepare_minimal_user_install(tmp_path)
    external_dir = tmp_path / "external"
    external_dir.mkdir()
    external_target = external_dir / "gigbuddy"
    marker = f"external target\n{install_root}\n"
    if external_target_exists:
        external_target.write_text(marker, encoding="utf-8")
    bin_dir.mkdir()
    command = bin_dir / "gigbuddy"
    command.symlink_to(external_target)

    result = _run_minimal_user_install(
        env, "--skip-presets", "--skip-dry-inputs", "--no-engine")

    assert result.returncode != 0
    assert "refusing to replace an existing command" in result.stderr
    assert command.is_symlink()
    assert os.readlink(command) == str(external_target)
    if external_target_exists:
        assert external_target.read_text(encoding="utf-8") == marker
    else:
        assert not external_target.exists()
    assert not install_root.exists()


@pytest.mark.parametrize("path_kind", ["parent-traversal", "parent-symlink"])
def test_user_installer_rejects_uncreated_data_path_inside_install(
        tmp_path, path_kind):
    env, _, install_root, _, _ = _prepare_minimal_user_install(tmp_path)
    if path_kind == "parent-traversal":
        decoy = tmp_path / "not-created"
        data_root = decoy / ".." / install_root.name / "nested-data"
    else:
        decoy = tmp_path / "install-link"
        decoy.symlink_to(install_root, target_is_directory=True)
        data_root = decoy / "nested-data"
    env["GIGBUDDY_DATA_HOME"] = str(data_root)

    result = _run_minimal_user_install(
        env, "--skip-presets", "--skip-dry-inputs", "--no-engine")

    assert result.returncode != 0
    assert "data path" in result.stderr
    assert not install_root.exists()
    if path_kind == "parent-traversal":
        assert not decoy.exists()
    else:
        assert decoy.is_symlink()


@pytest.mark.parametrize(
    "explicit_data_home,bootstrap_fails,preexisting_database",
    [(True, False, False), (False, False, False),
     (True, True, False), (True, True, True)],
)
def test_new_install_links_checkout_to_external_data_home_before_bootstrap(
        tmp_path, explicit_data_home, bootstrap_fails, preexisting_database):
    repo_root = Path(__file__).resolve().parents[1]
    fixture = tmp_path / "fixture"
    (fixture / "scripts").mkdir(parents=True)
    (fixture / ".venv" / "bin").mkdir(parents=True)
    (fixture / ".venv" / "bin" / "python").symlink_to(sys.executable)
    (fixture / "requirements.txt").write_text("", encoding="utf-8")
    (fixture / "pyproject.toml").write_text(
        '[project]\nname = "gigbuddy"\nversion = "1.2.3"\n',
        encoding="utf-8",
    )
    (fixture / "scripts" / "bootstrap.py").write_text(
        "import os\n"
        "from pathlib import Path\n"
        "root = Path(__file__).resolve().parents[1]\n"
        "data = root / 'data'\n"
        "if not data.is_symlink():\n"
        "    raise SystemExit('data link missing before bootstrap')\n"
        "resolved = data.resolve(strict=True)\n"
        "(resolved / 'bootstrap-marker').write_text(str(resolved))\n"
        "if os.environ.get('GIGBUDDY_TEST_MUTATE_DATABASE') == '1':\n"
        "    import sqlite3\n"
        "    with sqlite3.connect(resolved / 'gigbuddy.db') as conn:\n"
        "        conn.execute('PRAGMA user_version = 99')\n"
        "        conn.execute('DELETE FROM marker')\n"
        "        conn.execute(\"INSERT INTO marker VALUES ('new-data')\")\n"
        "        conn.execute('CREATE TABLE new_schema (value TEXT)')\n"
        "if os.environ.get('GIGBUDDY_TEST_BOOTSTRAP_FAIL') == '1':\n"
        "    raise SystemExit('bootstrap failed after creating data')\n",
        encoding="utf-8",
    )

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    fake_git.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"${1:-}\" == clone ]]; then\n"
        "  for target; do :; done\n"
        "  mkdir -p \"$target\"\n"
        "  cp -R \"$GIGBUDDY_TEST_FIXTURE\"/. \"$target\"/\n"
        "  mkdir -p \"$target/.git\"\n"
        "fi\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    fake_uv = fake_bin / "uv"
    fake_uv.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    fake_uv.chmod(0o755)

    install_root = tmp_path / "install"
    data_root = (tmp_path / "gigbuddy-data" if explicit_data_home
                 else Path(f"{install_root}-data"))
    if preexisting_database:
        data_root.mkdir()
        with sqlite3.connect(data_root / "gigbuddy.db") as conn:
            conn.execute("PRAGMA user_version = 7")
            conn.execute("CREATE TABLE marker (value TEXT NOT NULL)")
            conn.execute("INSERT INTO marker VALUES ('old-data')")
    env = {
        **os.environ,
        "GIGBUDDY_BIN_DIR": str(tmp_path / "global-bin"),
        "GIGBUDDY_HOME": str(install_root),
        "GIGBUDDY_TEST_FIXTURE": str(fixture),
        "GIGBUDDY_UV": str(fake_uv),
        "GIGBUDDY_VERBOSE": "1",
        "HOME": str(tmp_path / "home"),
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
    }
    if explicit_data_home:
        env["GIGBUDDY_DATA_HOME"] = str(data_root)
    if bootstrap_fails:
        env["GIGBUDDY_TEST_BOOTSTRAP_FAIL"] = "1"
    if preexisting_database:
        env["GIGBUDDY_TEST_MUTATE_DATABASE"] = "1"
    master, slave = pty.openpty()
    try:
        process = subprocess.Popen(
            ["bash", str(repo_root / "scripts" / "install.sh"),
             "--skip-presets", "--skip-dry-inputs", "--no-engine"],
            env=env, stdin=slave, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
        )
        os.close(slave)
        slave = -1
        os.write(master, b"\n")
        stdout, stderr = process.communicate(timeout=30)
    finally:
        os.close(master)
        if slave >= 0:
            os.close(slave)

    if bootstrap_fails:
        assert process.returncode != 0
        assert "bootstrap failed after creating data" in stderr
        assert not install_root.exists()
        if preexisting_database:
            assert data_root.exists()
            with sqlite3.connect(data_root / "gigbuddy.db") as conn:
                assert conn.execute("PRAGMA user_version").fetchone()[0] == 7
                assert conn.execute(
                    "SELECT value FROM marker").fetchone()[0] == "old-data"
                assert conn.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type='table' AND name='new_schema'"
                ).fetchone() is None
        else:
            assert not data_root.exists()
        return
    assert process.returncode == 0, (stdout, stderr)
    assert (install_root / "data").is_symlink()
    assert (install_root / "data").resolve() == data_root.resolve()
    assert (data_root / "bootstrap-marker").read_text(encoding="utf-8") == str(
        data_root.resolve())


def test_source_install_accepts_existing_relative_data_link(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    checkout = tmp_path / "checkout"
    data_root = tmp_path / "gigbuddy-data"
    (checkout / ".git").mkdir(parents=True)
    (checkout / "scripts").mkdir()
    (checkout / ".venv" / "bin").mkdir(parents=True)
    data_root.mkdir()
    (checkout / ".venv" / "bin" / "python").symlink_to(sys.executable)
    (checkout / "requirements.txt").write_text("", encoding="utf-8")
    (checkout / "pyproject.toml").write_text(
        '[project]\nname = "gigbuddy"\nversion = "1.2.3"\n',
        encoding="utf-8",
    )
    (checkout / "scripts" / "bootstrap.py").write_text(
        "from pathlib import Path\n"
        "data = Path(__file__).resolve().parents[1] / 'data'\n"
        "if data.resolve(strict=True) != Path(__import__('os').environ["
        "'GIGBUDDY_DATA_HOME']).resolve(strict=True):\n"
        "    raise SystemExit('unexpected data target')\n",
        encoding="utf-8",
    )
    relative_target = os.path.relpath(data_root, checkout)
    (checkout / "data").symlink_to(
        relative_target, target_is_directory=True)

    fake_uv = tmp_path / "uv"
    fake_uv.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    fake_uv.chmod(0o755)
    result = subprocess.run(
        ["bash", str(repo_root / "scripts" / "install.sh"),
         "--skip-presets", "--skip-dry-inputs", "--no-engine"],
        env={
            **os.environ,
            "GIGBUDDY_DATA_HOME": str(data_root),
            "GIGBUDDY_HOME": str(checkout),
            "GIGBUDDY_SOURCE_CHECKOUT": "1",
            "GIGBUDDY_UV": str(fake_uv),
            "GIGBUDDY_VERBOSE": "1",
            "HOME": str(tmp_path / "home"),
        },
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, (result.stdout, result.stderr)
    assert (checkout / "data").is_symlink()
    assert os.readlink(checkout / "data") == relative_target
    assert (checkout / "data").resolve() == data_root.resolve()


def test_existing_install_reuses_custom_data_link_without_redeclaring_home(
        tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    install_root = tmp_path / "install"
    data_root = tmp_path / "custom-data"
    (install_root / ".git").mkdir(parents=True)
    (install_root / "scripts").mkdir()
    (install_root / ".venv" / "bin").mkdir(parents=True)
    data_root.mkdir()
    (install_root / ".venv" / "bin" / "python").symlink_to(sys.executable)
    (install_root / ".gigbuddy-install").write_text(
        "GigBuddy\n", encoding="utf-8")
    (install_root / "requirements.txt").write_text("", encoding="utf-8")
    (install_root / "pyproject.toml").write_text(
        '[project]\nname = "gigbuddy"\nversion = "1.2.3"\n',
        encoding="utf-8",
    )
    (install_root / "scripts" / "bootstrap.py").write_text(
        "from pathlib import Path\n"
        "data = Path(__file__).resolve().parents[1] / 'data'\n"
        "if data.resolve(strict=True) != Path(__import__('os').environ[\n"
        "        'GIGBUDDY_TEST_DATA_ROOT']).resolve(strict=True):\n"
        "    raise SystemExit('existing custom data link was replaced')\n",
        encoding="utf-8",
    )
    relative_target = os.path.relpath(data_root, install_root)
    (install_root / "data").symlink_to(
        relative_target, target_is_directory=True)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    fake_git.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \" $* \" == *\" rev-parse HEAD \"* ]]; then\n"
        "  printf 'old-head\\n'\n"
        "fi\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    fake_uv = fake_bin / "uv"
    fake_uv.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    fake_uv.chmod(0o755)

    master, slave = pty.openpty()
    try:
        process = subprocess.Popen(
            ["bash", str(repo_root / "scripts" / "install.sh"),
             "--skip-presets", "--skip-dry-inputs", "--no-engine"],
            env={
                **os.environ,
                "GIGBUDDY_BIN_DIR": str(tmp_path / "global-bin"),
                "GIGBUDDY_HOME": str(install_root),
                "GIGBUDDY_TEST_DATA_ROOT": str(data_root),
                "GIGBUDDY_UV": str(fake_uv),
                "GIGBUDDY_VERBOSE": "1",
                "HOME": str(tmp_path / "home"),
                "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            },
            stdin=slave,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        os.close(slave)
        slave = -1
        os.write(master, b"\n")
        stdout, stderr = process.communicate(timeout=30)
    finally:
        os.close(master)
        if slave >= 0:
            os.close(slave)

    assert process.returncode == 0, (stdout, stderr)
    assert os.readlink(install_root / "data") == relative_target
    assert (install_root / "data").resolve() == data_root.resolve()


@pytest.mark.parametrize("existing_database", [True, False])
def test_existing_install_failure_restores_checkout_and_database(
        tmp_path, existing_database):
    """A failed upgrade must not leave old code reading a migrated database."""
    repo_root = Path(__file__).resolve().parents[1]
    remote = tmp_path / "remote.git"
    source = tmp_path / "source"
    install_root = tmp_path / "install"

    def git(*args, cwd=None):
        return subprocess.run(
            ["git", *map(str, args)], cwd=cwd, check=True, text=True,
            capture_output=True,
        )

    git("init", "--bare", remote)
    git("init", source)
    git("-C", source, "config", "user.email", "test@example.com")
    git("-C", source, "config", "user.name", "GigBuddy test")
    (source / "scripts").mkdir()
    (source / "pyproject.toml").write_text(
        '[project]\nname = "gigbuddy"\nversion = "1.2.2"\n',
        encoding="utf-8",
    )
    (source / "requirements.txt").write_text("", encoding="utf-8")
    (source / "scripts" / "bootstrap.py").write_text(
        "print('old bootstrap')\n", encoding="utf-8")
    git("-C", source, "add", ".")
    git("-C", source, "commit", "-m", "old install")
    old_head = git("-C", source, "rev-parse", "HEAD").stdout.strip()
    git("-C", source, "remote", "add", "origin", remote)
    git("-C", source, "push", "origin", "HEAD:refs/heads/main")
    git("clone", remote, install_root)
    git("-C", install_root, "checkout", "--detach", old_head)

    data_dir = install_root / "data"
    data_dir.mkdir()
    (data_dir / "user-preset.json").write_text(
        '{"name":"keep"}\n', encoding="utf-8")
    external_data = tmp_path / "external-data"
    database = data_dir / "gigbuddy.db"
    if existing_database:
        with sqlite3.connect(database) as conn:
            conn.execute("PRAGMA user_version = 7")
            conn.execute("CREATE TABLE marker (value TEXT NOT NULL)")
            conn.execute("INSERT INTO marker VALUES ('old-data')")

    venv_bin = install_root / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").symlink_to(sys.executable)
    eigen_fft = (install_root / "third_party" / "NeuralAudio" / "deps" /
                 "RTNeural" / "modules" / "Eigen" / "unsupported" /
                 "Eigen" / "FFT")
    eigen_fft.parent.mkdir(parents=True)
    eigen_fft.write_text("fixture\n", encoding="utf-8")
    portaudio = install_root / ".local" / "lib" / "libportaudio.2.dylib"
    portaudio.parent.mkdir(parents=True)
    portaudio.write_text("fixture\n", encoding="utf-8")
    (install_root / ".gigbuddy-install").write_text(
        "GigBuddy\n", encoding="utf-8")

    (source / "pyproject.toml").write_text(
        '[project]\nname = "gigbuddy"\nversion = "1.2.3"\n',
        encoding="utf-8",
    )
    (source / "scripts" / "bootstrap.py").write_text(
        "import sqlite3\n"
        "from pathlib import Path\n"
        "database = Path(__file__).resolve().parents[1] / 'data' / 'gigbuddy.db'\n"
        "with sqlite3.connect(database) as conn:\n"
        "    conn.execute('PRAGMA user_version = 99')\n"
        "    conn.execute('CREATE TABLE IF NOT EXISTS marker (value TEXT NOT NULL)')\n"
        "    conn.execute('DELETE FROM marker')\n"
        "    conn.execute(\"INSERT INTO marker VALUES ('new-data')\")\n"
        "    conn.execute('CREATE TABLE new_schema (value TEXT)')\n",
        encoding="utf-8",
    )
    cpp = source / "cpp"
    cpp.mkdir()
    build = cpp / "build.sh"
    build.write_text(
        "#!/usr/bin/env bash\nprintf 'engine build failed\\n' >&2\nexit 23\n",
        encoding="utf-8",
    )
    build.chmod(0o755)
    git("-C", source, "add", ".")
    git("-C", source, "commit", "-m", "migrating upgrade")
    git("-C", source, "tag", "v-next")
    git("-C", source, "push", "origin", "HEAD:refs/heads/main", "--tags")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_uname = fake_bin / "uname"
    fake_uname.write_text(
        "#!/usr/bin/env bash\nprintf 'Darwin\\n'\n", encoding="utf-8")
    fake_uname.chmod(0o755)
    fake_uv = fake_bin / "uv"
    fake_uv.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    fake_uv.chmod(0o755)
    env = {
        **os.environ,
        "GIGBUDDY_BIN_DIR": str(tmp_path / "global-bin"),
        "GIGBUDDY_DATA_HOME": str(external_data),
        "GIGBUDDY_HOME": str(install_root),
        "GIGBUDDY_REF": "v-next",
        "GIGBUDDY_UV": str(fake_uv),
        "GIGBUDDY_VERBOSE": "1",
        "HOME": str(tmp_path / "home"),
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
    }
    master, slave = pty.openpty()
    try:
        process = subprocess.Popen(
            ["bash", str(repo_root / "scripts" / "install.sh"),
             "--skip-presets", "--skip-dry-inputs"],
            env=env, stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
        )
        os.close(slave)
        slave = -1
        os.write(master, b"\n")
        stdout, stderr = process.communicate(timeout=30)
    finally:
        os.close(master)
        if slave >= 0:
            os.close(slave)

    assert process.returncode != 0, stdout
    assert "engine build failed" in stderr
    assert git("-C", install_root, "rev-parse", "HEAD").stdout.strip() == old_head
    assert data_dir.is_symlink()
    assert data_dir.resolve() == external_data.resolve()
    assert (external_data / "user-preset.json").read_text(
        encoding="utf-8") == '{"name":"keep"}\n'
    if existing_database:
        with sqlite3.connect(database) as conn:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 7
            assert conn.execute("SELECT value FROM marker").fetchone()[0] == "old-data"
            assert conn.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type='table' AND name='new_schema'"
            ).fetchone() is None
    else:
        assert not database.exists()
        assert not Path(f"{database}-wal").exists()
        assert not Path(f"{database}-shm").exists()


@pytest.mark.parametrize("failure_phase", ["header", "engine"])
def test_existing_install_engine_dependency_failure_rolls_back(
        tmp_path, failure_phase):
    """Eigen/header failures must restore the checkout and dependency tree."""
    repo_root = Path(__file__).resolve().parents[1]
    remote = tmp_path / "remote.git"
    source = tmp_path / "source"
    install_root = tmp_path / "install"

    def git(*args, cwd=None):
        return subprocess.run(
            ["git", *map(str, args)], cwd=cwd, check=True, text=True,
            capture_output=True,
        )

    git("init", "--bare", remote)
    git("init", source)
    git("-C", source, "config", "user.email", "test@example.com")
    git("-C", source, "config", "user.name", "GigBuddy test")
    (source / "pyproject.toml").write_text(
        '[project]\nname = "gigbuddy"\nversion = "1.2.2"\n',
        encoding="utf-8",
    )
    (source / "requirements.txt").write_text("", encoding="utf-8")
    (source / "scripts").mkdir()
    (source / "scripts" / "bootstrap.py").write_text(
        "print('bootstrap')\n", encoding="utf-8")
    git("-C", source, "add", ".")
    git("-C", source, "commit", "-m", "old install")
    old_head = git("-C", source, "rev-parse", "HEAD").stdout.strip()
    git("-C", source, "remote", "add", "origin", remote)
    git("-C", source, "push", "origin", "HEAD:refs/heads/main")
    git("clone", remote, install_root)
    git("-C", install_root, "checkout", "--detach", old_head)

    (install_root / "data").mkdir()
    (install_root / ".venv" / "bin").mkdir(parents=True)
    (install_root / ".venv" / "bin" / "python").symlink_to(sys.executable)
    (install_root / ".gigbuddy-install").write_text(
        "GigBuddy\n", encoding="utf-8")
    portaudio = install_root / ".local" / "lib" / "libportaudio.2.dylib"
    portaudio.parent.mkdir(parents=True)
    portaudio.write_text("fixture\n", encoding="utf-8")

    (source / "pyproject.toml").write_text(
        '[project]\nname = "gigbuddy"\nversion = "1.2.3"\n',
        encoding="utf-8",
    )
    (source / "cpp").mkdir()
    build = source / "cpp" / "build.sh"
    build.write_text(
        "#!/usr/bin/env bash\nprintf 'engine build failed\\n' >&2\nexit 23\n",
        encoding="utf-8",
    )
    build.chmod(0o755)
    git("-C", source, "add", ".")
    git("-C", source, "commit", "-m", "engine upgrade")
    git("-C", source, "tag", "v-next")
    git("-C", source, "push", "origin", "HEAD:refs/heads/main", "--tags")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    real_git = shutil.which("git")
    assert real_git is not None
    clone_log = tmp_path / "neural-audio-clone.log"
    (fake_bin / "git").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "if [[ ${1:-} == clone "
        "&& \"$*\" == *mikeoliphant/NeuralAudio* ]]; then\n"
        "  target=\"${@: -1}\"\n"
        "  mkdir -p \"$target/deps/RTNeural/modules/Eigen\" "
        "\"$target/NeuralAudio\"\n"
        "  printf 'old Eigen\\n' >\"$target/deps/RTNeural/modules/Eigen/keep.h\"\n"
        "  printf 'Eigen::placeholders::lastN\\n' "
        ">\"$target/NeuralAudio/LSTM.h\"\n"
        "  if [[ ${GIGBUDDY_TEST_MISSING_HEADER:-0} != 1 ]]; then\n"
        "    printf 'Eigen::placeholders::lastN\\n' "
        ">\"$target/NeuralAudio/LSTMDynamic.h\"\n"
        "  fi\n"
        "  printf 'cloned\\n' >\"$GIGBUDDY_TEST_CLONE_LOG\"\n"
        "  exit 0\n"
        "fi\n"
        "if [[ ${1:-} == -C "
        "&& ${2:-} == */third_party/NeuralAudio "
        "&& ${3:-} == checkout ]]; then\n"
        "  exit 0\n"
        "fi\n"
        f"exec {shlex.quote(real_git)} \"$@\"\n",
        encoding="utf-8",
    )
    (fake_bin / "git").chmod(0o755)
    (fake_bin / "uname").write_text(
        "#!/usr/bin/env bash\nprintf 'Darwin\\n'\n", encoding="utf-8")
    (fake_bin / "uname").chmod(0o755)
    (fake_bin / "uv").write_text(
        "#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (fake_bin / "uv").chmod(0o755)
    (fake_bin / "curl").write_text(
        "#!/usr/bin/env bash\n"
        "while [[ $# -gt 0 ]]; do\n"
        "  if [[ $1 == -o ]]; then output=$2; shift 2; else shift; fi\n"
        "done\n"
        ": >\"$output\"\n",
        encoding="utf-8",
    )
    (fake_bin / "curl").chmod(0o755)
    (fake_bin / "tar").write_text(
        "#!/usr/bin/env bash\n"
        "while [[ $# -gt 0 ]]; do\n"
        "  if [[ $1 == -C ]]; then destination=$2; shift 2; else shift; fi\n"
        "done\n"
        "mkdir -p \"$destination/eigen-3.4.0/unsupported/Eigen\"\n"
        ": >\"$destination/eigen-3.4.0/unsupported/Eigen/FFT\"\n",
        encoding="utf-8",
    )
    (fake_bin / "tar").chmod(0o755)

    master, slave = pty.openpty()
    try:
        process = subprocess.Popen(
            ["bash", str(repo_root / "scripts" / "install.sh"),
             "--skip-presets", "--skip-dry-inputs"],
            env={
                **os.environ,
                "GIGBUDDY_BIN_DIR": str(tmp_path / "global-bin"),
                "GIGBUDDY_HOME": str(install_root),
                "GIGBUDDY_REF": "v-next",
                "GIGBUDDY_UV": str(fake_bin / "uv"),
                "GIGBUDDY_VERBOSE": "1",
                "GIGBUDDY_TEST_CLONE_LOG": str(clone_log),
                "GIGBUDDY_TEST_MISSING_HEADER": (
                    "1" if failure_phase == "header" else "0"),
                "HOME": str(tmp_path / "home"),
                "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            },
            stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
        )
        os.close(slave)
        slave = -1
        os.write(master, b"\n")
        stdout, stderr = process.communicate(timeout=30)
    finally:
        os.close(master)
        if slave >= 0:
            os.close(slave)

    assert process.returncode != 0, stdout
    assert clone_log.read_text(encoding="utf-8") == "cloned\n"
    if failure_phase == "header":
        assert "LSTMDynamic.h" in stderr
    else:
        assert "engine build failed" in stderr
    assert git("-C", install_root, "rev-parse", "HEAD").stdout.strip() == old_head
    assert not (install_root / "third_party").exists()
