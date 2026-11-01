# npcadd — reproducible natural-product CADD pipeline
#
# `make reproduce` is the entry point that matters: it regenerates every table
# and figure from raw inputs inside a digest-pinned container.

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

UV            ?= uv
PY            ?= .venv/bin/python
CONFIG        ?= config.sample.yaml
SMOKE_CONFIG  ?= config.smoke.yaml
IMAGE         ?= npcadd:0.1.0
DOCKER        ?= docker

# Everything the container needs to see, mounted read-write so outputs land on
# the host. data/raw is mounted read-only to enforce the read-only convention.
DOCKER_RUN = $(DOCKER) run --rm \
	-v "$(CURDIR)/data/raw:/work/data/raw:ro" \
	-v "$(CURDIR)/data/sample:/work/data/sample:ro" \
	-v "$(CURDIR)/data/interim:/work/data/interim" \
	-v "$(CURDIR)/data/processed:/work/data/processed" \
	-v "$(CURDIR)/reports:/work/reports" \
	-v "$(CURDIR)/runs:/work/runs" \
	-e PYTHONHASHSEED=0 \
	$(IMAGE)

.PHONY: help
help: ## Show this help
	@echo "npcadd targets:"
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "Variables: CONFIG=$(CONFIG)  IMAGE=$(IMAGE)"

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

.PHONY: install
install: ## Create .venv and install from the pinned lockfile
	$(UV) venv -p 3.11 .venv
	$(UV) sync --frozen --extra dev
	@echo "Installed from uv.lock. Activate with: source .venv/bin/activate"

.PHONY: lock
lock: ## Regenerate uv.lock after changing dependencies
	$(UV) lock
	@echo "Lockfile updated. Commit uv.lock alongside pyproject.toml."

.PHONY: sample-data
sample-data: ## Regenerate the SYNTHETIC sample dataset
	$(PY) scripts/make_sample_data.py

# ---------------------------------------------------------------------------
# Quality gates
# ---------------------------------------------------------------------------

.PHONY: lint
lint: ## Run ruff (lint + format check)
	.venv/bin/ruff check src tests scripts
	.venv/bin/ruff format --check src tests scripts

.PHONY: format
format: ## Apply ruff formatting and safe fixes
	.venv/bin/ruff check --fix src tests scripts
	.venv/bin/ruff format src tests scripts

.PHONY: typecheck
typecheck: ## Run mypy --strict
	.venv/bin/mypy

.PHONY: test
test: ## Run the test suite with coverage on pure functions
	.venv/bin/pytest -m "not slow and not network" \
		--cov=npcadd --cov-report=term-missing --cov-report=xml \
		--cov-fail-under=80

.PHONY: test-all
test-all: ## Run every test, including slow ones
	.venv/bin/pytest

.PHONY: check
check: lint typecheck test ## Run every quality gate

# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

.PHONY: smoke
smoke: ## End-to-end run on 3 ligands (CI gate; software test, not science)
	$(PY) -m npcadd.cli run-all --config $(SMOKE_CONFIG)

.PHONY: validate
validate: ## Run only the mandatory docking validation
	$(PY) -m npcadd.cli validate-docking --config $(CONFIG)

.PHONY: pipeline
pipeline: ## Full pipeline on the host interpreter (no container)
	$(PY) -m npcadd.cli run-all --config $(CONFIG)

# ---------------------------------------------------------------------------
# Reproduction
# ---------------------------------------------------------------------------

BASE_IMAGE ?= python:3.11-slim-bookworm

.PHONY: docker-pin
docker-pin: ## Resolve the base image digest and pin it into the Dockerfile
	@echo "Resolving digest for $(BASE_IMAGE) ..."
	@$(DOCKER) pull $(BASE_IMAGE) >/dev/null
	@digest=$$($(DOCKER) inspect --format='{{index .RepoDigests 0}}' $(BASE_IMAGE) | cut -d@ -f2); \
	test -n "$$digest" || { echo "Could not resolve a digest"; exit 1; }; \
	sed -i.bak -E 's|^ARG BASE_IMAGE=.*|ARG BASE_IMAGE=python@'"$$digest"'|' Dockerfile; \
	rm -f Dockerfile.bak; \
	echo "Pinned base image to python@$$digest"; \
	echo "Now record the digest and today's date in DECISIONS.md."

