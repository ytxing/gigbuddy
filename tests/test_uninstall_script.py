"""Safety checks for the standalone install/uninstall scripts."""

import os
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "uninstall.sh"


def _fake_install(
        tmp_path: Path, *, external_data: bool = True,
        relative_data_link: bool = False,
) -> tuple[Path, Path, Path]:
    home = tmp_path / "home"
    install_root = home / ".local" / "share" / "gigbuddy"
    bin_dir = home / ".local" / "bin"
    (install_root / ".git").mkdir(parents=True)
    (install_root / ".venv" / "bin").mkdir(parents=True)
    bin_dir.mkdir(parents=True)
    (install_root / ".gigbuddy-install").write_text("GigBuddy\n")
    if external_data:
        data_root = home / ".local" / "share" / "gigbuddy-data"
        (data_root / "tones").mkdir(parents=True)
        target = data_root
        if relative_data_link:
            target = Path(os.path.relpath(data_root, install_root))
        (install_root / "data").symlink_to(target, target_is_directory=True)
    else:
        data_root = install_root / "data"
        (data_root / "tones").mkdir(parents=True)
    (data_root / "gigbuddy.db").write_text("db")
    (install_root / "app.py").write_text("app")
    token_file = home / ".config" / "gigbuddy" / "tone3000_tokens.json"
    token_file.parent.mkdir(parents=True)
    token_file.write_text('{"access_token":"test"}\n')
    (bin_dir / "gigbuddy").symlink_to(install_root / ".venv" / "bin" / "gigbuddy")
    (bin_dir / "gigbuddy-tui").symlink_to(install_root / ".venv" / "bin" / "gigbuddy-tui")
    return home, install_root, data_root


def _run(
        home: Path, install_root: Path, args: list[str], *,
        extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "HOME": str(home), "GIGBUDDY_HOME": str(install_root),
           "GIGBUDDY_BIN_DIR": str(home / ".local" / "bin")}
    env.update(extra_env or {})
    return subprocess.run(["bash", str(SCRIPT), *args], text=True,
                          capture_output=True, env=env, check=False)


