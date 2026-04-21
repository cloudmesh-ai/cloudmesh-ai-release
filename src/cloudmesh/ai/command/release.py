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
import urllib.request
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
            "now",
            "rollback",
        ]

class ReleaseConfig:
    """Manages the release configuration and bulk state files."""
    
    def __init__(self, config_path: str = ".release_config.json", state_path: str = ".release_plan_state.json"):
        self.config_path = Path(config_path)
        self.state_path = Path(state_path)
        self.data = self._load()

    def _load(self) -> Dict[str, Any]:
        if self.config_path.exists():
            try:
                return json.loads(self.config_path.read_text())
            except json.JSONDecodeError:
                return {"packages": [], "ignored": []}
        return {"packages": [], "ignored": []}

    def save(self):
        with open(self.config_path, "w") as f:
            json.dump(self.data, f, indent=4)

    def add_package(self, package: str):
        if package not in self.data["packages"]:
            self.data["packages"].append(package)
            if package in self.data["ignored"]:
                self.data["ignored"].remove(package)
            self.save()

    def ignore_package(self, package: str):
        if package in self.data["packages"]:
            self.data["packages"].remove(package)
        if package not in self.data["ignored"]:
            self.data["ignored"].append(package)
        self.save()

    def get_packages(self) -> List[str]:
        return self.data.get("packages", [])

    def save_plan_state(self, last_package: str, status: str):
        """Saves the progress of a bulk release."""
        state = {"last_package": last_package, "status": status}
        self.state_path.write_text(json.dumps(state, indent=4))

    def load_plan_state(self) -> Optional[Dict[str, Any]]:
        """Loads the progress of a bulk release."""
        if self.state_path.exists():
            try:
                return json.loads(self.state_path.read_text())
            except json.JSONDecodeError:
                return None
        return None

    def clear_plan_state(self):
        """Clears the bulk release state."""
        if self.state_path.exists():
            self.state_path.unlink()

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
            
        # Extract organization from git remote
        self.organization = self._extract_git_info().get("organization", "Unknown")
            
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

    def _extract_git_info(self) -> Dict[str, str]:
        """Extracts organization from the git remote URL."""
        url = None
        try:
            # Use git config to get the remote URL for origin
            result = self.run_command(["git", "config", "--get", "remote.origin.url"])
            url = result.stdout.strip()
        except Exception:
            try:
                # Fallback: get the first available remote
                remotes_res = self.run_command(["git", "remote"])
                remotes = remotes_res.stdout.splitlines()
                if remotes:
                    result = self.run_command(["git", "config", f"--get remote.{remotes[0]}.url"])
                    url = result.stdout.strip()
            except Exception:
                pass

        if url:
            # Handle GitHub SSH: git@github.com:org/repo.git
            if "github.com:" in url:
                org = url.split("github.com:")[1].split("/")[0]
                return {"organization": org}
            # Handle GitHub HTTPS: https://github.com/org/repo.git
            if "github.com/" in url:
                org = url.split("github.com/")[1].split("/")[0]
                return {"organization": org}
        
        return {"organization": "Unknown"}

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

    def run_command(self, cmd: List[str], cwd: Optional[Path] = None, stream: bool = False) -> subprocess.CompletedProcess:
        """Executes a shell command, handles dry-run, and logs output."""
        cwd = cwd or self.package_dir
        cmd_str = " ".join(cmd)
        
        if self.dry_run:
            self._log(f"[DRY-RUN] Would execute: {cmd_str}", "DEBUG")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        self._log(f"Executing: {cmd_str}", "DEBUG")
        try:
            if stream:
                # Use Popen to stream output in real-time
                process = subprocess.Popen(
                    cmd, 
                    cwd=cwd, 
                    stdout=subprocess.PIPE, 
                    stderr=subprocess.STDOUT, 
                    text=True
                )
                stdout_acc = []
                for line in process.stdout:
                    console.print(f"  [dim]{line.strip()}[/dim]")
                    stdout_acc.append(line)
                
                process.wait()
                if process.returncode != 0:
                    raise subprocess.CalledProcessError(process.returncode, cmd, "".join(stdout_acc))
                
                result = subprocess.CompletedProcess(cmd, 0, stdout="".join(stdout_acc), stderr="")
            else:
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

    def get_scm_version(self) -> str:
        """Gets the current version using git describe (setuptools-scm style)."""
        try:
            # --tags: use tags, --always: fallback to commit hash, --dirty: append -dirty if changes exist
            result = self.run_command(["git", "describe", "--tags", "--always", "--dirty"])
            version = result.stdout.strip()
            # Remove 'v' prefix if present for internal use
            if version.startswith("v"):
                version = version[1:]
            return version
        except Exception as e:
            self._log(f"Could not determine SCM version: {e}", "ERROR")
            raise RuntimeError("Failed to determine package version from Git tags.")

    def is_commit_hash(self, version: str) -> bool:
        """Checks if the version string looks like a git commit hash rather than a semantic version."""
        # Semantic versions usually have dots. Commit hashes are hex strings.
        if "." in version:
            return False
        # Check if it's a hex string of typical commit hash length (7-40)
        return bool(re.match(r'^[0-9a-f]{7,40}$', version.lower()))

    def _parse_version(self, version: str) -> Optional[tuple]:
        """Parses a version string into a tuple of integers for comparison."""
        if not version or version == "Not found" or version == "No tag":
            return None
        
        # Remove 'v' prefix and .devN suffix for base comparison
        v = version[1:] if version.startswith("v") else version
        v = v.split(".dev")[0]
        
        try:
            return tuple(map(int, v.split('.')))
        except (ValueError, AttributeError):
            return None

    def bump_patch_version(self, version: str) -> str:
        """Increments the patch version of a semantic version string (x.y.z -> x.y.z+1)."""
        # Ensure we are working with the base version (no .dev)
        base_version = version.split(".dev")[0]
        if base_version.startswith("v"):
            base_version = base_version[1:]
            
        parts = base_version.split('.')
        if len(parts) != 3:
            raise RuntimeError(f"Invalid semantic version for bumping: {version}. Expected x.y.z")
        
        major, minor, patch = parts
        return f"{major}.{minor}.{int(patch) + 1}"

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

    def increment_prod_version(self, base_version: Optional[str] = None) -> str:
        """Increments the production patch version (x.y.z -> x.y.z+1)."""
        version = base_version or self.get_current_version()
        return self.bump_patch_version(version)

    def increment_dev_version(self, base_version: Optional[str] = None) -> str:
        """Increments the .devN suffix."""
        version = base_version or self.get_current_version()
        
        # Remove 'v' prefix if present
        v_clean = version[1:] if version.startswith("v") else version
        
        if ".dev" in v_clean:
            try:
                base, dev_part = v_clean.rsplit(".dev", 1)
                return f"{base}.dev{int(dev_part) + 1}"
            except (ValueError, IndexError):
                pass
        
        # If not a dev version, start at .dev1 of the current version
        return f"{v_clean}.dev1"

    def get_pypi_version(self, repository: str = "pypi") -> str:
        """Fetches the current version of the package from PyPI or TestPyPI."""
        base_url = "https://pypi.org/pypi" if repository == "pypi" else "https://test.pypi.org/pypi"
        url = f"{base_url}/{self.package_name}/json"
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                data = json.loads(response.read().decode())
                return data["info"]["version"]
        except Exception:
            return "Not found"

    def version_exists_on_testpypi(self, version: str) -> bool:
        """Checks if a specific version exists on TestPyPI."""
        url = f"https://test.pypi.org/pypi/{self.package_name}/{version}/json"
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                return response.getcode() == 200
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return False
            raise e
        except Exception:
            return False

    def get_latest_git_tag(self) -> str:
        """Gets the latest git tag."""
        try:
            result = self.run_command(["git", "describe", "--tags", "--abbrev=0"])
            tag = result.stdout.strip()
            return tag[1:] if tag.startswith("v") else tag
        except Exception:
            return "No tag"

    def get_version_projection(self) -> Dict[str, str]:
        """
        Returns a comprehensive version projection based on the maximum current version.
        """
        current_file = self.get_current_version()
        latest_tag = self.get_latest_git_tag()
        pypi_v = self.get_pypi_version("pypi")
        testpypi_v = self.get_pypi_version("testpypi")
        
        sources = [("pypi", pypi_v), ("git", latest_tag)]
        parsed_versions = []
        for source, v in sources:
            parsed = self._parse_version(v)
            if parsed:
                parsed_versions.append((parsed, v, source))
        
        if not parsed_versions:
            proj_prod = self.get_current_version()
        else:
            parsed_versions.sort(key=lambda x: x[0])
            _, max_v_str, _ = parsed_versions[-1]
            clean_max = max_v_str[1:] if max_v_str.startswith("v") else max_v_str
            if ".dev" in clean_max:
                proj_prod = clean_max.split(".dev")[0]
            else:
                proj_prod = self.bump_patch_version(clean_max)
        proj_dev = f"{proj_prod}.dev1"
        
        while self.version_exists_on_testpypi(proj_dev) or self.check_tag_exists(proj_dev):
            proj_dev = self.increment_dev_version(proj_dev)
        
        return {
            "git_tag": latest_tag,
            "github_version": current_file,
            "pypi_version": pypi_v,
            "testpypi_version": testpypi_v,
            "projected_pypi": proj_prod,
            "projected_testpypi": proj_dev
        }

    def get_next_dev_version(self) -> str:
        """Calculates the next .devN version based on existing git tags."""
        try:
            tags_res = self.run_command(["git", "tag", "-l"])
            tags = tags_res.stdout.splitlines()
        except Exception:
            tags = []

        semver_tags = []
        for t in tags:
            if t.startswith('v') and any(char.isdigit() for char in t):
                semver_tags.append(t[1:])

        if not semver_tags:
            return None

        semver_tags.sort(reverse=True)
        latest = semver_tags[0]

        if ".dev" in latest:
            base, dev_part = latest.rsplit(".dev", 1)
            try:
                next_n = int(dev_part) + 1
                return f"{base}.dev{next_n}"
            except ValueError:
                return f"{base}.dev1"
        else:
            try:
                next_v = self.bump_patch_version(latest)
                return f"{next_v}.dev1"
            except RuntimeError:
                return f"{self.get_current_version()}.dev1"

    def init_logging(self, version: str):
        """Initializes the log file with the version number."""
        self.log_file = self.package_dir / f"release_{version}.log"
        with open(self.log_file, "w") as f:
            f.write(f"Release Log for {self.package_name} version {version}\n")
            f.write(f"Started at: {datetime.now().isoformat()}\n")
            f.write("-" * 40 + "\n")

    def check_dependencies(self):
        """Verifies that required CLI tools are installed."""
        deps = ["git", "twine", "python"]
        for dep in deps:
            if shutil.which(dep) is None:
                raise RuntimeError(f"Required dependency '{dep}' not found in PATH.")
        
        try:
            self.run_command(["python", "-m", "build", "--help"])
        except Exception:
            raise RuntimeError("The 'build' module is not installed. Please run 'pip install build'.")
            
        self._log("All dependencies verified.", "INFO")

    def check_git_clean(self, allowed_version: Optional[str] = None):
        """Ensures the git working directory is clean, optionally allowing VERSION to match allowed_version."""
        result = self.run_command(["git", "status", "--porcelain"])
        stdout = result.stdout.strip()
        
        if stdout:
            # Check if the only change is the VERSION file
            lines = stdout.splitlines()
            version_file_only = len(lines) == 1 and "VERSION" in lines[0]
            
            if version_file_only and allowed_version:
                current_v = self.get_current_version()
                if current_v == allowed_version:
                    self._log(f"Git directory has modified VERSION matching projected version {allowed_version}. Proceeding...", "INFO")
                    return

            status_result = self.run_command(["git", "status"])
            status_output = status_result.stdout
            raise RuntimeError(
                f"Git working directory is not clean. Please commit or stash changes.\n\n{status_output}"
            )
        self._log("Git working directory is clean.", "INFO")

    def check_tag_exists(self, version: str):
        """Checks if a git tag already exists for the given version."""
        tag = f"v{version}"
        try:
            self.run_command(["git", "rev-parse", tag])
            return True
        except subprocess.CalledProcessError:
            return False

    def create_baseline(self):
        """Creates a baseline git commit of the current state."""
        self._log("Creating baseline git commit...", "INFO")
        commit = self.run_command(["git", "rev-parse", "HEAD"]).stdout.strip()
        self.state["baseline_commit"] = commit
        version = self.target_version or self.get_scm_version() or "dev"
        self.run_command(["git", "add", "."])
        try:
            self.run_command(["git", "commit", "-m", f"Baseline for release {version}"])
        except subprocess.CalledProcessError as e:
            error_msg = (e.stdout or "") + (e.stderr or "")
            if "nothing to commit" in error_msg.lower():
                self._log("No changes to commit for baseline.", "INFO")
            else:
                raise e
        self.save_state()

    def build_package(self):
        """Builds the package using the build module and verifies artifacts."""
        self._log("Building package artifacts...", "INFO")
        is_dry = self.dry_run
        if is_dry:
            self._log("[DRY-RUN] Verifying build process by executing build...", "DEBUG")
            try:
                subprocess.run([sys.executable, "-m", "build"], cwd=self.package_dir, check=True, capture_output=True)
            except subprocess.CalledProcessError as e:
                raise RuntimeError(f"Build verification failed: {e.stderr.decode()}") from e
        else:
            self.run_command([sys.executable, "-m", "build"], stream=True)

        dist_dir = self.package_dir / "dist"
        if not dist_dir.exists():
            raise RuntimeError("Build completed but 'dist' directory was not created.")
        
        artifacts = list(dist_dir.glob("*"))
        if not artifacts:
            raise RuntimeError("Build completed but no artifacts were found in 'dist' directory.")
        
        for art in artifacts:
            self._log(f"Verified artifact: {art.name}", "INFO")
        
        if is_dry:
            self._log("[DRY-RUN] Cleaning up verification artifacts...", "DEBUG")
            shutil.rmtree(dist_dir)

    def upload_to_pypi(self, repository: str = "pypi"):
        """Uploads the package to PyPI or TestPyPI."""
        self._log(f"Uploading to {repository}...", "INFO")
        dist_dir = self.package_dir / "dist"
        files = [str(f) for f in dist_dir.glob("*") if f.suffix in (".whl", ".gz")]
        final_cmd = ["twine", "upload"]
        if repository == "testpypi":
            final_cmd.extend(["--repository", "testpypi"])
        final_cmd.extend(files)
        self.run_command(final_cmd, stream=True)

    def create_tag(self, version: str):
        """Creates and pushes a git tag."""
        if self.check_tag_exists(version):
            raise RuntimeError(f"Git tag v{version} already exists. Please check the version or delete the tag.")
        tag = f"v{version}"
        self._log(f"Creating git tag {tag}...", "INFO")
        self.run_command(["git", "tag", "-a", tag, "-m", f"Release {version}"])
        self.run_command(["git", "push", "origin", tag])
        self.state["created_tag"] = tag
        self.save_state()

    def get_changelog(self) -> str:
        """Generates a summary of commits since the last tag."""
        try:
            last_tag_res = self.run_command(["git", "describe", "--tags", "--abbrev=0"])
            last_tag = last_tag_res.stdout.strip()
            range_str = f"{last_tag}..HEAD"
        except Exception:
            range_str = "HEAD"
        self._log(f"Generating changelog for {range_str}...", "DEBUG")
        result = self.run_command(["git", "log", range_str, "--oneline", "--no-merges"])
        return result.stdout.strip() or "No new commits found."

    def rollback(self):
        """Rolls back the local environment to the baseline state."""
        if not self.load_state():
            self._log("No release state found. Nothing to roll back.", "WARNING")
            return
        self._log("Starting rollback process...", "WARNING")
        tag = self.state.get("created_tag")
        if tag:
            self._log(f"Deleting tag {tag}...", "INFO")
            self.run_command(["git", "tag", "-d", tag])
            try:
                self.run_command(["git", "push", "origin", "--delete", tag])
            except Exception:
                self._log(f"Could not delete remote tag {tag}, you may need to do it manually.", "WARNING")
        baseline = self.state.get("baseline_commit")
        if baseline:
            self._log(f"Resetting git to baseline {baseline}...", "INFO")
            self.run_command(["git", "reset", "--hard", baseline])
        dist_dir = self.package_dir / "dist"
        if dist_dir.exists():
            shutil.rmtree(dist_dir)
            self._log("Removed dist directory.", "INFO")
        self.state_file.unlink()
        self._log("Rollback complete. Local environment restored.", "INFO")

