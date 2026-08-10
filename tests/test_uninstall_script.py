"""Safety checks for the standalone install/uninstall scripts."""

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "uninstall.sh"


def _fake_install(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "home"
    install_root = home / ".local" / "share" / "gigbuddy"
    bin_dir = home / ".local" / "bin"
    (install_root / ".git").mkdir(parents=True)
    (install_root / "data" / "tones").mkdir(parents=True)
    (install_root / ".venv" / "bin").mkdir(parents=True)
    bin_dir.mkdir(parents=True)
    (install_root / ".gigbuddy-install").write_text("GigBuddy\n")
    (install_root / "data" / "gigbuddy.db").write_text("db")
    (install_root / "app.py").write_text("app")
    token_file = home / ".config" / "gigbuddy" / "tone3000_tokens.json"
    token_file.parent.mkdir(parents=True)
    token_file.write_text('{"access_token":"test"}\n')
    (bin_dir / "gigbuddy").symlink_to(install_root / ".venv" / "bin" / "gigbuddy")
    (bin_dir / "gigbuddy-tui").symlink_to(install_root / ".venv" / "bin" / "gigbuddy-tui")
    return home, install_root


def _run(home: Path, install_root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "HOME": str(home), "GIGBUDDY_HOME": str(install_root),
           "GIGBUDDY_BIN_DIR": str(home / ".local" / "bin")}
    return subprocess.run(["bash", str(SCRIPT), *args], text=True,
                          capture_output=True, env=env, check=False)


def test_uninstall_keep_data_removes_runtime_only(tmp_path):
    home, install_root = _fake_install(tmp_path)

    result = _run(home, install_root, ["--yes", "--keep-data"])

    assert result.returncode == 0, result.stderr
    assert (install_root / "data" / "gigbuddy.db").exists()
    assert (install_root / ".gigbuddy-install").exists()
    assert not (install_root / "app.py").exists()
    assert not (home / ".local" / "bin" / "gigbuddy").is_symlink()
    assert not (home / ".config" / "gigbuddy" / "tone3000_tokens.json").exists()
    assert "local data kept" in result.stdout


def test_uninstall_yes_removes_local_data(tmp_path):
    home, install_root = _fake_install(tmp_path)

    result = _run(home, install_root, ["--yes"])

    assert result.returncode == 0, result.stderr
    assert not install_root.exists()
    assert not (home / ".local" / "bin" / "gigbuddy").exists()
    assert not (home / ".config" / "gigbuddy" / "tone3000_tokens.json").exists()
    assert "including local data" in result.stdout


def test_uninstall_refuses_unrecognized_directory(tmp_path):
    home = tmp_path / "home"
    install_root = home / ".local" / "share" / "gigbuddy"
    install_root.mkdir(parents=True)
    sentinel = install_root / "keep.txt"
    sentinel.write_text("keep")

    result = _run(home, install_root, ["--yes"])

    assert result.returncode != 0
    assert sentinel.exists()
    assert "not a recognized GigBuddy install" in result.stderr


def test_uninstall_rejects_parent_traversal(tmp_path):
    home = tmp_path / "home"
    install_root = home / ".."

    result = _run(home, install_root, ["--yes"])

    assert result.returncode != 0
    assert "invalid install path" in result.stderr