.PHONY: docker-check-pinned
docker-check-pinned: ## Fail if the base image is still an unpinned tag
	@if grep -qE '^ARG BASE_IMAGE=python@sha256:[0-9a-f]{64}$$' Dockerfile; then \
		echo "Base image is digest-pinned."; \
	else \
		echo "ERROR: the Dockerfile base image is NOT digest-pinned."; \
		echo "Run 'make docker-pin' before using this pipeline for a manuscript."; \
		echo "See LIMITATIONS.md, 'Container digest is unpinned'."; \
		exit 1; \
	fi

.PHONY: docker-build
docker-build: ## Build the analysis image
	$(DOCKER) build --build-arg BASE_IMAGE=$(BASE_IMAGE) -t $(IMAGE) .

.PHONY: reproduce
reproduce: docker-build ## THE TARGET: regenerate every table and figure in the container
	@$(MAKE) --no-print-directory docker-check-pinned || \
		echo ">>> Continuing with an UNPINNED base image: byte-identical output across time is NOT guaranteed."
	@mkdir -p data/interim data/processed reports/figures runs
	$(DOCKER_RUN) run-all --config $(CONFIG)
	@echo ""
	@echo "Reproduction complete. Outputs:"
	@echo "  reports/$$(basename $$(ls -t reports/*.xlsx 2>/dev/null | head -1 || echo '<no workbook>'))"
	@echo "  reports/VALIDATION.md"
	@echo "  reports/SUPPLEMENTARY_INFORMATION.md"
	@echo "  reports/figures/"
	@echo "Verify a previous run's checksums with: make verify RUN=<run-id>"

.PHONY: reproduce-local
reproduce-local: ## Reproduce without Docker (weaker guarantee; see LIMITATIONS.md)
	@echo "WARNING: running outside the container. Byte-identical output is only"
	@echo "guaranteed inside the pinned image. See LIMITATIONS.md."
	$(PY) -m npcadd.cli run-all --config $(CONFIG)

.PHONY: verify
verify: ## Re-hash a run's recorded inputs/outputs: make verify RUN=cfg-abc123
	@test -n "$(RUN)" || (echo "Usage: make verify RUN=<run-id>"; exit 2)
	$(PY) -m npcadd.cli verify-provenance runs/$(RUN)/provenance.json

# ---------------------------------------------------------------------------
# Documentation and housekeeping
# ---------------------------------------------------------------------------

.PHONY: docs
docs: ## Render the pipeline diagram and check documentation links
	@command -v mmdc >/dev/null 2>&1 \
		&& mmdc -i docs/pipeline.mmd -o docs/pipeline.svg \
		|| echo "mermaid-cli (mmdc) not installed; docs/pipeline.mmd left unrendered"
	$(PY) scripts/check_docs.py

.PHONY: clean
clean: ## Remove generated outputs (never touches data/raw or data/sample)
	rm -rf runs reports/figures/* reports/*.xlsx \
		reports/VALIDATION.md reports/SUPPLEMENTARY_INFORMATION.md
	rm -rf data/interim/* data/processed/*
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
	rm -rf .pytest_cache .mypy_cache .ruff_cache .hypothesis .coverage coverage.xml
	@touch reports/figures/.gitkeep data/interim/.gitkeep data/processed/.gitkeep
	@echo "Cleaned. data/raw and data/sample were not touched."

.PHONY: clean-cache
clean-cache: ## Also drop the fetched-structure cache (forces re-download)
	rm -rf data/raw/pdb_cache/*
	@touch data/raw/pdb_cache/.gitkeep

.PHONY: hooks
hooks: ## Install the pre-commit hooks
	.venv/bin/pre-commit install
	.venv/bin/pre-commit install --hook-type commit-msg
