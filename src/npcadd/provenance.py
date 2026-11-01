"""Run provenance capture.

A ``provenance.json`` is written into every run directory. It records enough to
answer the only question a reviewer really has: *what exactly produced these
numbers, and can I get them again?*

Captured: git commit SHA and dirty flag, config hash and verbatim config,
SHA256 of every declared input and output, resolved versions of every
scientifically relevant package, the interpreter, OS and CPU, the UTC
timestamp, and every random seed in play.
"""

from __future__ import annotations

import importlib.metadata as md
import json
import os
import platform
import subprocess
import sys
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import __version__
from .hashing import hash_file, hash_many_files, hash_obj
from .logging_utils import get_logger

__all__ = ["Provenance", "git_state", "package_versions", "platform_state"]

log = get_logger("provenance")

#: Packages whose versions materially affect numerical output.
TRACKED_PACKAGES: tuple[str, ...] = (
    "npcadd",
    "rdkit",
    "meeko",
    "vina",
    "prolif",
    "gemmi",
    "dimorphite-dl",
    "pdb2pqr",
    "propka",
    "pdbfixer",
    "openmm",
    "numpy",
    "scipy",
    "pandas",
    "scikit-learn",
    "matplotlib",
    "openpyxl",
    "pydantic",
    "MDAnalysis",
)


def package_versions(packages: Iterable[str] = TRACKED_PACKAGES) -> dict[str, str]:
    """Resolve installed versions for the tracked packages.

    A package that is not installed is reported as ``"<not installed>"`` rather
    than omitted, so the absence is itself part of the record.
    """
    out: dict[str, str] = {}
    for name in packages:
        try:
            out[name] = md.version(name)
        except md.PackageNotFoundError:
            out[name] = "<not installed>"
    return dict(sorted(out.items()))


