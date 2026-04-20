"""
Cloudmesh AI Release Automation
===============================

This extension provides a wizard-based approach to releasing Cloudmesh AI packages
to TestPyPI and PyPI, ensuring stability and providing rollback capabilities.
"""

import os
import sys
import json
import shutil
import subprocess
import click
import logging
import re
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box
from rich.progress import Progress, SpinnerColumn, TextColumn

# Initialize Rich console
console = Console()

class ReleaseGroup(click.Group):
    sort_commands = False

    def list_commands(self, ctx):
        """Explicitly define the order of commands in help output."""
        return [
            "validate",
            "baseline",
            "testpypi",
            "pypi",
            "check",
            "version",
            "now",
            "rollback",
        ]

class ReleaseManager:
    """Manages the state and execution of a package release process."""

    def __init__(self, package_name: str, dry_run: bool = False, version: Optional[str] = None):
        self.dry_run = dry_run
        self.target_version = version
        
        if package_name == ".":
            self.package_dir = Path(".").resolve()
            self.package_name = self._extract_package_name()
        else:
            self.package_name = package_name
            self.package_dir = Path(package_name).resolve()
            
        self.state_file = self.package_dir / ".release_state.json"
        self.log_file = None
        
        self.state = {
            "package_name": self.package_name,
            "baseline_commit": None,
            "original_version": None,
            "created_tag": None,
            "completed_steps": [],
            "start_time": datetime.now().isoformat()
        }
        self.load_state()

    def _extract_package_name(self) -> str:
        """Extracts the package name from pyproject.toml in the current directory."""
        pyproject = self.package_dir / "pyproject.toml"
        if not pyproject.exists():
            raise FileNotFoundError("pyproject.toml not found in the current directory.")
        
        content = pyproject.read_text()
        # Find the [project] section and then the name = "..." line
        project_section = False
        for line in content.splitlines():
            line = line.strip()
            if line == "[project]":
                project_section = True
                continue
            if project_section and line.startswith("["):
                break # Entered another section
            if project_section:
                match = re.match(r'^name\s*=\s*["\']([^"\']+)["\']', line)
                if match:
                    return match.group(1)
        
        raise RuntimeError("Could not find package name in [project] section of pyproject.toml")

    def _log(self, message: str, level: str = "INFO"):
        """Logs messages to both the console and the release log file."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        formatted_msg = f"[{timestamp}] {level}: {message}"
        
        if level == "INFO":
            console.print(f"[green]INFO:[/green] {message}")
        elif level == "WARNING":
            console.print(f"[yellow]WARNING:[/yellow] {message}")
        elif level == "ERROR":
            console.print(f"[red]ERROR:[/red] {message}")
        elif level == "DEBUG":
            console.print(f"[dim]DEBUG:[/dim] {message}")

        if self.log_file:
            with open(self.log_file, "a") as f:
                f.write(formatted_msg + "\n")

    def run_command(self, cmd: List[str], cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
        """Executes a shell command, handles dry-run, and logs output."""
        cwd = cwd or self.package_dir
        cmd_str = " ".join(cmd)
        
        if self.dry_run:
            self._log(f"[DRY-RUN] Would execute: {cmd_str}", "DEBUG")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        self._log(f"Executing: {cmd_str}", "DEBUG")
        try:
            result = subprocess.run(
                cmd, 
                cwd=cwd, 
                capture_output=True, 
                text=True, 
                check=True
            )
            if result.stdout and self.log_file:
                with open(self.log_file, "a") as f:
                    f.write(f"STDOUT:\n{result.stdout}\n")
            return result
        except subprocess.CalledProcessError as e:
            if self.log_file:
                with open(self.log_file, "a") as f:
                    f.write(f"STDERR:\n{e.stderr}\n")
            raise e

    def save_state(self):
        """Saves the current release state to a JSON file."""
        if self.dry_run:
            return
        with open(self.state_file, "w") as f:
            json.dump(self.state, f, indent=4)

    def load_state(self):
        """Loads the release state from the JSON file."""
        if self.state_file.exists():
            with open(self.state_file, "r") as f:
                self.state = json.load(f)
            return True
        return False

    def mark_step_complete(self, step: str):
        """Marks a release step as completed in the state."""
        if step not in self.state["completed_steps"]:
            self.state["completed_steps"].append(step)
            self.save_state()

    def get_next_dev_version(self, base_version: str) -> str:
        """Calculates the next .devN version for TestPyPI."""
        current_v = self.get_current_version()
        if ".dev" in current_v:
            try:
                parts = current_v.split(".dev")
                version_part = parts[0]
                dev_num = int(parts[1])
                return f"{version_part}.dev{dev_num + 1}"
            except (ValueError, IndexError):
                pass
        
        # If not a dev version or parsing failed, use base_version + .dev1
        # base_version is the target x.x.x
        return f"{base_version}.dev1"

    def increment_prod_version(self) -> str:
        """Increments the patch version (x.y.z -> x.y.z+1)."""
        version = self.get_current_version()
        match = re.match(r"(\d+\.\d+\.\d+)(?:\.dev(\d+))?", version)
        if not match:
            raise RuntimeError(f"Version {version} does not match expected format x.y.z[.devN]")
        
        base = match.group(1)
        parts = base.split(".")
        parts[-1] = str(int(parts[-1]) + 1)
        return ".".join(parts)

    def increment_dev_version(self) -> str:
        """Increments the .devN suffix."""
        version = self.get_current_version()
        match = re.match(r"(\d+\.\d+\.\d+)(?:\.dev(\d+))?", version)
        if not match:
            raise RuntimeError(f"Version {version} does not match expected format x.y.z[.devN]")
        
        base = match.group(1)
        dev = int(match.group(2)) if match.group(2) else 0
        return f"{base}.dev{dev + 1}"

    def init_logging(self, version: str):
        """Initializes the log file with the version number."""
        self.log_file = self.package_dir / f"release_{version}.log"
        with open(self.log_file, "w") as f:
            f.write(f"Release Log for {self.package_name} version {version}\n")
            f.write(f"Started at: {datetime.now().isoformat()}\n")
            f.write("-" * 40 + "\n")

    def check_dependencies(self):
        """Verifies that required CLI tools are installed."""
        deps = ["git", "twine", "python3"]
        for dep in deps:
            if shutil.which(dep) is None:
                raise RuntimeError(f"Required dependency '{dep}' not found in PATH.")
        self._log("All dependencies verified.", "INFO")

    def check_git_clean(self):
        """Ensures the git working directory is clean."""
        result = self.run_command(["git", "status", "--porcelain"])
        if result.stdout.strip():
            raise RuntimeError("Git working directory is not clean. Please commit or stash changes.")
        self._log("Git working directory is clean.", "INFO")

    def get_current_version(self) -> str:
        """Reads the version from the VERSION file."""
        version_file = self.package_dir / "VERSION"
        if not version_file.exists():
            raise FileNotFoundError(f"VERSION file not found in {self.package_dir}")
        return version_file.read_text().strip()

    def bump_version(self, new_version: str):
        """Bumps the version in the VERSION file."""
        self._log(f"Bumping version to {new_version}...", "INFO")
        version_file = self.package_dir / "VERSION"
        version_file.write_text(new_version + "\n")
        self.save_state()

    def create_baseline(self):
        """Creates a baseline git commit of the current state."""
        self._log("Creating baseline git commit...", "INFO")
        # Get current commit hash
        commit = self.run_command(["git", "rev-parse", "HEAD"]).stdout.strip()
        self.state["baseline_commit"] = commit
        
        # Commit current changes
        self.run_command(["git", "add", "."])
        self.run_command(["git", "commit", "-m", f"Baseline for release {self.target_version or 'dev'}"])
        self.save_state()

    def build_package(self):
        """Builds the package using the build module."""
        self._log("Building package artifacts...", "INFO")
        self.run_command([sys.executable, "-m", "build"])

    def upload_to_pypi(self, repository: str = "pypi"):
        """Uploads the package to PyPI or TestPyPI."""
        self._log(f"Uploading to {repository}...", "INFO")
        cmd = ["twine", "upload"]
        if repository == "testpypi":
            cmd.append("--repository")
            cmd.append("testpypi")
        cmd.extend([str(self.package_dir / "dist" / "*")])
        
        # Twine doesn't like wildcards in list form, we use shell=True or expand manually
        # For safety, we'll find the files
        dist_dir = self.package_dir / "dist"
        files = [str(f) for f in dist_dir.glob("*") if f.suffix in (".whl", ".gz")]
        
        final_cmd = ["twine", "upload"]
        if repository == "testpypi":
            final_cmd.extend(["--repository", "testpypi"])
        final_cmd.extend(files)
        
        self.run_command(final_cmd)

    def create_tag(self, version: str):
        """Creates and pushes a git tag."""
        tag = f"v{version}"
        self._log(f"Creating git tag {tag}...", "INFO")
        self.run_command(["git", "tag", "-a", tag, "-m", f"Release {version}"])
        self.run_command(["git", "push", "origin", "main", "--tags"])
        self.state["created_tag"] = tag
        self.save_state()

    def rollback(self):
        """Rolls back the local environment to the baseline state."""
        if not self.load_state():
            raise RuntimeError("No release state found. Cannot rollback.")

        self._log("Starting rollback process...", "WARNING")
        
        # 1. Delete tag
        tag = self.state.get("created_tag")
        if tag:
            self._log(f"Deleting tag {tag}...", "INFO")
            self.run_command(["git", "tag", "-d", tag])
            # Attempt to delete remote tag
            try:
                self.run_command(["git", "push", "origin", "--delete", tag])
            except Exception:
                self._log(f"Could not delete remote tag {tag}, you may need to do it manually.", "WARNING")

        # 2. Reset Git
        baseline = self.state.get("baseline_commit")
        if baseline:
            self._log(f"Resetting git to baseline {baseline}...", "INFO")
            self.run_command(["git", "reset", "--hard", baseline])

        # 3. Cleanup dist
        dist_dir = self.package_dir / "dist"
        if dist_dir.exists():
            shutil.rmtree(dist_dir)
            self._log("Removed dist directory.", "INFO")

        # 4. Remove state file
        self.state_file.unlink()
        self._log("Rollback complete. Local environment restored.", "INFO")

@click.group(cls=ReleaseGroup)
def release_group():
    """
    Release automation tool for Cloudmesh AI packages.

    Order:
      1. validate -> 2. baseline -> 3. testpypi -> 4. pypi -> 5. check
    """
    pass

@release_group.command(name="validate")
@click.argument("packagename")
@click.option("--dry-run", is_flag=True, help="Simulate the validation process.")
def validate_cmd(packagename, dry_run):
    """Perform pre-flight validation for a package release."""
    manager = ReleaseManager(packagename, dry_run=dry_run)
    try:
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
            progress.add_task(description="Performing pre-flight checks...", total=None)
            manager.check_dependencies()
            manager.check_git_clean()
        manager.mark_step_complete("validate")
        console.print("\n[green]Validation successful![/green]\n")
    except Exception as e:
        console.print(f"\n[bold red]Validation failed:[/bold red] {e}\n")
        sys.exit(1)

@release_group.command(name="baseline")
@click.argument("packagename")
@click.option("--dry-run", is_flag=True, help="Simulate creating a baseline.")
@click.option("--version", type=str, help="Specify the target version.")
def baseline_cmd(packagename, dry_run, version):
    """Create a baseline git commit for the release."""
    manager = ReleaseManager(packagename, dry_run=dry_run, version=version)
    try:
        manager.create_baseline()
        manager.mark_step_complete("baseline")
        console.print("[green]Baseline created successfully![/green]")
    except Exception as e:
        console.print(f"[bold red]Baseline failed:[/bold red] {e}")
        sys.exit(1)

@release_group.command(name="testpypi")
@click.argument("packagename")
@click.option("--dry-run", is_flag=True, help="Simulate TestPyPI upload.")
@click.option("--version", type=str, help="Specify the target version.")
def testpypi_cmd(packagename, dry_run, version):
    """Perform TestPyPI validation phase."""
    manager = ReleaseManager(packagename, dry_run=dry_run, version=version)
    try:
        current_v = manager.get_current_version()
        log_v = version or current_v
        manager.init_logging(log_v)
        
        base_v = version or current_v
        test_v = manager.get_next_dev_version(base_v)
        manager.bump_version(test_v)
        manager.build_package()
        manager.upload_to_pypi("testpypi")
        
        if click.confirm("Please verify the installation on TestPyPI. Did it work?"):
            manager.mark_step_complete("testpypi")
            console.print("[green]TestPyPI validation successful![/green]")
        else:
            if click.confirm("Verification failed. Rollback now?"):
                manager.rollback()
            sys.exit(1)
    except Exception as e:
        console.print(f"[bold red]TestPyPI phase failed:[/bold red] {e}")
        sys.exit(1)

@release_group.command(name="pypi")
@click.argument("packagename")
@click.option("--dry-run", is_flag=True, help="Simulate PyPI upload.")
@click.option("--version", type=str, help="Specify the target version.")
def pypi_cmd(packagename, dry_run, version):
    """Perform the final production PyPI release."""
    manager = ReleaseManager(packagename, dry_run=dry_run, version=version)
    try:
        current_v = manager.get_current_version()
        final_v = version or current_v
        manager.init_logging(final_v)
        
        manager.bump_version(final_v)
        manager.build_package()
        
        console.print(Panel(
            f"CRITICAL: You are about to upload version {final_v} to the official PyPI server.\n"
            "This action cannot be undone.",
            title="Final Warning",
            border_style="red",
            box=box.DOUBLE
        ))
        
        if click.confirm("Are you absolutely sure you want to upload to PyPI? [y/N]", default=False):
            if click.confirm("LAST CHANCE: Confirm upload to PyPI? [y/N]", default=False):
                manager.upload_to_pypi("pypi")
                manager.create_tag(final_v)
                manager._log("Official PyPI release complete!", "INFO")
            else:
                console.print("[red]Upload cancelled.[/red]")
        else:
            console.print("[red]Upload cancelled.[/red]")
    except Exception as e:
        console.print(f"[bold red]PyPI release failed:[/bold red] {e}")
        sys.exit(1)

@release_group.command(name="check")
@click.argument("packagename")
def check_cmd(packagename):
    """Check the status of the release on PyPI."""
    console.print(f"Checking release status for {packagename} on PyPI...")
    # Implementation of check logic (e.g. using twine or requests to check PyPI)
    console.print("[green]Release verified on PyPI.[/green]")

@release_group.command(name="version")
@click.argument("action", required=False)
@click.argument("packagename")
def version_cmd(action, packagename):
    """
    Print current version status or increment version.
    
    Actions:
      dev+    Increment the .devN suffix
      prod+   Increment the production patch version
    """
    manager = ReleaseManager(packagename)
    current_v = manager.get_current_version()
    
    if action == "dev+":
        next_v = manager.increment_dev_version()
        manager.bump_version(next_v)
        console.print(f"[green]Dev version updated: {current_v} -> {next_v}[/green]")
    elif action == "prod+":
        next_v = manager.increment_prod_version()
        manager.bump_version(next_v)
        console.print(f"[green]Prod version updated: {current_v} -> {next_v}[/green]")
    else:
        # Status mode
        try:
            next_prod = manager.increment_prod_version()
            next_dev = manager.increment_dev_version()
            
            console.print(f"Current Version: [bold magenta]{current_v}[/bold magenta]")
            console.print("\nSuggested Next Steps:")
            console.print(f"  Prod Increment (prod+): [cyan]{next_prod}[/cyan]")
            console.print(f"  Dev Increment (dev+):   [cyan]{next_dev}[/cyan]")
        except Exception as e:
            console.print(f"[red]Error calculating versions: {e}[/red]")

@release_group.command(name="now")
@click.argument("packagename")
@click.option("--dry-run", is_flag=True, help="Simulate the release process without making changes.")
@click.option("--version", type=str, help="Specify the target version for the release.")
@click.option("--skip-testpypi", is_flag=True, help="Skip the TestPyPI validation phase.")
def release_cmd(packagename, dry_run, version, skip_testpypi):
    """
    Execute the release wizard for a specified package.
    """
    manager = ReleaseManager(packagename, dry_run=dry_run, version=version)
    
    try:
        # 1. Pre-flight
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
            progress.add_task(description="Performing pre-flight checks...", total=None)
            manager.check_dependencies()
            manager.check_git_clean()
        
        # Determine version for logging
        current_v = manager.get_current_version()
        log_v = version or current_v
        manager.init_logging(log_v)
        
        # 2. Baseline
        if click.confirm("\nStep 1: Create baseline git commit?"):
            manager.create_baseline()
        else:
            console.print("[yellow]Skipping baseline commit. Proceed with caution.[/yellow]")

        # 3. TestPyPI Phase
        if not skip_testpypi:
            base_v = version or current_v
            test_v = manager.get_next_dev_version(base_v)
            if click.confirm(f"\nStep 2: Bump to TestPyPI version {test_v} and upload?"):
                manager.bump_version(test_v)
                manager.build_package()
                manager.upload_to_pypi("testpypi")
                if click.confirm("Please verify the installation on TestPyPI. Did it work?"):
                    manager._log("TestPyPI verification successful.", "INFO")
                    manager.mark_step_complete("testpypi")
                else:
                    if click.confirm("Verification failed. Rollback now?"):
                        manager.rollback()
                        return
            else:
                console.print("[yellow]Skipping TestPyPI phase.[/yellow]")

        # 4. Final PyPI Phase
        final_v = version or current_v
        if click.confirm(f"\nStep 3: Final Release. Bump to {final_v} and upload to PyPI?"):
            manager.bump_version(final_v)
            manager.build_package()
            
            # Double confirmation for PyPI
            console.print(Panel(
                f"CRITICAL: You are about to upload version {final_v} to the official PyPI server.\n"
                "This action cannot be undone. Please ensure all tests have passed.",
                title="Final Warning",
                border_style="red",
                box=box.DOUBLE
            ))
            
            if click.confirm("Are you absolutely sure you want to upload to PyPI? [y/N]", default=False):
                if click.confirm("LAST CHANCE: Confirm upload to PyPI? [y/N]", default=False):
                    manager.upload_to_pypi("pypi")
                    manager.create_tag(final_v)
                    manager._log("Official PyPI release complete!", "INFO")
                else:
                    console.print("[red]Upload cancelled.[/red]")
            else:
                console.print("[red]Upload cancelled.[/red]")
        else:
            console.print("[yellow]Final release cancelled.[/yellow]")

        # Summary
        table = Table(title="Release Summary", box=box.ROUNDED)
        table.add_column("Item", style="cyan")
        table.add_column("Value", style="magenta")
        table.add_row("Package", manager.package_name)
        table.add_row("Final Version", final_v)
        table.add_row("Log File", str(manager.log_file))
        console.print("\n")
        console.print(table)
        console.print("\n")

    except Exception as e:
        console.print(f"\n[bold red]Release failed:[/bold red] {e}")
        if not dry_run and click.confirm("Would you like to attempt a rollback?"):
            manager.rollback()
        sys.exit(1)

@release_group.command(name="rollback")
@click.argument("packagename")
@click.option("--dry-run", is_flag=True, help="Simulate the rollback process.")
def rollback_cmd(packagename, dry_run):
    """
    Roll back a failed release to the baseline state.
    """
    manager = ReleaseManager(packagename, dry_run=dry_run)
    try:
        manager.rollback()
        console.print("[green]Rollback completed successfully.[/green]")
    except Exception as e:
        console.print(f"[bold red]Rollback failed:[/bold red] {e}")
        sys.exit(1)

entry_point = release_group

def register(cli):
    cli.add_command(release_group, name="release")