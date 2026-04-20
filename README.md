# Cloudmesh AI Release Automation

Cloudmesh AI Release is an automation extension for the `cmc` (Cloudmesh Commands) tool. It transforms the error-prone process of releasing Python packages to PyPI into a structured, wizard-driven workflow.

By enforcing pre-flight checks, managing state, and providing a "safety net" via baseline commits and rollback capabilities, it ensures that every release is consistent, documented, and reversible.

## Quickstart

For a standard release of a package (e.g., `cloudmesh-ai-cmc`), you have two options. 

**Tip:** If you are already inside the package directory, you can use `.` as the package name, and the tool will automatically detect the package name from the `pyproject.toml` file.

### Option 1: The Wizard (Recommended)
Run the interactive wizard that guides you through all steps:
``` bash
cmc release now cloudmesh-ai-cmc
# OR if you are in the package directory:
cmc release now .
```

### Option 2: Granular Control
Execute each phase manually:
``` bash
cmc release validate cloudmesh-ai-cmc
cmc release baseline cloudmesh-ai-cmc
cmc release testpypi cloudmesh-ai-cmc
cmc release pypi cloudmesh-ai-cmc
cmc release check cloudmesh-ai-cmc
```

------------------------------------------------------------------------

## Usage

``` text
Usage:
  cmc release now [options] <packagename>
  cmc release validate <packagename>
  cmc release baseline [options] <packagename>
  cmc release testpypi [options] <packagename>
  cmc release pypi [options] <packagename>
  cmc release check <packagename>
  cmc release version [action] <packagename>
  cmc release rollback [options] <packagename>
  cmc release (-h | --help)

Options:
  -h --help                Show this screen.
  --dry-run               Simulate the process without making changes.
  --version <text>        Specify the target version for the release.
  --skip-testpypi         Skip the TestPyPI validation phase.
```

### Subcommand Details

#### `release`

The primary entry point for releasing a package. It initiates an interactive wizard.

- **`<packagename>`**: The directory name of the package to release (must contain a `VERSION` file).
- **`--dry-run`**: Simulates the entire process. No files are changed, no tags created, and no uploads performed.
- **`--version <text>`**: Force a specific target version. If omitted, the version in the package's `VERSION` file is used.
- **`--skip-testpypi`**: Skip the TestPyPI validation phase and go straight to production.

**Example: Standard Release**

``` bash
cmc release now cloudmesh-ai-cmc
```

**Example: Fast-track Release (Skip TestPyPI)**

``` bash
cmc release now cloudmesh-ai-cmc --skip-testpypi
```

**Example: Specific Version Dry-Run**

``` bash
cmc release now cloudmesh-ai-cmc --version 1.2.0 --dry-run
```

#### `version`

Manage and inspect package versions.

- **`<packagename>`**: Print the current version and suggested next steps.
- **`dev+ <packagename>`**: Increment the development version suffix (e.g., `1.0.0.dev1` $\rightarrow$ `1.0.0.dev2`).
- **`prod+ <packagename>`**: Increment the production patch version (e.g., `1.0.0` $\rightarrow$ `1.0.1`).

#### `rollback`

Emergency recovery tool to restore the local environment to the pre-release state.

- **`<packagename>`**: The directory name of the package to roll back.
- **`--dry-run`**: Simulate the rollback process.

**Example: Recover from a failed release**

``` bash
cmc release rollback cloudmesh-ai-cmc
```

------------------------------------------------------------------------

## How it Works: The Release Lifecycle

When you run `cmc release now`, the tool executes the following lifecycle:

### Phase 1: Pre-flight Validation

Before any changes are made, the tool verifies: - **Dependencies**: Checks for `git`, `twine`, and `python` in the system PATH. - **Git Hygiene**: Ensures the working directory is clean (`git status --porcelain`). You cannot release from a dirty tree to ensure the baseline is accurate.

### Phase 2: Establishing the Baseline

To ensure a 100% recovery path, the tool: 1. Captures the current `HEAD` commit hash. 2. Creates a "Baseline" commit containing all current changes. 3. Saves this state to `.release_state.json`.

### Phase 3: TestPyPI Validation (The Sandbox)

To prevent "broken" releases from hitting production: 1. **Version Bump**: Automatically appends `.dev1` to the version (e.g., `1.0.0` $\rightarrow$ `1.0.0.dev1`). 2. **Build**: Executes `python -m build` to create `.whl` and `.tar.gz` artifacts. 3. **Upload**: Uses `twine` to upload to the TestPyPI repository. 4. **Verification**: The wizard pauses and asks the user to manually install the package from TestPyPI to verify it works.

### Phase 4: Production Release

Once validated: 1. **Final Bump**: Sets the version to the final target (e.g., `1.0.0`). 2. **Build**: Re-builds the artifacts for the final version. 3. **Double-Confirmation**: Displays a high-visibility red warning panel. The user must confirm **twice** before the upload proceeds. 4. **PyPI Upload**: Uploads the final artifacts to the official PyPI server. 5. **Git Tagging**: Creates an annotated tag (e.g., `v1.0.0`) and pushes it to `origin main`.

------------------------------------------------------------------------

## Safety Mechanisms

### State Tracking (`.release_state.json`)

The tool maintains a hidden state file in the package root to track the release progress. This allows the `rollback` command to know exactly what to undo.

**State Schema:** - `package_name`: Name of the package. - `baseline_commit`: The git hash to return to on rollback. - `original_version`: The version before the release started. - `created_tag`: The git tag created (if any), used for deletion during rollback. - `completed_steps`: A list of successfully finished phases.

### Rollback Logic

The `rollback` command performs the following in order: 1. **Tag Deletion**: Deletes the local git tag and attempts to delete the remote tag from `origin`. 2. **Git Reset**: Performs a `git reset --hard` to the `baseline_commit`. 3. **Artifact Cleanup**: Deletes the `dist/` directory. 4. **State Cleanup**: Deletes `.release_state.json`.

### Audit Logging

Every release creates a `release_<version>.log` file. This file is the "black box" of the release, containing: - Exact timestamps for every step. - The full shell command executed. - The complete `STDOUT` and `STDERR` of every subprocess.

------------------------------------------------------------------------

## Configuration & Requirements

### Authentication

This tool relies on `twine` for uploads. You must have your PyPI/TestPyPI credentials configured in your environment or via a `.pypirc` file.

**Recommended: Environment Variables**

``` bash
export TWINE_USERNAME=__token__
export TWINE_PASSWORD=pypi-your-api-token-here
```

### System Requirements

- **Python 3.8+**
- **Git**: Installed and configured with a remote `origin`.
- **Twine**: `pip install twine`
- **Build**: `pip install build`

------------------------------------------------------------------------

## Development & Contribution

### Local Installation

``` bash
cd cloudmesh-ai-release
make install
```

### Development Workflow

Use the provided `Makefile` for standard tasks:

| Target               | Action                                     |
|:---------------------|:-------------------------------------------|
| `make test`          | Run the pytest suite                       |
| `make test-cov`      | Run tests with coverage report             |
| `make build`         | Build sdist and wheel distributions        |
| `make check`         | Validate distribution metadata using twine |
| `make clean`         | Remove build artifacts and cache           |
| `make patch V=x.y.z` | Update the version in the `VERSION` file   |

## License

Apache License, Version 2.0