@click.group(cls=ReleaseGroup)
def release_group():
    """
    Release automation tool for Cloudmesh AI packages.
    """
    pass

@click.group(name="plan")
def plan_group():
    """Manage the release plan and execute bulk releases."""
    pass

release_group.add_command(plan_group)

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
        test_v = version or manager.get_next_dev_version()
        if not test_v:
            console.print("[yellow]No git tags found to determine the next dev version.[/yellow]")
            test_v = click.prompt("Please enter the next dev version (e.g., 0.1.1.dev1)")
        manager.init_logging(test_v)
        manager.create_tag(test_v)
        manager.build_package()
        manager.upload_to_pypi("testpypi")
        if click.confirm(f"Please verify the installation of version {test_v} on TestPyPI. Did it work?"):
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
        current_v = manager.get_scm_version()
        final_v = version or current_v
        if not version and manager.is_commit_hash(final_v):
            console.print(f"[yellow]Warning: SCM version is a commit hash ({final_v}).[/yellow]")
            final_v = click.prompt("Please enter a starting semantic version (e.g., 0.1.0)")
        manager.init_logging(final_v)
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
                manager._log("Official PyPI release complete!", "INFO")
                try:
                    next_v = manager.bump_patch_version(final_v)
                    manager._log(f"Post-release: Preparing next cycle {next_v}...", "INFO")
                    manager.create_tag(next_v)
                    manager.create_tag(f"{next_v}.dev1")
                    manager._log(f"Next release target set to v{next_v} and dev version to v{next_v}.dev1", "INFO")
                except Exception as e:
                    manager._log(f"Post-release tagging failed: {e}", "WARNING")
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
    console.print("[green]Release verified on PyPI.[/green]")

