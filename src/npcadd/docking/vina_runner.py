"""AutoDock Vina execution: deterministic, resumable and parallel.

Three design points worth stating explicitly.

**Determinism.** The seed comes from config and is passed to every Vina
instance. Vina's search is stochastic; with a fixed seed, a fixed receptor and
a fixed ligand it is reproducible. Thread count can perturb results because
Vina's parallel search interleaves work, so the worker and CPU counts are part
of the config and are recorded in provenance.

**Map reuse.** ``compute_vina_maps`` is the expensive setup and depends only on
the receptor and the box. Within a worker, maps are computed once per receptor
and reused across every ligand docked against it. On the bundled sample this
is the difference between ~25 s and ~6 s per small ligand.

**Resumability.** A ligand-receptor pair is skipped when its output exists and
the recorded input fingerprint still matches. The fingerprint covers the
receptor PDBQT hash, the ligand PDBQT hash, the box, and every docking
parameter — so changing the exhaustiveness or nudging the box invalidates the
cache rather than quietly mixing settings within one results table.
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..config import DockingConfig
from ..hashing import hash_file, hash_obj
from ..logging_utils import get_logger
from .grid import ResolvedBox

__all__ = [
    "DockingError",
    "DockingJob",
    "DockingResult",
    "dock_many",
    "dock_one",
    "effective_cpu_budget",
    "job_fingerprint",
]

log = get_logger("docking.vina")


class DockingError(RuntimeError):
    """A docking run failed."""


@dataclass(slots=True, frozen=True)
class DockingJob:
    """One ligand docked into one receptor."""

    ligand_id: str
    receptor_id: str
    ligand_pdbqt: Path
    receptor_pdbqt: Path
    box: ResolvedBox
    out_pdbqt: Path
    role: str = "screen"  # screen | redock | decoy | null

    @property
    def fingerprint_path(self) -> Path:
        return self.out_pdbqt.with_suffix(".fingerprint.json")


@dataclass(slots=True)
class DockingResult:
    """Outcome of one docking job."""

    ligand_id: str
    receptor_id: str
    role: str
    ok: bool
    affinities: list[float] = field(default_factory=list)
    intermolecular: list[float] = field(default_factory=list)
    out_pdbqt: str = ""
    n_poses: int = 0
    elapsed_s: float = 0.0
    from_cache: bool = False
    error: str = ""

    @property
    def best_affinity(self) -> float | None:
        return self.affinities[0] if self.affinities else None

    def as_record(self) -> dict[str, Any]:
        return {
            "ligand_id": self.ligand_id,
            "receptor_id": self.receptor_id,
            "role": self.role,
            "ok": self.ok,
            "best_affinity_kcal_mol": self.best_affinity,
            "all_affinities_kcal_mol": ";".join(f"{a:.3f}" for a in self.affinities),
            "n_poses": self.n_poses,
            "out_pdbqt": self.out_pdbqt,
            "elapsed_s": round(self.elapsed_s, 2),
            "from_cache": self.from_cache,
            "error": self.error,
        }


def job_fingerprint(job: DockingJob, docking: DockingConfig) -> str:
    """Hash every input that can change this job's numerical result."""
    return hash_obj(
        {
            "ligand_pdbqt_sha256": hash_file(job.ligand_pdbqt),
            "receptor_pdbqt_sha256": hash_file(job.receptor_pdbqt),
            "box_center": [round(c, 6) for c in job.box.center],
            "box_size": [round(s, 6) for s in job.box.size],
            "engine": docking.engine,
            "scoring": docking.scoring,
            "seed": docking.seed,
            "exhaustiveness": docking.exhaustiveness,
            "n_poses": docking.n_poses,
            "max_evals": docking.max_evals,
            "cpu_per_worker": docking.cpu_per_worker,
        }
    )


