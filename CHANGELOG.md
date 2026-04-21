# Changelog

All notable changes to `cloudmesh-ai-release` will be documented in this file.

## [0.1.6] - 2026-04-21

### Fixed
- Fixed `twine upload` command argument order for `--no-color` to prevent execution errors.
- Improved readability of `twine` upload output by stripping ANSI escape sequences and disabling color.

## [0.1.5] - 2026-04-20

### Added
- Added `cmc release clean-tags` command to interactively delete local and remote git tags (defaults to `.dev` tags).
- Implemented a bulk release planning system (`release plan add`, `list`, `do`) to manage releases across multiple packages.
- Added a "Version Review" table to the release wizard for transparent version projection.

### Fixed
- Fixed log file versioning in the Release Summary and ensured the log file is renamed on disk to match the final version.
- Handled existing Git tags during the production phase by suggesting a version increment.
- Resolved missing `questionary` dependency.
- Fixed duplicated confirmation prompts in the release wizard.

### Changed
- Enhanced the `clean-tags` TUI with red highlighting for selected items.

## [0.1.4] - 2026-04-19

### Added
- Implemented rollback capabilities to restore the local environment to the baseline state after a failed release.
- Added automatic cleanup of the `dist` directory before building to prevent artifact confusion.

### Fixed
- Fixed "Git working directory is not clean" check to allow the `VERSION` file to match the projected version.
- Fixed a `NameError` in the `validate` command.
- Fixed build artifacts using the wrong version by ensuring they respect the `VERSION` file.

## [0.1.3] - 2026-04-18

### Changed
- Configured `pyproject.toml` to read the package version dynamically from the `VERSION` file.
- Removed hardcoded version strings from the `ReleaseManager` logic.
- Improved version projection logic to correctly handle the transition from `.dev` versions to stable releases.
- Removed the 'v' prefix from projected Git Tags in the review table for consistency.

## [0.1.0] - 2026-04-17

### Added
- Initial release of the Cloudmesh AI Release Automation tool.
- Wizard-based release flow: `validate` -> `baseline` -> `testpypi` -> `pypi`.
- Integration with `twine` for PyPI and TestPyPI uploads.
- Automatic Git tagging and baseline commit creation.
- Support for `.dev` versioning and semantic version bumping.