#!/usr/bin/env python3
"""Check that the documentation stays consistent with the code.

CI runs this. It catches the documentation failures that actually happen:
a promised file that does not exist, a CLI command documented but not
implemented (or implemented but undocumented), and a sample-data file that has
lost its synthetic-data marker.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = [
    "README.md",
    "DECISIONS.md",
    "LIMITATIONS.md",
    "CONTRIBUTING.md",
    "CITATION.cff",
    "LICENSE",
    "Makefile",
    "Dockerfile",
    "pyproject.toml",
    "uv.lock",
    "config.sample.yaml",
    "config.smoke.yaml",
    ".pre-commit-config.yaml",
    ".github/workflows/ci.yml",
    "data/sample/README.md",
    "data/sample/receptors/PROVENANCE.md",
    "docs/pipeline.mmd",
]

#: Commands the brief requires the CLI to expose.
REQUIRED_COMMANDS = [
    "prepare-ligands",
    "prepare-receptor",
    "validate-docking",
    "dock",
    "admet",
    "rank",
    "report",
    "run-all",
]


def check_required_files() -> list[str]:
    return [
        f"missing required file: {name}"
        for name in REQUIRED_FILES
        if not (REPO_ROOT / name).exists()
    ]


def check_cli_commands() -> list[str]:
    """Every required command must exist in the CLI and be named in the README."""
    problems: list[str] = []
    cli_source = (REPO_ROOT / "src" / "npcadd" / "cli.py").read_text(encoding="utf-8")
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")

    registered = set(re.findall(r'@app\.command\(\s*"([^"]+)"', cli_source))
    # Commands registered by function name rather than an explicit string.
    for match in re.finditer(r"@app\.command\(\)\s*\ndef\s+(\w+)", cli_source):
        registered.add(match.group(1).replace("_", "-"))

    for command in REQUIRED_COMMANDS:
        if command not in registered:
            problems.append(f"CLI does not register the required command: {command}")
        if f"npcadd {command}" not in readme:
            problems.append(f"README does not document: npcadd {command}")
    return problems


def check_synthetic_markers() -> list[str]:
    """Sample CSVs must keep the marker that stops them being mistaken for data."""
    problems: list[str] = []
    sample_dir = REPO_ROOT / "data" / "sample"
    csvs = sorted(sample_dir.glob("*.csv"))
    if not csvs:
        return ["data/sample contains no CSV files"]
    for path in csvs:
        text = path.read_text(encoding="utf-8")
        if "data_provenance" not in text.splitlines()[0]:
            problems.append(f"{path.name} has no data_provenance column")
        if "SYNTHETIC_PLACEHOLDER" not in text:
            problems.append(f"{path.name} lost its SYNTHETIC_PLACEHOLDER marker")
    return problems


def check_decisions_entries() -> list[str]:
    """The methodological decisions the brief requires must be recorded."""
    required_topics = [
        ("catalase", "human catalase rejected as a docking target"),
        ("assayed activities", "docking targets restricted to the assayed activities"),
        ("efficiency normalisation", "raw score comparison needs size normalisation"),
        ("directionality", "activation phenotype vs inhibitor-bound structure"),
        ("GUI", "GUI tools excluded from the critical path"),
    ]
    text = (REPO_ROOT / "DECISIONS.md").read_text(encoding="utf-8").lower()
    problems: list[str] = []
    for needle, description in required_topics:
        if needle.lower() not in text:
            problems.append(f"DECISIONS.md does not cover: {description}")
    return problems


def main() -> int:
    problems: list[str] = []
    problems += check_required_files()
    problems += check_cli_commands()
    problems += check_synthetic_markers()
    if (REPO_ROOT / "DECISIONS.md").exists():
        problems += check_decisions_entries()

    if problems:
        print("Documentation consistency check FAILED:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print("Documentation consistency check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