def _cached_result(job: DockingJob, docking: DockingConfig) -> DockingResult | None:
    """Return a cached result when the stored fingerprint still matches."""
    if not (job.out_pdbqt.is_file() and job.fingerprint_path.is_file()):
        return None
    try:
        stored = json.loads(job.fingerprint_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if stored.get("fingerprint") != job_fingerprint(job, docking):
        log.info(
            "Inputs changed; re-docking",
            extra={"ligand": job.ligand_id, "receptor": job.receptor_id},
        )
        return None
    return DockingResult(
        ligand_id=job.ligand_id,
        receptor_id=job.receptor_id,
        role=job.role,
        ok=True,
        affinities=[float(a) for a in stored.get("affinities", [])],
        intermolecular=[float(a) for a in stored.get("intermolecular", [])],
        out_pdbqt=job.out_pdbqt.as_posix(),
        n_poses=int(stored.get("n_poses", 0)),
        elapsed_s=0.0,
        from_cache=True,
    )


class _ReceptorMaps:
    """Per-process cache of a prepared Vina instance with computed maps."""

    def __init__(self) -> None:
        self._key: tuple[str, tuple[float, ...], tuple[float, ...], str, int, int] | None = None
        self._vina: Any = None

    def get(self, job: DockingJob, docking: DockingConfig, cpu: int) -> Any:
        from vina import Vina

        key = (
            job.receptor_pdbqt.as_posix(),
            tuple(round(c, 6) for c in job.box.center),
            tuple(round(s, 6) for s in job.box.size),
            docking.scoring,
            docking.seed,
            cpu,
        )
        if self._key == key and self._vina is not None:
            return self._vina

        engine = Vina(
            sf_name=docking.scoring,
            cpu=cpu,
            seed=docking.seed,
            verbosity=0,
        )
        engine.set_receptor(str(job.receptor_pdbqt))
        engine.compute_vina_maps(center=list(job.box.center), box_size=list(job.box.size))
        self._key = key
        self._vina = engine
        log.debug(
            "Computed Vina maps",
            extra={"receptor": job.receptor_id, "pid": os.getpid()},
        )
        return engine


_MAPS = _ReceptorMaps()


def effective_cpu_budget(docking: DockingConfig, n_jobs: int) -> int:
    """CPUs to give a single Vina instance, given how many jobs there are.

    npcadd parallelises across docking *jobs*, so each worker normally gets
    ``cpu_per_worker`` threads. But when there are fewer jobs than workers --
    most importantly the single redocking job in the validation gate -- that
    leaves most of the CPU budget idle and makes the mandatory gate the slowest
    part of a run. Measured: the bundled redock takes ~86 s on 4 CPUs and ~5.5
    min on 1.

    So a job that has the machine to itself is given the whole budget
    (``workers * cpu_per_worker``). This changes wall-clock only; Vina's seed is
    unchanged. Thread count can perturb Vina's parallel search at the last
    decimal, which is why the effective budget is recorded in provenance along
    with the configured values -- see LIMITATIONS.md section 4.5.
    """
    if n_jobs <= 0:
        raise ValueError("n_jobs must be positive")
    budget = docking.workers * docking.cpu_per_worker
    if n_jobs >= docking.workers:
        return docking.cpu_per_worker
    # Spread the whole budget over the jobs that actually exist.
    return max(docking.cpu_per_worker, budget // n_jobs)


def dock_one(
    job: DockingJob, docking: DockingConfig, cpu_override: int | None = None
) -> DockingResult:
    """Dock one ligand into one receptor.

    Never raises for an ordinary docking failure: the failure is returned as a
    :class:`DockingResult` with ``ok=False`` and an ``error`` string, so one bad
    ligand does not abort a screen of a hundred. Configuration and filesystem
    problems do raise, because those affect every job.
    """
    import time

    if docking.resume:
        cached = _cached_result(job, docking)
        if cached is not None:
            return cached

    for path, label in ((job.ligand_pdbqt, "ligand"), (job.receptor_pdbqt, "receptor")):
        if not path.is_file():
            raise DockingError(f"{label} PDBQT does not exist: {path}")

    job.out_pdbqt.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()

    try:
        engine = _MAPS.get(job, docking, cpu_override or docking.cpu_per_worker)
        engine.set_ligand_from_file(str(job.ligand_pdbqt))
        if docking.max_evals > 0:
            engine.dock(
                exhaustiveness=docking.exhaustiveness,
                n_poses=docking.n_poses,
                max_evals=docking.max_evals,
            )
        else:
            engine.dock(exhaustiveness=docking.exhaustiveness, n_poses=docking.n_poses)
        engine.write_poses(str(job.out_pdbqt), n_poses=docking.n_poses, overwrite=True)
        energies = engine.energies(n_poses=docking.n_poses)
    except (RuntimeError, ValueError, OSError, TypeError) as exc:
        elapsed = time.monotonic() - started
        log.warning(
            "Docking failed",
            extra={
                "ligand": job.ligand_id,
                "receptor": job.receptor_id,
                "error": f"{type(exc).__name__}: {exc}",
            },
        )
        return DockingResult(
            ligand_id=job.ligand_id,
            receptor_id=job.receptor_id,
            role=job.role,
            ok=False,
            elapsed_s=elapsed,
            error=f"{type(exc).__name__}: {exc}",
        )

    affinities = [float(row[0]) for row in energies]
    intermolecular = [float(row[1]) for row in energies] if len(energies[0]) > 1 else []
    elapsed = time.monotonic() - started

    job.fingerprint_path.write_text(
        json.dumps(
            {
                "fingerprint": job_fingerprint(job, docking),
                "affinities": affinities,
                "intermolecular": intermolecular,
                "n_poses": len(affinities),
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    return DockingResult(
        ligand_id=job.ligand_id,
        receptor_id=job.receptor_id,
        role=job.role,
        ok=True,
        affinities=affinities,
        intermolecular=intermolecular,
        out_pdbqt=job.out_pdbqt.as_posix(),
        n_poses=len(affinities),
        elapsed_s=elapsed,
    )


def _worker(payload: tuple[dict[str, Any], dict[str, Any], int]) -> dict[str, Any]:
    """Process-pool entry point. Takes and returns plain dicts for picklability."""
    job_dict, docking_dict, cpu = payload
    job = DockingJob(
        ligand_id=job_dict["ligand_id"],
        receptor_id=job_dict["receptor_id"],
        ligand_pdbqt=Path(job_dict["ligand_pdbqt"]),
        receptor_pdbqt=Path(job_dict["receptor_pdbqt"]),
        box=ResolvedBox(
            center=tuple(job_dict["box_center"]),
            size=tuple(job_dict["box_size"]),
            mode=job_dict["box_mode"],
            derivation=job_dict["box_derivation"],
        ),
        out_pdbqt=Path(job_dict["out_pdbqt"]),
        role=job_dict["role"],
    )
    return asdict(dock_one(job, DockingConfig.model_validate(docking_dict), cpu))


def _to_payload(
    job: DockingJob, docking: DockingConfig, cpu: int
) -> tuple[dict[str, Any], dict[str, Any], int]:
    return (
        {
            "ligand_id": job.ligand_id,
            "receptor_id": job.receptor_id,
            "ligand_pdbqt": job.ligand_pdbqt.as_posix(),
            "receptor_pdbqt": job.receptor_pdbqt.as_posix(),
            "box_center": list(job.box.center),
            "box_size": list(job.box.size),
            "box_mode": job.box.mode,
            "box_derivation": job.box.derivation,
            "out_pdbqt": job.out_pdbqt.as_posix(),
            "role": job.role,
        },
        docking.model_dump(mode="json"),
        cpu,
    )


def dock_many(
    jobs: list[DockingJob],
    docking: DockingConfig,
    progress_every: int = 10,
) -> list[DockingResult]:
    """Dock many jobs, in parallel when ``workers > 1``.

    Jobs are grouped by receptor before dispatch so that consecutive jobs in a
    worker share a receptor and therefore reuse its Vina maps.

    Results are returned in the same order as ``jobs``, independent of
    completion order, so downstream tables are deterministic.
    """
    if not jobs:
        return []

    ordered = sorted(
        range(len(jobs)), key=lambda i: (jobs[i].receptor_id, jobs[i].role, jobs[i].ligand_id)
    )
    results: dict[int, DockingResult] = {}
    cpu = effective_cpu_budget(docking, len(jobs))
    n_workers = min(docking.workers, len(jobs))
    if cpu != docking.cpu_per_worker:
        log.info(
            "Few jobs for the configured workers; widening the per-job CPU budget",
            extra={
                "n_jobs": len(jobs),
                "configured_workers": docking.workers,
                "configured_cpu_per_worker": docking.cpu_per_worker,
                "effective_cpu_per_job": cpu,
            },
        )

    if n_workers == 1:
        for done, index in enumerate(ordered, start=1):
            results[index] = dock_one(jobs[index], docking, cpu)
            if done % progress_every == 0 or done == len(ordered):
                log.info("Docked %d/%d", done, len(ordered))
    else:
        with ProcessPoolExecutor(max_workers=docking.workers) as pool:
            futures = {pool.submit(_worker, _to_payload(jobs[i], docking, cpu)): i for i in ordered}
            for done, future in enumerate(as_completed(futures), start=1):
                index = futures[future]
                try:
                    results[index] = DockingResult(**future.result())
                except Exception as exc:
                    job = jobs[index]
                    log.exception(
                        "Docking worker crashed",
                        extra={
                            "ligand": job.ligand_id,
                            "receptor": job.receptor_id,
                            "error": f"{type(exc).__name__}: {exc}",
                        },
                    )
                    results[index] = DockingResult(
                        ligand_id=job.ligand_id,
                        receptor_id=job.receptor_id,
                        role=job.role,
                        ok=False,
                        error=f"worker crashed: {type(exc).__name__}: {exc}",
                    )
                if done % progress_every == 0 or done == len(ordered):
                    log.info("Docked %d/%d", done, len(ordered))

    final = [results[i] for i in range(len(jobs))]
    n_failed = sum(1 for r in final if not r.ok)
    n_cached = sum(1 for r in final if r.from_cache)
    log.info(
        "Docking batch complete",
        extra={"n_jobs": len(final), "n_failed": n_failed, "n_from_cache": n_cached},
    )
    return final