@release_group.command(name="version")
@click.argument("action", required=False)
@click.argument("packagename")
def version_cmd(action, packagename):
    """Print current version status or increment version."""
    manager = ReleaseManager(packagename)
    try:
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
            next_prod = manager.increment_prod_version()
            next_dev = manager.increment_dev_version()
            console.print(f"Current Version: [bold magenta]{current_v}[/bold magenta]")
            console.print("\nSuggested Next Steps:")
            console.print(f"  Prod Increment (prod+): [cyan]{next_prod}[/cyan]")
            console.print(f"  Dev Increment (dev+):   [cyan]{next_dev}[/cyan]")
    except Exception as e:
        console.print(f"[red]Error managing version: {e}[/red]")

def run_release_wizard(packagename, dry_run, version, skip_testpypi):
    """Core logic for the release wizard, reusable by 'now' and 'do'."""
    manager = ReleaseManager(packagename, dry_run=dry_run, version=version)
    try:
        projection = manager.get_version_projection()
        review_table = Table(title="Version Review", box=box.ROUNDED)
        review_table.add_column("Metric", style="cyan")
        review_table.add_column("Value", style="magenta")
        review_table.add_column("Projected", style="green")
        review_table.add_row("Package", manager.package_name, "")
        review_table.add_row("Organization", manager.organization, "")
        review_table.add_row("Git Tag", projection["git_tag"], version or projection['projected_pypi'])
        review_table.add_row("VERSION", projection["github_version"], "")
        review_table.add_row("PyPI", projection["pypi_version"], f"{version or projection['projected_pypi']}")
        review_table.add_row("TestPyPI", projection["testpypi_version"], projection['projected_testpypi'])
        console.print("\n")
        console.print(review_table)
        console.print("\n")
        if not click.confirm("Do you agree with these versions and wish to proceed?"):
            console.print("[yellow]Release cancelled by user during version review.[/yellow]")
            return False
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
            progress.add_task(description="Performing pre-flight checks...", total=None)
            manager.check_dependencies()
            manager.check_git_clean()
        final_v = version or projection['projected_pypi']
        manager.init_logging(final_v)
        changelog = manager.get_changelog()
        console.print(Panel(changelog, title="Suggested Changelog", border_style="blue"))
        if click.confirm("\nStep 1: Create baseline git commit?"):
            manager.create_baseline()
        else:
            console.print("[yellow]Skipping baseline commit. Proceed with caution.[/yellow]")
        if not skip_testpypi:
            test_v = version or projection['projected_testpypi']
            console.print(f"\nStep 2: [bold cyan]Verification Phase[/bold cyan] - Tag as {test_v}, build, and upload to TestPyPI?")
            if click.confirm("Proceed?", default=True):
                try:
                    manager.bump_version(test_v)
                    manager.create_tag(test_v)
                    manager.build_package()
                    manager.upload_to_pypi("testpypi")
                    console.print(f"\n[bold yellow]ACTION REQUIRED:[/bold yellow] Please install version {test_v} from TestPyPI in a clean environment to verify it works.")
                    if click.confirm("Did the TestPyPI installation and verification work?"):
                        manager._log("TestPyPI verification successful.", "INFO")
                        manager.mark_step_complete("testpypi")
                    else:
                        if click.confirm("Verification failed. Rollback now?"):
                            manager.rollback()
                            return False
                except Exception as e:
                    console.print(f"[bold red]TestPyPI phase failed:[/bold red] {e}")
                    if click.confirm("Would you like to attempt a rollback?"):
                        manager.rollback()
                        return False
            else:
                console.print("[yellow]Skipping TestPyPI verification phase.[/yellow]")
        console.print(f"\nStep 3: [bold green]Production Phase[/bold green] - Tag as {final_v}, build, and upload to PyPI?")
        if click.confirm("Proceed?", default=True):
            manager.bump_version(final_v)
            manager.create_tag(final_v)
            manager.build_package()
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
                    manager._log("Official PyPI release complete!", "INFO")
                    try:
                        next_v = manager.bump_patch_version(final_v)
                        manager._log(f"Post-release: Preparing next cycle {next_v}...", "INFO")
                        manager.create_tag(next_v)
                        manager.create_tag(f"{next_v}.dev1")
                        manager._log(f"Next release target set to v{next_v} and dev version to v{next_v}.dev1", "INFO")
                    except Exception as e:
                        manager._log(f"Post-release tagging failed: {e}", "WARNING")
                else:
                    console.print("[red]Upload cancelled.[/red]")
            else:
                console.print("[red]Upload cancelled.[/red]")
        else:
            console.print("[yellow]Final release cancelled.[/yellow]")
        table = Table(title="Release Summary", box=box.ROUNDED)
        table.add_column("Item", style="cyan")
        table.add_column("Value", style="magenta")
        table.add_row("Organization", manager.organization)
        table.add_row("Package", manager.package_name)
        table.add_row("Final Version", final_v)
        table.add_row("Log File", str(manager.log_file))
        console.print("\n")
        console.print(table)
        console.print("\n")
        return True
    except Exception as e:
        console.print(f"\n[bold red]Release failed:[/bold red] {e}")
        if not dry_run and manager.state.get("baseline_commit") and click.confirm("Would you like to attempt a rollback?"):
            manager.rollback()
        return False

