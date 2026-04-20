import pytest
import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open
from click.testing import CliRunner
from cloudmesh.ai.command.release import ReleaseConfig, ReleaseManager, release_group

@pytest.fixture
def tmp_release_dir(tmp_path):
    """Provides a temporary directory for release tests."""
    return tmp_path

@pytest.fixture
def mock_pyproject(tmp_release_dir):
    """Creates a dummy pyproject.toml file."""
    pyproject = tmp_release_dir / "pyproject.toml"
    content = '[project]\nname = "test-package"\nversion = "0.1.0"\n'
    pyproject.write_text(content)
    return pyproject

class TestReleaseConfig:
    def test_load_default(self, tmp_release_dir):
        # Change working directory to tmp_release_dir for config files
        with patch("pathlib.Path.exists", return_value=False):
            config = ReleaseConfig(config_path=str(tmp_release_dir / "config.json"))
            assert config.data == {"packages": [], "ignored": []}

    def test_add_package(self, tmp_release_dir):
        config_path = tmp_release_dir / "config.json"
        config = ReleaseConfig(config_path=str(config_path))
        config.add_package("pkg1")
        assert "pkg1" in config.get_packages()
        assert config_path.exists()
        
        # Test that adding again doesn't duplicate
        config.add_package("pkg1")
        assert config.get_packages().count("pkg1") == 1

    def test_ignore_package(self, tmp_release_dir):
        config_path = tmp_release_dir / "config.json"
        config = ReleaseConfig(config_path=str(config_path))
        config.add_package("pkg1")
        config.ignore_package("pkg1")
        assert "pkg1" not in config.get_packages()
        assert "pkg1" in config.data["ignored"]

    def test_plan_state(self, tmp_release_dir):
        state_path = tmp_release_dir / "state.json"
        config = ReleaseConfig(state_path=str(state_path))
        config.save_plan_state("pkg1", "in_progress")
        
        state = config.load_plan_state()
        assert state == {"last_package": "pkg1", "status": "in_progress"}
        
        config.clear_plan_state()
        assert not state_path.exists()

class TestReleaseManager:
    def test_extract_package_name(self, tmp_release_dir, mock_pyproject):
        manager = ReleaseManager(str(tmp_release_dir))
        # Manually trigger extraction since __init__ uses the argument as name if it's not "."
        name = manager._extract_package_name()
        assert name == "test-package"

    def test_extract_package_name_not_found(self, tmp_release_dir):
        manager = ReleaseManager(str(tmp_release_dir))
        with pytest.raises(FileNotFoundError):
            manager._extract_package_name()

    def test_run_command_dry_run(self, tmp_release_dir):
        manager = ReleaseManager(str(tmp_release_dir), dry_run=True)
        result = manager.run_command(["ls", "-la"])
        assert result.returncode == 0
        assert result.stdout == ""

    @patch("subprocess.run")
    def test_run_command_real(self, mock_run, tmp_release_dir):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["ls"], returncode=0, stdout="file1\nfile2", stderr=""
        )
        manager = ReleaseManager(str(tmp_release_dir), dry_run=False)
        result = manager.run_command(["ls"])
        assert result.stdout == "file1\nfile2"
        mock_run.assert_called_once()

    def test_save_load_state(self, tmp_release_dir):
        manager = ReleaseManager(str(tmp_release_dir))
        manager.state["baseline_commit"] = "abc1234"
        manager.save_state()
        
        # Create a new manager to load the state
        manager2 = ReleaseManager(str(tmp_release_dir))
        assert manager2.state["baseline_commit"] == "abc1234"

    @patch("subprocess.run")
    def test_check_git_clean(self, mock_run, tmp_release_dir):
        # Mock clean git status
        mock_run.return_value = subprocess.CompletedProcess(
            args=["git", "status", "--porcelain"], returncode=0, stdout="", stderr=""
        )
        manager = ReleaseManager(str(tmp_release_dir))
        manager.check_git_clean() # Should not raise

        # Mock dirty git status
        mock_run.return_value = subprocess.CompletedProcess(
            args=["git", "status", "--porcelain"], returncode=0, stdout="M file.py", stderr=""
        )
        with pytest.raises(RuntimeError, match="Git working directory is not clean"):
            manager.check_git_clean()

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_check_dependencies(self, mock_run, mock_which, tmp_release_dir):
        mock_which.return_value = "/usr/bin/git"
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        
        manager = ReleaseManager(str(tmp_release_dir))
        manager.check_dependencies() # Should not raise

        mock_which.return_value = None
        with pytest.raises(RuntimeError, match="Required dependency"):
            manager.check_dependencies()

    @patch("subprocess.run")
    def test_get_scm_version(self, mock_run, tmp_release_dir):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="v1.2.3-dirty\n"
        )
        manager = ReleaseManager(str(tmp_release_dir))
        assert manager.get_scm_version() == "1.2.3-dirty"

class TestReleaseCLI:
    def test_validate_cmd_success(self, tmp_release_dir, mock_pyproject):
        runner = CliRunner()
        with patch("cloudmesh.ai.command.release.ReleaseManager.check_dependencies"), \
             patch("cloudmesh.ai.command.release.ReleaseManager.check_git_clean"):
            result = runner.invoke(release_group, ["validate", str(tmp_release_dir)])
            assert result.exit_code == 0
            assert "Validation successful!" in result.output

    def test_validate_cmd_failure(self, tmp_release_dir, mock_pyproject):
        runner = CliRunner()
        with patch("cloudmesh.ai.command.release.ReleaseManager.check_dependencies", side_effect=RuntimeError("Dep missing")):
            result = runner.invoke(release_group, ["validate", str(tmp_release_dir)])
            assert result.exit_code == 1
            assert "Validation failed: Dep missing" in result.output

    def test_baseline_cmd(self, tmp_release_dir, mock_pyproject):
        runner = CliRunner()
        with patch("cloudmesh.ai.command.release.ReleaseManager.create_baseline"):
            result = runner.invoke(release_group, ["baseline", str(tmp_release_dir)])
            assert result.exit_code == 0
            assert "Baseline created successfully!" in result.output

    def test_rollback_cmd(self, tmp_release_dir, mock_pyproject):
        runner = CliRunner()
        with patch("cloudmesh.ai.command.release.ReleaseManager.rollback"):
            result = runner.invoke(release_group, ["rollback", str(tmp_release_dir)])
            assert result.exit_code == 0
            assert "Rollback completed successfully." in result.output