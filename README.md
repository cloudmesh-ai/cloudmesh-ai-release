# Cloudmesh AI Release Automation

Cloudmesh AI Release is an automation extension for the `cmc` (Cloudmesh Commands) tool. It transforms the error-prone process of releasing Python packages to PyPI into a structured, wizard-driven workflow.

By enforcing pre-flight checks, managing state, and providing a "safety net" via baseline commits and rollback capabilities, it ensures that every release is consistent, documented, and reversible.

## Quickstart

The tool is designed to be flexible regarding your working directory. You can run it from a parent directory by specifying the package path, or from within the package directory itself.

### Option 1: The Wizard (Recommended)

Run the interactive wizard that guides you through all steps.

**From a parent directory:**

``` bash
# Specify the path to the package root
cmc release now cloudmesh-ai-cmc
```

**From within the package directory:**

``` bash
# Use '.' to indicate the current directory
cmc release now .
```

The tool will automatically detect the actual package name from the `pyproject.toml` file located at the specified path.

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

stead do b

``` text
Usage:
  cmc release now [options] <package_path>
  cmc release validate <package_path>
  cmc release baseline [options] <package_path>
  cmc release testpypi [options] <package_path>
  cmc release pypi [options] <package_path>
  cmc release check <package_path>
  cmc release rollback [options] <package_path>
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

- **`<package_path>`**: The path to the root directory of the package to release. This can be a relative path (e.g., `cloudmesh-ai-cmc`) or `.` if you are already inside the package directory.
- **`--dry-run`**: Simulates the entire process. No files are changed, no tags created, and no uploads performed.
- **`--version <text>`**: Force a specific target version. If omitted, the version is determined by Git tags (via `setuptools-scm`).
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

#### `rollback`

Emergency recovery tool to restore the local environment to the pre-release state.

- **`<packagename>`**: The directory name of the package to roll back.
- **`--dry-run`**: Simulate the rollback process.

**Example: Recover from a failed release**

``` bash
cmc release rollback cloudmesh-ai-cmc
```

------------------------------------------------------------------------

## Version Management

This tool leverages `setuptools-scm` for dynamic versioning, ensuring that the version of the released package is always perfectly synchronized with the Git history.

### How it Works

Instead of hardcoding a version string in `pyproject.toml` or `setup.py`, the version is derived from the most recent Git tag.

1.  **Tag as Version**: When the tool creates a Git tag (e.g., `v1.2.3`), `setuptools-scm` detects this tag during the `make build` phase.
2.  **Automatic Injection**: The version `1.2.3` is automatically injected into the package metadata.
3.  **Single Source of Truth**: The Git tag is the only place where the version is defined, eliminating the risk of "version mismatch" between the code and the PyPI upload.

### Overriding the Version

While automatic tagging is recommended, you can force a specific version using the `--version` flag: `cmc release now <package> --version 1.2.4`

In this case, the tool will use the provided version for the build and create the corresponding Git tag to maintain consistency.

------------------------------------------------------------------------

## How it Works: The Release Lifecycle

The release process is designed as a safety-first pipeline. It ensures that no broken package ever reaches the official PyPI repository.

### Workflow Diagram

``` mermaid
graph TD;
    A-->B;
    A-->C;
    B-->D;
    C-->D;
```

``` mermaid
graph LR
    %% Style Definitions
    classDef white fill:#ffffff,stroke:#333,stroke-width:1px;
    classDef yellow fill:#ffffcc,stroke:#d4d4aa,stroke-width:1px;
    classDef blue fill:#e6f3ff,stroke:#adcceb,stroke-width:1px;
    classDef green fill:#e6ffed,stroke:#c2e0c6,stroke-width:1px;

    Develop[Develop<br/><small>develop your code</small>] --> A[Start Release<br/><small>cmc release now</small>]
    A --> B{Pre-flight Checks<br/><small>cmc release validate</small>}
    B -- Fail --> C[Stop/Fix]
    B -- Pass --> D[Create Baseline Commit<br/><small>cmc release baseline</small>]
    D --> E[Build Package<br/><small>make build</small>]
    E --> F[Upload to TestPyPI<br/><small>cmc release testpypi</small>]
    F --> G{User Verifies Install?<br/><small>cmc release check</small>}
    G -- No -.-> Develop
    G -- Yes --> I[Create Git Tag<br/><small>git tag & push</small>]
    I --> J[Build Final Artifacts<br/><small>make build</small>]
    J --> K[Upload to PyPI<br/><small>cmc release pypi</small>]
    K --> M[Final Validation<br/><small>pip install & test</small>]
    M --> L[Release Complete<br/><small>Done</small>]
    L -.-> Develop

    %% Assign Classes
    class Develop,A,B,C,D white;
    class E,F,G,H yellow;
    class I,J,K blue;
    class L green;
```

### Detailed Phases

#### Phase 1: Pre-flight Validation

Before any changes are made, the tool verifies: - **Dependencies**: Checks for `git`, `twine`, and `python` in the system PATH. - **Build Module**: Ensures `python -m build` is available. - **Git Hygiene**: Ensures the working directory is clean (`git status --porcelain`). You cannot release from a dirty tree to ensure the baseline is accurate.

#### Phase 2: Establishing the Baseline

To ensure a 100% recovery path, the tool: 1. Captures the current `HEAD` commit hash. 2. Creates a "Baseline" commit containing all current changes. 3. Saves this state to `.release_state.json`.

#### Phase 3: TestPyPI Validation (The Sandbox)

To prevent "broken" releases from hitting production: 1. **Build**: Executes `python -m build` to create `.whl` and `.tar.gz` artifacts. 2. **Upload**: Uses `twine` to upload to the TestPyPI repository. 3. **Verification**: The wizard pauses and asks the user to manually install the package from TestPyPI to verify it works. **This is a critical gate; the process will not proceed to production without user confirmation.**

#### Phase 4: Production Release

Once validated: 1. **Git Tagging**: Creates an annotated tag (e.g., `v1.0.0`) and pushes it to `origin main`. - *Command:* `git tag -a vX.Y.Z -m "Release vX.Y.Z" && git push origin vX.Y.Z` 2. **Build**: Re-builds the artifacts for the final version to ensure the tag is included. - *Command:* `make build` (or `python -m build`) 3. **Double-Confirmation**: Displays a high-visibility red warning panel. The user must confirm **twice** before the upload proceeds. 4. **PyPI Upload**: Uploads the final artifacts to the official PyPI server. 5. **Final Validation**: A critical final check to ensure the production package is installable and functional. - *Action:* Perform a fresh `pip install` of the released version in a clean environment and run the test suite (e.g., `pytest`).

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

| Target          | Action                                     |
|:----------------|:-------------------------------------------|
| `make test`     | Run the pytest suite                       |
| `make test-cov` | Run tests with coverage report             |
| `make build`    | Build sdist and wheel distributions        |
| `make check`    | Validate distribution metadata using twine |
| `make clean`    | Remove build artifacts and cache           |

## License

Apache License, Version 2.0