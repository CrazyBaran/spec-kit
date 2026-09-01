"""Tests for bundled-extension content staleness detection (#4345).

The registry's manifest_hash only covers extension.yml, so bundled
extension content that changed upstream without a version bump used to be
undetectable: `specify extension update` compared semver only and reported
"Up to date" forever. These tests cover the content hash that closes that
gap and the update command's stale-content reporting.
"""

from __future__ import annotations

import os

import pytest
import yaml
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from specify_cli import app
from specify_cli.extensions import (
    ExtensionCatalog,
    ExtensionManager,
    compute_extension_content_hash,
)


def _create_extension_source(
    base_dir: Path, name: str = "test-ext", version: str = "1.0.0"
) -> Path:
    """Create a minimal installable extension source directory."""
    ext_dir = base_dir / name
    ext_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "schema_version": "1.0",
        "extension": {
            "id": "test-ext",
            "name": "Test Extension",
            "version": version,
            "description": "A test extension",
        },
        "requires": {"speckit_version": ">=0.1.0"},
        "provides": {
            "commands": [
                {
                    "name": "speckit.test-ext.hello",
                    "file": "commands/hello.md",
                    "description": "Test command",
                }
            ]
        },
    }

    (ext_dir / "extension.yml").write_text(yaml.dump(manifest, sort_keys=False))
    commands_dir = ext_dir / "commands"
    commands_dir.mkdir(exist_ok=True)
    (commands_dir / "hello.md").write_text("---\ndescription: Test\n---\n\n$ARGUMENTS\n")
    scripts_dir = ext_dir / "scripts"
    scripts_dir.mkdir(exist_ok=True)
    (scripts_dir / "run.sh").write_text("#!/bin/sh\necho hello\n")
    (ext_dir / "test-ext-config.yml").write_text("setting: default\n")
    return ext_dir


def _make_project(tmp_path: Path) -> Path:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / ".specify").mkdir()
    (project_dir / ".claude" / "skills").mkdir(parents=True)
    return project_dir


BUNDLED_CATALOG_INFO = {
    "id": "test-ext",
    "name": "Test Extension",
    "version": "1.0.0",
    "bundled": True,
    "_install_allowed": True,
}


class TestComputeExtensionContentHash:
    def test_deterministic(self, tmp_path):
        ext_dir = _create_extension_source(tmp_path)
        assert compute_extension_content_hash(ext_dir) == compute_extension_content_hash(
            ext_dir
        )

    def test_identical_copies_hash_equal(self, tmp_path):
        a = _create_extension_source(tmp_path / "a")
        b = _create_extension_source(tmp_path / "b")
        assert compute_extension_content_hash(a) == compute_extension_content_hash(b)

    def test_content_change_changes_hash(self, tmp_path):
        ext_dir = _create_extension_source(tmp_path)
        before = compute_extension_content_hash(ext_dir)
        (ext_dir / "scripts" / "run.sh").write_text("#!/bin/sh\necho fixed\n")
        assert compute_extension_content_hash(ext_dir) != before

    def test_new_file_changes_hash(self, tmp_path):
        ext_dir = _create_extension_source(tmp_path)
        before = compute_extension_content_hash(ext_dir)
        (ext_dir / "scripts" / "extra.sh").write_text("#!/bin/sh\n")
        assert compute_extension_content_hash(ext_dir) != before

    def test_user_config_files_excluded(self, tmp_path):
        ext_dir = _create_extension_source(tmp_path)
        before = compute_extension_content_hash(ext_dir)
        (ext_dir / "test-ext-config.yml").write_text("setting: user-edited\n")
        (ext_dir / "test-ext-config.local.yml").write_text("local: override\n")
        assert compute_extension_content_hash(ext_dir) == before

    def test_nested_config_suffixed_files_are_hashed(self, tmp_path):
        """Only top-level config files are preserved across installs
        (_target_follows_preserved_convention); a nested *-config.yml is
        overwritten by installation, so its changes are real staleness."""
        ext_dir = _create_extension_source(tmp_path)
        templates = ext_dir / "templates"
        templates.mkdir()
        (templates / "scaffold-config.yml").write_text("shipped: v1\n")
        before = compute_extension_content_hash(ext_dir)
        (templates / "scaffold-config.yml").write_text("shipped: v2\n")
        assert compute_extension_content_hash(ext_dir) != before

    def test_extensionignore_and_ignored_files_excluded(self, tmp_path):
        ext_dir = _create_extension_source(tmp_path)
        before = compute_extension_content_hash(ext_dir)
        (ext_dir / ".extensionignore").write_text("*.log\n")
        (ext_dir / "debug.log").write_text("noise\n")
        assert compute_extension_content_hash(ext_dir) == before

    def test_symlinks_are_never_followed(self, tmp_path):
        """A symlink inside the extension dir must not pull external bytes
        into the hash (never follow symlinks out of the project root)."""
        ext_dir = _create_extension_source(tmp_path)
        outside = tmp_path / "outside.txt"
        outside.write_text("external bytes\n")
        before = compute_extension_content_hash(ext_dir)
        try:
            (ext_dir / "scripts" / "link.txt").symlink_to(outside)
        except OSError:
            pytest.skip("symlink creation requires privileges on this platform")
        assert compute_extension_content_hash(ext_dir) == before

    def test_matches_between_source_and_installation(self, tmp_path):
        """An install made from a source dir hashes identically to it."""
        project_dir = _make_project(tmp_path)
        source = _create_extension_source(tmp_path)
        manager = ExtensionManager(project_dir)
        manager.install_from_directory(source, "0.1.0")

        installed_dir = project_dir / ".specify" / "extensions" / "test-ext"
        # User edits to the preserved config file must not affect parity.
        (installed_dir / "test-ext-config.yml").write_text("setting: user-edited\n")
        assert compute_extension_content_hash(
            installed_dir
        ) == compute_extension_content_hash(source)


