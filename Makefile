######################################################################
# Cloudmesh AI Release Makefile
######################################################################

# Variables
PYTHON       := python
PIP          := $(PYTHON) -m pip
PACKAGE_NAME := $(shell basename $(CURDIR))
TWINE        := $(PYTHON) -m twine
VERSION_FILE := VERSION
GIT          := git
PYENVVERSION := $(shell pyenv version-name)

.PHONY: help install clean build upload test-upload test-install reinstall \
        check version patch tag release test test-cov setup-test uninstall-all \
        tmp-setup

help:
	@echo
	@echo "Makefile for the AI Release plugin:"
	@echo
	@echo "  version       - Display current version from $(VERSION_FILE)"
	@echo "  patch V=x.y.z - Update version in $(VERSION_FILE) (e.g., V=4.0.1.dev1)"
	@echo "  install       - Install in editable mode for local development"
	@echo "  reinstall     - Clean and reinstall locally"
	@echo "  clean         - Remove build artifacts, cache, and test debris"
	@echo "  build         - Build distributions (sdist and wheel)"
	@echo "  check         - Build and validate metadata/README"
	@echo "  test          - Run pytest suite"
	@echo "  test-cov      - Run pytest with coverage report"
	@echo "  setup-test    - Install test deps and generate test files"
	@echo "  test-upload   - Build, check, and upload to TestPyPI"
	@echo "  test-install  - Uninstall local and install from TestPyPI"
	@echo "  upload        - Build, check, and upload to Production PyPI"
	@echo "  tag           - Create a git tag based on current version and push"
	@echo "  release       - Full Production Cycle: upload + tag"
	@echo

# --- VERSION MANAGEMENT ---

version:
	@cat $(VERSION_FILE)

patch:
	@if [ -z "$(V)" ]; then echo "Usage: make patch V=4.0.1.dev1"; exit 1; fi
	@echo $(V) > $(VERSION_FILE)

# --- DEVELOPMENT & TESTING ---

install:
	$(PIP) install -e .

requirements:
	pip-compile --output-file=requirements.txt pyproject.toml

test:
	PYTHONPATH=src pytest -v tests/

test-cov:
	PYTHONPATH=src pytest --cov=cloudmesh.ai.command.release --cov-report=term-missing tests/

setup-test:
	$(PIP) install pytest pytest-mock pytest-cov

# --- BUILD AND VALIDATE ---

build: clean
	@echo "Building distributions..."
	$(PYTHON) -m build

check: build
	@echo "Validating distribution metadata..."
	$(TWINE) check dist/*

# --- TEST PYPI (SANDBOX) ---

test-upload: check
	@echo "Uploading to TestPyPI..."
	$(TWINE) upload --repository testpypi dist/*

tmp-setup:
	cd /tmp && pyenv local $$(pyenv global)

test-install: tmp-setup
	@echo "Removing local version to ensure fresh test..."
	-$(PIP) uninstall -y $(PACKAGE_NAME)
	@echo "Installing latest version from TestPyPI..."
	cd /tmp && pyenv exec $(PYTHON) -m pip install --no-cache-dir --upgrade --force-reinstall --ignore-installed \
				  --index-url https://test.pypi.org/simple/ \
				  --extra-index-url https://pypi.org/simple/ \
				  --pre $(PACKAGE_NAME)
	@echo "\nVerification: Package $(PACKAGE_NAME) installed from TestPyPI."

# --- PRODUCTION AND TAGGING ---

upload: check
	@echo "Uploading to Production PyPI..."
	$(TWINE) upload dist/*

tag:
	@VERSION=$$(cat $(VERSION_FILE)); \
	echo "Tagging version v$$VERSION..."; \
	$(GIT) tag -a v$$VERSION -m "Release v$$VERSION"; \
	$(GIT) push origin v$$VERSION

release: upload tag
	@echo "Production release and tagging complete."

# --- CLEANUP & REINSTALL ---

uninstall-all:
	@echo "Searching for installed cloudmesh-ai packages..."
	@$(PIP) freeze | grep "cloudmesh-ai" | cut -d'=' -f1 | xargs $(PIP) uninstall -y || echo "No cloudmesh-ai packages found."

clean:
	@echo "Cleaning artifacts and temporary test plugins..."
	rm -rf build/ dist/ *.egg-info .pytest_cache .coverage
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -rf tmp/cloudmesh-ai-*

reinstall: uninstall-all clean
	@echo "Performing fresh install..."
	$(PIP) install -e .