@release_group.command(name="now")
@click.argument("packagename")
@click.option("--dry-run", is_flag=True, help="Simulate the release process without making changes.")
@click.option("--version", type=str, help="Specify the target version for the release.")
@click.option("--skip-testpypi", is_flag=True, help="Skip the TestPyPI validation phase.")
def release_cmd(packagename, dry_run, version, skip_testpypi):
    """Execute the release wizard for a specified package."""
    if not run_release_wizard(packagename, dry_run, version, skip_testpypi):
        sys.exit(1)

@plan_group.command(name="add")
@click.argument("packagename")
def plan_add(packagename):
    """Add a package to the release plan."""
    config = ReleaseConfig()
    config.add_package(packagename)
    console.print(f"[green]Added {packagename} to the release plan.[/green]")

@plan_group.command(name="ignore")
@click.argument("packagename")
def plan_ignore(packagename):
    """Remove a package from the release plan."""
    config = ReleaseConfig()
    config.ignore_package(packagename)
    console.print(f"[yellow]Ignored {packagename} in the release plan.[/yellow]")

@plan_group.command(name="list")
def plan_list():
    """Show packages configured for release."""
    config = ReleaseConfig()
    packages = config.get_packages()
    if not packages:
        console.print("[yellow]No packages configured for release. Use 'release plan add' to add some.[/yellow]")
        return
    table = Table(title="Release Plan", box=box.ROUNDED)
    table.add_column("Index", style="cyan")
    table.add_column("Package Name", style="magenta")
    for i, pkg in enumerate(packages, 1):
        table.add_row(str(i), pkg)
    console.print(table)