def test_uninstall_help_returns_zero_without_an_install(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    install_root = home / ".local" / "share" / "gigbuddy"

    for option in ("-h", "--help"):
        result = _run(home, install_root, [option])

        assert result.returncode == 0, result.stderr
        assert "Usage: uninstall.sh [OPTIONS]" in result.stdout
        assert "--keep-data" in result.stdout
        assert result.stderr == ""
        assert not install_root.exists()


def test_uninstall_unknown_option_points_to_help_without_changes(tmp_path):
    home, install_root, data_root = _fake_install(tmp_path)

    result = _run(home, install_root, ["--unknown"])

    assert result.returncode != 0
    assert "unknown option: --unknown" in result.stderr
    assert "--help" in result.stderr
    assert (install_root / "app.py").exists()
    assert (data_root / "gigbuddy.db").exists()


def test_uninstall_keep_data_removes_runtime_only(tmp_path):
    home, install_root, data_root = _fake_install(tmp_path)

    result = _run(home, install_root, ["--yes", "--keep-data"])

    assert result.returncode == 0, result.stderr
    assert not install_root.exists()
    assert (data_root / "gigbuddy.db").exists()
    assert not (home / ".local" / "bin" / "gigbuddy").is_symlink()
    assert not (home / ".config" / "gigbuddy" / "tone3000_tokens.json").exists()
    assert "local data kept" in result.stdout


def test_uninstall_removes_wrapper_written_through_parent_symlink(tmp_path):
    home = tmp_path / "home"
    real_parent = tmp_path / "real-parent"
    alias_parent = tmp_path / "alias-parent"
    install_root = alias_parent / "gigbuddy"
    bin_dir = tmp_path / "bin"
    home.mkdir()
    real_parent.mkdir()
    alias_parent.symlink_to(real_parent, target_is_directory=True)
    install_root.mkdir()
    bin_dir.mkdir()
    (install_root / ".gigbuddy-install").write_text("GigBuddy\n")
    wrapper = bin_dir / "gigbuddy"
    wrapper.write_text(
        f"#!/usr/bin/env bash\nexec {install_root}/bin/gigbuddy \"$@\"\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)

    result = _run(
        home, install_root, ["--yes"],
        extra_env={"GIGBUDDY_BIN_DIR": str(bin_dir)},
    )

    assert result.returncode == 0, result.stderr
    assert not install_root.exists()
    assert not wrapper.exists()


def test_uninstall_removes_relative_internal_command_symlink(tmp_path):
    home, install_root, data_root = _fake_install(tmp_path)
    command = home / ".local" / "bin" / "gigbuddy"
    command.unlink()
    command.symlink_to(os.path.relpath(
        install_root / ".venv" / "bin" / "gigbuddy", command.parent))
    external_target = tmp_path / "external" / "gigbuddy-tui"
    external_target.parent.mkdir()
    external_target.write_text("keep", encoding="utf-8")
    external_command = home / ".local" / "bin" / "gigbuddy-tui"
    external_command.unlink()
    external_link = os.path.relpath(external_target, external_command.parent)
    external_command.symlink_to(external_link)

    result = _run(home, install_root, ["--yes", "--keep-data"])

    assert result.returncode == 0, result.stderr
    assert not install_root.exists()
    assert not command.is_symlink()
    assert external_command.is_symlink()
    assert os.readlink(external_command) == external_link
    assert external_target.read_text(encoding="utf-8") == "keep"
    assert (data_root / "gigbuddy.db").exists()


def test_uninstall_preserves_broken_command_with_unresolved_parent_traversal(
        tmp_path):
    home, install_root, data_root = _fake_install(tmp_path)
    command = home / ".local" / "bin" / "gigbuddy"
    command.unlink()
    broken_target = install_root / "missing" / ".." / "external" / "gigbuddy"
    command.symlink_to(broken_target)

    result = _run(home, install_root, ["--yes", "--keep-data"])

    assert result.returncode == 0, result.stderr
    assert not install_root.exists()
    assert command.is_symlink()
    assert os.readlink(command) == str(broken_target)
    assert (data_root / "gigbuddy.db").exists()


def test_uninstall_preserves_custom_wrapper_that_mentions_install_path(
        tmp_path):
    home, install_root, data_root = _fake_install(tmp_path)
    command = home / ".local" / "bin" / "gigbuddy"
    command.unlink()
    marker = (
        "#!/usr/bin/env bash\n"
        "# User-owned setup belongs here.\n"
        f'exec "{install_root}/bin/gigbuddy" "$@"\n'
    )
    command.write_text(marker, encoding="utf-8")
    command.chmod(0o755)

    result = _run(home, install_root, ["--yes", "--keep-data"])

    assert result.returncode == 0, result.stderr
    assert not install_root.exists()
    assert command.read_text(encoding="utf-8") == marker
    assert (data_root / "gigbuddy.db").exists()


def test_uninstall_resolves_wrapper_parent_alias_before_removing(tmp_path):
    home = tmp_path / "home"
    real_parent = tmp_path / "real-parent"
    alias_parent = tmp_path / "alias-parent"
    install_root = real_parent / "gigbuddy"
    bin_dir = tmp_path / "bin"
    home.mkdir()
    real_parent.mkdir()
    alias_parent.symlink_to(real_parent, target_is_directory=True)
    install_root.mkdir()
    bin_dir.mkdir()
    (install_root / ".gigbuddy-install").write_text("GigBuddy\n")
    wrapper = bin_dir / "gigbuddy"
    wrapper.write_text(
        f"#!/usr/bin/env bash\nexec {alias_parent}/gigbuddy/bin/gigbuddy \"$@\"\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)

    result = _run(
        home, install_root, ["--yes"],
        extra_env={"GIGBUDDY_BIN_DIR": str(bin_dir)},
    )

    assert result.returncode == 0, result.stderr
    assert not install_root.exists()
    assert not wrapper.exists()


def test_uninstall_yes_removes_local_data(tmp_path):
    home, install_root, data_root = _fake_install(tmp_path)

    result = _run(home, install_root, ["--yes"])

    assert result.returncode == 0, result.stderr
    assert not install_root.exists()
    assert not data_root.exists()
    assert not (home / ".local" / "bin" / "gigbuddy").exists()
    assert not (home / ".config" / "gigbuddy" / "tone3000_tokens.json").exists()
    assert "including local data" in result.stdout


def test_uninstall_keep_data_migrates_legacy_embedded_data(tmp_path):
    home, install_root, embedded_data = _fake_install(
        tmp_path, external_data=False)
    migrated_data = Path(f"{install_root}-data")

    result = _run(home, install_root, ["--yes", "--keep-data"])

    assert result.returncode == 0, result.stderr
    assert not install_root.exists()
    assert not embedded_data.exists()
    assert (migrated_data / "gigbuddy.db").read_text() == "db"
    assert f"local data kept at {migrated_data}" in result.stdout


def test_uninstall_refuses_to_merge_legacy_data_into_nonempty_target(tmp_path):
    home, install_root, embedded_data = _fake_install(
        tmp_path, external_data=False)
    migration_target = Path(f"{install_root}-data")
    migration_target.mkdir()
    sentinel = migration_target / "existing.txt"
    sentinel.write_text("keep", encoding="utf-8")

    result = _run(home, install_root, ["--yes", "--keep-data"])

    assert result.returncode != 0
    assert (embedded_data / "gigbuddy.db").read_text() == "db"
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert (install_root / "app.py").exists()
    assert "refusing to merge" in result.stderr


def test_uninstall_resolves_relative_external_data_link(tmp_path):
    home, install_root, data_root = _fake_install(
        tmp_path, relative_data_link=True)

    result = _run(home, install_root, ["--yes", "--keep-data"])

    assert result.returncode == 0, result.stderr
    assert not install_root.exists()
    assert (data_root / "gigbuddy.db").exists()


def test_uninstall_refuses_configured_data_path_that_disagrees_with_link(
        tmp_path):
    home, install_root, data_root = _fake_install(tmp_path)
    other_data = tmp_path / "other-data"
    other_data.mkdir()

    result = _run(
        home, install_root, ["--yes"],
        extra_env={"GIGBUDDY_DATA_HOME": str(other_data)},
    )

    assert result.returncode != 0
    assert (install_root / "app.py").exists()
    assert (data_root / "gigbuddy.db").exists()
    assert other_data.exists()
    assert (home / ".local" / "bin" / "gigbuddy").is_symlink()
    assert "does not match installed data link" in result.stderr


def test_uninstall_removes_stale_wrappers_when_checkout_is_already_missing(
        tmp_path):
    home, install_root, data_root = _fake_install(tmp_path)
    shutil.rmtree(install_root)

    result = _run(home, install_root, ["--yes", "--keep-data"])

    assert result.returncode == 0, result.stderr
    assert (data_root / "gigbuddy.db").exists()
    assert not (home / ".local" / "bin" / "gigbuddy").exists()
    assert not (home / ".local" / "bin" / "gigbuddy-tui").exists()
    assert "local data kept" in result.stdout


def test_uninstall_refuses_broad_external_data_target(tmp_path):
    home, install_root, data_root = _fake_install(tmp_path)
    (install_root / "data").unlink()
    (install_root / "data").symlink_to(home, target_is_directory=True)
    sentinel = home / "keep.txt"
    sentinel.write_text("keep")

    result = _run(home, install_root, ["--yes"])

    assert result.returncode != 0
    assert install_root.exists()
    assert data_root.exists()
    assert sentinel.read_text() == "keep"
    assert "refusing to remove a broad data path" in result.stderr


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