class TestArchiveExtensionDirectory:
    def test_archive_contains_regular_files_only(self, tmp_path):
        import zipfile

        from specify_cli.extensions._commands import _archive_extension_directory

        ext_dir = _create_extension_source(tmp_path)
        archive_path = _archive_extension_directory(ext_dir)
        try:
            with zipfile.ZipFile(archive_path) as zf:
                names = set(zf.namelist())
            assert "extension.yml" in names
            assert "commands/hello.md" in names
        finally:
            archive_path.unlink()

    def test_archive_never_follows_symlinks(self, tmp_path):
        """A symlink in the source must not pull out-of-tree bytes into the
        archive before the hardened extractor sees it."""
        import zipfile

        from specify_cli.extensions._commands import _archive_extension_directory

        ext_dir = _create_extension_source(tmp_path)
        outside = tmp_path / "outside.txt"
        outside.write_text("external bytes\n")
        try:
            (ext_dir / "scripts" / "link.txt").symlink_to(outside)
        except OSError:
            pytest.skip("symlink creation requires privileges on this platform")

        archive_path = _archive_extension_directory(ext_dir)
        try:
            with zipfile.ZipFile(archive_path) as zf:
                names = set(zf.namelist())
            assert "scripts/link.txt" not in names
        finally:
            archive_path.unlink()

    @pytest.mark.skipif(
        os.name == "nt", reason="POSIX execute bits do not exist on Windows"
    )
    def test_archive_route_restores_script_execute_bits(self, tmp_path):
        """safe_extract_archive writes members without their recorded ZIP
        modes, so the archive install route depends on install_from_directory's
        trailing ensure_executable_scripts() call to keep documented
        `.specify/extensions/<id>/scripts/*.sh` invocations executable. Pin
        that round trip so removing the restoration would fail here instead
        of surfacing as `Permission denied` after a bundled update."""
        from specify_cli.extensions._commands import _archive_extension_directory

        project_dir = _make_project(tmp_path)
        source = _create_extension_source(tmp_path)
        (source / "scripts" / "run.sh").chmod(0o755)

        archive_path = _archive_extension_directory(source)
        try:
            ExtensionManager(project_dir).install_from_zip(archive_path, "0.1.0")
        finally:
            archive_path.unlink()

        installed_script = (
            project_dir / ".specify" / "extensions" / "test-ext" / "scripts" / "run.sh"
        )
        assert installed_script.is_file()
        assert installed_script.stat().st_mode & 0o100, (
            "execute bit lost through the archive install route"
        )


class TestInstallStoresContentHash:
    def test_registry_entry_records_source_content_hash(self, tmp_path):
        project_dir = _make_project(tmp_path)
        source = _create_extension_source(tmp_path)
        manager = ExtensionManager(project_dir)
        manager.install_from_directory(source, "0.1.0")

        entry = manager.registry.get("test-ext")
        assert entry["content_hash"] == compute_extension_content_hash(source)