@plan_group.command(name="do")
@click.option("--dry-run", is_flag=True, help="Simulate the bulk release.")
@click.option("--version", type=str, help="Specify a target version for all packages.")
@click.option("--skip-testpypi", is_flag=True, help="Skip TestPyPI for all packages.")
def plan_do(dry_run, version, skip_testpypi):
    """Execute the release wizard for all configured packages."""
    config = ReleaseConfig()
    packages = config.get_packages()
    if not packages:
        console.print("[red]No packages configured. Use 'release plan add' first.[/red]")
        return
    state = config.load_plan_state()
    if state and state.get("status") != "completed":
        last_pkg = state.get("last_package")
        if last_pkg and click.confirm(f"A previous bulk release failed at {last_pkg}. Resume from there?"):
            try:
                idx = packages.index(last_pkg)
                packages = packages[idx:]
                console.print(f"[yellow]Resuming release from {last_pkg}...[/yellow]")
            except ValueError:
                console.print("[red]Last package not found in current plan. Starting from beginning.[/red]")
    console.print(Panel(f"Preparing version review for {len(packages)} packages...", style="bold blue"))
    all_projections = {}
    for pkg in packages:
        try:
            pkg_manager = ReleaseManager(pkg)
            proj = pkg_manager.get_version_projection()
            proj["organization"] = pkg_manager.organization
            all_projections[pkg] = proj
        except Exception as e:
            all_projections[pkg] = {"error": str(e)}
    review_table = Table(title="Bulk Release Version Review", box=box.ROUNDED)
    review_table.add_column("Package", style="cyan")
    review_table.add_column("Organization", style="magenta")
    review_table.add_column("Current Tag", style="magenta")
    review_table.add_column("Projected Tag", style="green")
    review_table.add_column("VERSION", style="magenta")
    review_table.add_column("Projected PyPI", style="green")
    review_table.add_column("Current TestPyPI", style="magenta")
    review_table.add_column("Projected TestPyPI", style="green")
    for pkg in packages:
        proj = all_projections.get(pkg, {})
        if "error" in proj:
            review_table.add_row(pkg, "[red]Error[/red]", "[red]Error[/red]", "[red]Error[/red]", "[red]Error[/red]", "[red]Error[/red]", "[red]Error[/red]", "[red]Error[/red]", "[red]Error[/red]")
            continue
        review_table.add_row(
            pkg,
            proj.get("organization", "N/A"),
            proj.get("git_tag", "N/A"),
            f"[green]v{version or proj.get('projected_pypi', 'N/A')}[/green]",
            proj.get("github_version", "N/A"),
            f"[green]{version or proj.get('projected_pypi', 'N/A')}[/green]",
            proj.get("testpypi_version", "N/A"),
            f"[green]{proj.get('projected_testpypi', 'N/A')}[/green]"
        )
    console.print("\n")
    console.print(review_table)
    console.print("\n")
    if not click.confirm("Do you agree with all projected versions and wish to proceed with the bulk release?"):
        console.print("[yellow]Bulk release cancelled by user during version review.[/yellow]")
        return
    console.print(Panel(f"Starting bulk release for {len(packages)} packages...", style="bold blue"))
    for pkg in packages:
        console.print(Panel(f"Processing package: [bold magenta]{pkg}[/bold magenta]", style="cyan"))
        config.save_plan_state(pkg, "in_progress")
        success = run_release_wizard(pkg, dry_run, version, skip_testpypi)
        if not success:
            config.save_plan_state(pkg, "failed")
            if click.confirm(f"Release failed for {pkg}. Continue with remaining packages?"):
                continue
            else:
                sys.exit(1)
    config.clear_plan_state()
    console.print("\n[bold green]Bulk release process completed![/bold green]\n")

@plan_group.command(name="now")
@click.argument("packagename")
@click.option("--dry-run", is_flag=True, help="Simulate the release process.")
@click.option("--version", type=str, help="Specify the target version.")
@click.option("--skip-testpypi", is_flag=True, help="Skip TestPyPI.")
def plan_now(packagename, dry_run, version, skip_testpypi):
    """Execute the release wizard for a specific package from the plan group."""
    if not run_release_wizard(packagename, dry_run, version, skip_testpypi):
        sys.exit(1)

@release_group.command(name="rollback")
@click.argument("packagename")
@click.option("--dry-run", is_flag=True, help="Simulate the rollback process.")
def rollback_cmd(packagename, dry_run):
    """Roll back a failed release to the baseline state."""
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