def _git(args: list[str], cwd: Path) -> str | None:
    """Run a git command, returning stripped stdout or None on any failure."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("git %s failed: %s", " ".join(args), exc)
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def git_state(repo_root: Path | None = None) -> dict[str, Any]:
    """Capture the git commit, branch and working-tree cleanliness.

    A dirty working tree is recorded explicitly: results produced from
    uncommitted code are not reproducible from the commit alone, and the report
    says so rather than implying the commit is sufficient.
    """
    root = repo_root or Path(__file__).resolve().parents[2]
    commit = _git(["rev-parse", "HEAD"], root)
    status = _git(["status", "--porcelain"], root)
    return {
        "commit": commit or "<unavailable>",
        "branch": _git(["rev-parse", "--abbrev-ref", "HEAD"], root) or "<unavailable>",
        "describe": _git(["describe", "--always", "--dirty"], root) or "<unavailable>",
        "dirty": bool(status) if status is not None else None,
        "dirty_files": sorted(line[3:] for line in (status or "").splitlines() if line[3:])
        if status
        else [],
        "repo_root": root.as_posix(),
    }


def platform_state() -> dict[str, Any]:
    """Capture interpreter, OS and CPU characteristics."""
    return {
        "python_version": sys.version.split()[0],
        "python_implementation": platform.python_implementation(),
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "processor": platform.processor() or "<unavailable>",
        "cpu_count_logical": os.cpu_count(),
        "in_container": Path("/.dockerenv").exists(),
    }


@dataclass(slots=True)
class Provenance:
    """Accumulates provenance for one run and writes it as JSON.

    Usage::

        prov = Provenance.start(run_id, cfg)
        prov.add_input(cfg.ligands.input_csv)
        ...
        prov.add_output(path)
        prov.add_stage("prepare-ligands", {"n_accepted": 97})
        prov.write(run_dir / "provenance.json")
    """

    run_id: str
    config_hash: str
    config_yaml: str
    config_source: str
    started_at_utc: str
    seeds: dict[str, int]
    inputs: dict[str, str] = field(default_factory=dict)
    outputs: dict[str, str] = field(default_factory=dict)
    stages: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    external_tools: list[dict[str, str]] = field(default_factory=list)
    _t0: float = field(default_factory=time.monotonic)

    @classmethod
    def start(cls, run_id: str, cfg: Any) -> Provenance:
        """Begin provenance capture for ``cfg`` (a :class:`~npcadd.config.Config`)."""
        return cls(
            run_id=run_id,
            config_hash=cfg.config_hash(),
            config_yaml=cfg.to_yaml(),
            config_source=(cfg.source_path.as_posix() if cfg.source_path else "<in-memory>"),
            started_at_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            seeds={
                "ligand_embed_seed": cfg.ligands.embed_seed,
                "docking_seed": cfg.docking.seed,
                "decoy_seed": cfg.validation.decoy_seed,
                "null_seed": cfg.validation.null_seed,
                "sensitivity_seed": cfg.ranking.sensitivity_seed,
            },
        )

    def add_input(self, path: str | Path) -> None:
        """Hash and record an input file."""
        p = Path(path)
        self.inputs[p.as_posix()] = hash_file(p) if p.is_file() else "<missing>"

    def add_inputs(self, paths: Iterable[str | Path]) -> None:
        self.inputs.update(hash_many_files(paths))

    def add_output(self, path: str | Path) -> None:
        """Hash and record an output file."""
        p = Path(path)
        self.outputs[p.as_posix()] = hash_file(p) if p.is_file() else "<missing>"

    def add_outputs(self, paths: Iterable[str | Path]) -> None:
        self.outputs.update(hash_many_files(paths))

    def add_stage(self, name: str, metrics: Mapping[str, Any] | None = None) -> None:
        """Record that a stage completed, with its summary metrics."""
        self.stages.append(
            {
                "stage": name,
                "finished_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "elapsed_s": round(time.monotonic() - self._t0, 3),
                "metrics": dict(metrics or {}),
            }
        )

    def add_warning(self, message: str) -> None:
        """Record a methodological caveat that must surface in the report."""
        if message not in self.warnings:
            self.warnings.append(message)
        log.warning("%s", message)

    def add_external_tool(
        self, tool: str, version: str, retrieved_utc: str, notes: str = ""
    ) -> None:
        """Record a manual retrieval from an HTML-form-only tool."""
        self.external_tools.append(
            {
                "tool": tool,
                "version": version,
                "retrieved_utc": retrieved_utc,
                "notes": notes,
            }
        )

    def to_dict(self) -> dict[str, Any]:
        """Assemble the full provenance record."""
        record: dict[str, Any] = {
            "schema": "npcadd/provenance/1",
            "npcadd_version": __version__,
            "run_id": self.run_id,
            "started_at_utc": self.started_at_utc,
            "written_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "wallclock_s": round(time.monotonic() - self._t0, 3),
            "config": {
                "hash": self.config_hash,
                "source": self.config_source,
                "yaml": self.config_yaml,
            },
            "seeds": dict(sorted(self.seeds.items())),
            "git": git_state(),
            "platform": platform_state(),
            "packages": package_versions(),
            "inputs": dict(sorted(self.inputs.items())),
            "outputs": dict(sorted(self.outputs.items())),
            "stages": self.stages,
            "warnings": self.warnings,
            "external_tools": self.external_tools,
        }
        # A content hash over everything except volatile timing fields, so two
        # runs of identical code on identical inputs produce the same value.
        stable = {
            k: v
            for k, v in record.items()
            if k not in {"written_at_utc", "started_at_utc", "wallclock_s", "stages", "run_id"}
        }
        stable["stage_names"] = [s["stage"] for s in self.stages]
        stable["stage_metrics"] = [s["metrics"] for s in self.stages]
        record["content_hash"] = hash_obj(stable)
        return record

    def write(self, path: str | Path) -> Path:
        """Write ``provenance.json`` and return its path."""
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2, sort_keys=True, ensure_ascii=False)
            fh.write("\n")
        log.info("Wrote provenance to %s", out, extra={"path": out.as_posix()})
        return out