class TestUpdateStaleContentDetection:
    def _install(self, tmp_path):
        project_dir = _make_project(tmp_path)
        source = _create_extension_source(tmp_path / "bundled")
        manager = ExtensionManager(project_dir)
        manager.install_from_directory(source, "0.1.0")
        return project_dir, source

    @staticmethod
    def _flat(result) -> str:
        """Console output with Rich's line wrapping collapsed."""
        return " ".join(result.output.split())

    def _run_update(self, project_dir, bundled_path, catalog_info=None):
        runner = CliRunner()
        with patch.object(Path, "cwd", return_value=project_dir), \
             patch.object(
                 ExtensionCatalog,
                 "get_extension_info",
                 return_value=dict(catalog_info or BUNDLED_CATALOG_INFO),
             ), \
             patch(
                 "specify_cli._locate_bundled_extension",
                 return_value=bundled_path,
             ):
            return runner.invoke(
                app, ["extension", "update", "test-ext"], catch_exceptions=True
            )

    def test_reports_stale_content_when_bundled_copy_changed(self, tmp_path):
        project_dir, source = self._install(tmp_path)
        # Upstream ships a fix without bumping the version.
        (source / "scripts" / "run.sh").write_text("#!/bin/sh\necho fixed\n")

        result = self._run_update(project_dir, source)

        assert result.exit_code == 0, result.output
        assert "differ from the copy bundled" in self._flat(result)
        assert "extension add test-ext --force" in self._flat(result)
        assert "All extensions are up to date!" not in self._flat(result)

    def test_up_to_date_when_bundled_copy_matches(self, tmp_path):
        project_dir, source = self._install(tmp_path)

        result = self._run_update(project_dir, source)

        assert result.exit_code == 0, result.output
        assert "Up to date (v1.0.0)" in self._flat(result)
        assert "All extensions are up to date!" in self._flat(result)
        assert "differ from the copy bundled" not in self._flat(result)

    def test_stale_check_covers_registry_entries_without_content_hash(self, tmp_path):
        """Installs that predate content_hash fall back to hashing the installed dir."""
        project_dir, source = self._install(tmp_path)
        manager = ExtensionManager(project_dir)
        entry = manager.registry.get("test-ext")
        del entry["content_hash"]
        manager.registry.data["extensions"]["test-ext"] = entry
        manager.registry._save()
        (source / "scripts" / "run.sh").write_text("#!/bin/sh\necho fixed\n")

        result = self._run_update(project_dir, source)

        assert result.exit_code == 0, result.output
        assert "differ from the copy bundled" in self._flat(result)

    def test_no_stale_flag_when_bundled_copy_is_older_version(self, tmp_path):
        """An installed copy newer than the running release's bundled copy is
        version skew, not content drift — flagging it would steer the user
        into a downgrading --force refresh."""
        project_dir = _make_project(tmp_path)
        v2_source = _create_extension_source(tmp_path / "installed-src", version="2.0.0")
        ExtensionManager(project_dir).install_from_directory(v2_source, "0.1.0")
        old_bundled = _create_extension_source(tmp_path / "bundled", version="1.0.0")
        (old_bundled / "scripts" / "run.sh").write_text("#!/bin/sh\necho old\n")
        catalog_info = dict(BUNDLED_CATALOG_INFO)
        catalog_info["version"] = "2.0.0"

        result = self._run_update(project_dir, old_bundled, catalog_info)

        assert result.exit_code == 0, result.output
        assert "Up to date (v2.0.0)" in self._flat(result)
        assert "differ from the copy bundled" not in self._flat(result)

    def test_no_stale_flag_when_catalog_lags_installed_version(self, tmp_path):
        """The stale check only runs when catalog and installed versions are
        equal; a catalog behind the installed version must not trigger it."""
        project_dir = _make_project(tmp_path)
        v2_source = _create_extension_source(tmp_path / "installed-src", version="2.0.0")
        ExtensionManager(project_dir).install_from_directory(v2_source, "0.1.0")
        old_bundled = _create_extension_source(tmp_path / "bundled", version="1.0.0")
        (old_bundled / "scripts" / "run.sh").write_text("#!/bin/sh\necho old\n")

        result = self._run_update(project_dir, old_bundled)

        assert result.exit_code == 0, result.output
        assert "Up to date (v2.0.0)" in self._flat(result)
        assert "differ from the copy bundled" not in self._flat(result)

    def test_user_config_edits_are_not_reported_as_stale(self, tmp_path):
        project_dir, source = self._install(tmp_path)
        installed_config = (
            project_dir / ".specify" / "extensions" / "test-ext" / "test-ext-config.yml"
        )
        installed_config.write_text("setting: user-edited\n")

        result = self._run_update(project_dir, source)

        assert result.exit_code == 0, result.output
        assert "All extensions are up to date!" in self._flat(result)

    def test_non_bundled_extensions_skip_the_content_check(self, tmp_path):
        project_dir, source = self._install(tmp_path)
        (source / "scripts" / "run.sh").write_text("#!/bin/sh\necho fixed\n")
        catalog_info = dict(BUNDLED_CATALOG_INFO)
        catalog_info["bundled"] = False
        catalog_info["download_url"] = "https://example.com/test-ext-1.0.0.zip"

        runner = CliRunner()
        with patch.object(Path, "cwd", return_value=project_dir), \
             patch.object(
                 ExtensionCatalog, "get_extension_info", return_value=catalog_info
             ), \
             patch(
                 "specify_cli._locate_bundled_extension",
                 return_value=source,
             ):
            result = runner.invoke(
                app, ["extension", "update", "test-ext"], catch_exceptions=True
            )

        assert result.exit_code == 0, result.output
        assert "All extensions are up to date!" in self._flat(result)
