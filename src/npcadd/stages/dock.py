"""Stage 4: screening.

Every accepted ligand is docked into every prepared receptor, and each
ligand-receptor pair's best pose is fingerprinted with ProLIF. Affinities are
calibrated against the receptor's null distribution so that every reported
number carries a z-score and an empirical p-value rather than standing alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from rdkit import Chem

from ..config import Config
from ..docking.grid import PosePlacement, assess_placement
from ..docking.interactions import (
    InteractionError,
    InteractionProfile,
    build_interaction_matrix,
    load_protein,
    profile_interactions,
)
from ..docking.metrics import NullDistribution
from ..docking.poses import PoseLoadError, load_poses
from ..docking.vina_runner import DockingJob, DockingResult, dock_many
from ..logging_utils import get_logger
from .ligands import LigandRecord
from .receptor import PreparedReceptor

__all__ = ["DockingSummary", "ScreenRow", "run_docking"]

log = get_logger("stages.dock")


@dataclass(slots=True)
class ScreenRow:
    """One ligand-receptor screening result."""

    ligand_id: str
    name: str
    compound_class: str
    area_percent: float
    receptor_id: str
    activity: str
    target_name: str
    ok: bool
    best_affinity_kcal_mol: float | None
    all_affinities: list[float] = field(default_factory=list)
    z_score: float | None = None
    empirical_p: float | None = None
    p_resolution: float | None = None
    n_contacts: int = 0
    contact_residues: str = ""
    contacts: str = ""
    placement_ok: bool | None = None
    placement_note: str = ""
    frac_atoms_in_box: float | None = None
    n_atoms_in_box: int | None = None
    pose_centroid_offset_a: float | None = None
    pose_centroid_offset_frac: float | None = None
    pose_max_atom_offset_frac: float | None = None
    pose_path: str = ""
    error: str = ""

    def as_record(self) -> dict[str, Any]:
        return {
            "ligand_id": self.ligand_id,
            "name": self.name,
            "compound_class": self.compound_class,
            "area_percent": self.area_percent,
            "receptor_id": self.receptor_id,
            "activity": self.activity,
            "target_name": self.target_name,
            "docking_ok": self.ok,
            "best_affinity_kcal_mol": self.best_affinity_kcal_mol,
            "all_affinities_kcal_mol": ";".join(f"{a:.3f}" for a in self.all_affinities),
            "z_score_vs_null": self.z_score,
            "empirical_p_vs_null": self.empirical_p,
            "p_value_resolution": self.p_resolution,
            "n_contacts": self.n_contacts,
            "contact_residues": self.contact_residues,
            "contacts": self.contacts,
            "placement_ok": self.placement_ok,
            "placement_note": self.placement_note,
            "frac_atoms_in_box": self.frac_atoms_in_box,
            "n_atoms_in_box": self.n_atoms_in_box,
            "pose_centroid_offset_a": self.pose_centroid_offset_a,
            "pose_centroid_offset_frac": self.pose_centroid_offset_frac,
            "pose_max_atom_offset_frac": self.pose_max_atom_offset_frac,
            "pose_path": self.pose_path,
            "error": self.error,
        }


@dataclass(slots=True)
class DockingSummary:
    """Aggregate outcome of the screening stage."""

    n_jobs: int
    n_ok: int
    n_failed: int
    n_from_cache: int
    n_interaction_profiles: int
    n_placement_flagged: int = 0
    interaction_matrix_path: str = ""
    results_path: str = ""

    def as_metrics(self) -> dict[str, Any]:
        return {
            "n_jobs": self.n_jobs,
            "n_ok": self.n_ok,
            "n_failed": self.n_failed,
            "n_from_cache": self.n_from_cache,
            "n_interaction_profiles": self.n_interaction_profiles,
            "n_placement_flagged": self.n_placement_flagged,
        }


def _build_jobs(
    ligands: list[LigandRecord], receptors: list[PreparedReceptor], out_dir: Path
) -> list[DockingJob]:
    jobs: list[DockingJob] = []
    for receptor in receptors:
        pose_dir = out_dir / "docking" / receptor.receptor_id
        for ligand in ligands:
            jobs.append(
                DockingJob(
                    ligand_id=ligand.ligand_id,
                    receptor_id=receptor.receptor_id,
                    # Manifest paths are relative to the run directory.
                    ligand_pdbqt=out_dir / ligand.pdbqt_path,
                    receptor_pdbqt=receptor.pdbqt_path,
                    box=receptor.box,
                    out_pdbqt=pose_dir / f"{ligand.ligand_id}_poses.pdbqt",
                    role="screen",
                )
            )
    return jobs


def _fingerprint_receptor(
    cfg: Config,
    receptor: PreparedReceptor,
    results: list[DockingResult],
) -> list[InteractionProfile]:
    """Fingerprint the best pose of every successful ligand for one receptor."""
    if not cfg.docking.interaction_fingerprints:
        return []

    try:
        protein = load_protein(receptor.cleaned_pdb_path)
    except InteractionError as exc:
        log.warning(
            "Interaction fingerprinting unavailable for this receptor",
            extra={"receptor": receptor.receptor_id, "error": str(exc)},
        )
        return []

    profiles: list[InteractionProfile] = []
    for result in results:
        if not result.ok or not result.out_pdbqt:
            continue
        try:
            poses = load_poses(result.out_pdbqt)
            profiles.extend(
                profile_interactions(
                    protein,
                    poses,
                    ligand_id=result.ligand_id,
                    receptor_id=receptor.receptor_id,
                    interactions=cfg.docking.prolif_interactions,
                    max_poses=1,
                )
            )
        except (PoseLoadError, InteractionError) as exc:
            log.warning(
                "Could not fingerprint ligand",
                extra={
                    "ligand": result.ligand_id,
                    "receptor": receptor.receptor_id,
                    "error": str(exc),
                },
            )
    return profiles


def _best_pose_heavy_coords(out_pdbqt: str) -> np.ndarray:
    """Heavy-atom coordinates of a ligand's best (rank-1) docked pose."""
    mol = Chem.RemoveHs(load_poses(out_pdbqt))
    if mol.GetNumConformers() == 0:  # pragma: no cover - load_poses guarantees >=1
        raise PoseLoadError(f"No conformers parsed from {out_pdbqt}")
    return np.asarray(mol.GetConformer(0).GetPositions(), dtype=np.float64)


def _assess_placements(
    cfg: Config,
    receptors: dict[str, PreparedReceptor],
    results: list[DockingResult],
) -> dict[tuple[str, str], PosePlacement]:
    """Placement verdict for the best pose of every successful docking.

    Independent of interaction fingerprinting so the check still runs when
    fingerprints are disabled. Returns an empty mapping when the check is off.
    """
    if not cfg.docking.placement_check:
        return {}
    placements: dict[tuple[str, str], PosePlacement] = {}
    for result in results:
        if not result.ok or not result.out_pdbqt:
            continue
        receptor = receptors.get(result.receptor_id)
        if receptor is None:  # pragma: no cover - results are built from receptors
            continue
        try:
            coords = _best_pose_heavy_coords(result.out_pdbqt)
            placements[(result.ligand_id, result.receptor_id)] = assess_placement(
                coords,
                receptor.box,
                tolerance=cfg.docking.placement_box_tolerance_a,
                min_fraction_inside=cfg.docking.placement_min_fraction_in_box,
                max_centroid_offset_frac=cfg.docking.placement_max_centroid_offset_frac,
            )
        except (PoseLoadError, ValueError) as exc:
            log.warning(
                "Could not assess pose placement",
                extra={
                    "ligand": result.ligand_id,
                    "receptor": result.receptor_id,
                    "error": str(exc),
                },
            )
    return placements


def run_docking(
    cfg: Config,
    ligands: list[LigandRecord],
    receptors: list[PreparedReceptor],
    out_dir: Path,
    nulls: dict[str, NullDistribution] | None = None,
) -> tuple[list[ScreenRow], DockingSummary]:
    """Dock every ligand into every receptor and assemble the screening table.

    Args:
        cfg: Run configuration.
        ligands: Accepted ligands from stage 1.
        receptors: Prepared receptors from stage 2.
        out_dir: Run directory.
        nulls: Per-receptor null distributions from stage 3. When a receptor has
            no null distribution, its z-scores and p-values are left as None
            rather than filled with a placeholder, so a missing calibration is
            visible in the output table.
    """
    nulls = nulls or {}
    jobs = _build_jobs(ligands, receptors, out_dir)
    if not jobs:
        raise RuntimeError("No docking jobs to run (no ligands or no receptors)")

    log.info(
        "Screening %d ligand(s) against %d receptor(s): %d jobs",
        len(ligands),
        len(receptors),
        len(jobs),
    )
    results = dock_many(jobs, cfg.docking)

    by_ligand = {lig.ligand_id: lig for lig in ligands}
    by_receptor = {rec.receptor_id: rec for rec in receptors}
    results_by_receptor: dict[str, list[DockingResult]] = {}
    for result in results:
        results_by_receptor.setdefault(result.receptor_id, []).append(result)

    profiles: list[InteractionProfile] = []
    for receptor_id, receptor_results in results_by_receptor.items():
        profiles.extend(_fingerprint_receptor(cfg, by_receptor[receptor_id], receptor_results))
    profile_index = {(p.ligand_id, p.receptor_id): p for p in profiles if p.pose_rank == 1}
    placements = _assess_placements(cfg, by_receptor, results)

    rows: list[ScreenRow] = []
    for result in results:
        ligand = by_ligand[result.ligand_id]
        receptor = by_receptor[result.receptor_id]
        row = ScreenRow(
            ligand_id=ligand.ligand_id,
            name=ligand.name,
            compound_class=ligand.compound_class,
            area_percent=ligand.area_percent,
            receptor_id=receptor.receptor_id,
            activity=receptor.activity,
            target_name=receptor.target_name,
            ok=result.ok,
            best_affinity_kcal_mol=result.best_affinity,
            all_affinities=result.affinities,
            pose_path=result.out_pdbqt,
            error=result.error,
        )

        null = nulls.get(receptor.receptor_id)
        if null is not None and result.best_affinity is not None:
            row.z_score = round(null.z_score(result.best_affinity), 3)
            row.empirical_p = round(null.empirical_p(result.best_affinity), 5)
            row.p_resolution = round(null.resolution, 5)

        profile = profile_index.get((ligand.ligand_id, receptor.receptor_id))
        if profile is not None:
            row.n_contacts = profile.n_contacts
            row.contact_residues = ";".join(profile.residues)
            row.contacts = ";".join(f"{r}:{k}" for r, k in sorted(profile.contacts))

        placement = placements.get((ligand.ligand_id, receptor.receptor_id))
        if placement is not None:
            row.placement_ok = placement.in_box
            row.placement_note = placement.note
            row.frac_atoms_in_box = round(placement.fraction_inside, 4)
            row.n_atoms_in_box = placement.n_inside
            row.pose_centroid_offset_a = round(placement.centroid_offset_a, 3)
            row.pose_centroid_offset_frac = round(placement.centroid_offset_frac, 4)
            row.pose_max_atom_offset_frac = round(placement.max_atom_offset_frac, 4)
        rows.append(row)

    results_path = out_dir / "docking_results.csv"
    pd.DataFrame([r.as_record() for r in rows]).to_csv(
        results_path, index=False, lineterminator="\n"
    )

    matrix_path = ""
    matrix = build_interaction_matrix(profiles)
    if not matrix.empty:
        path = out_dir / "interaction_matrix.csv"
        matrix.to_csv(path, lineterminator="\n")
        matrix_path = path.as_posix()

    n_placement_flagged = sum(1 for r in rows if r.placement_ok is False)
    summary = DockingSummary(
        n_jobs=len(results),
        n_ok=sum(1 for r in results if r.ok),
        n_failed=sum(1 for r in results if not r.ok),
        n_from_cache=sum(1 for r in results if r.from_cache),
        n_interaction_profiles=len(profiles),
        n_placement_flagged=n_placement_flagged,
        interaction_matrix_path=matrix_path,
        results_path=results_path.as_posix(),
    )
    if n_placement_flagged:
        log.warning(
            "%d screened pose(s) are placement-suspect (outside or hugging the search "
            "box); their affinities may reflect where the box was drawn rather than the "
            "pocket, and should not be trusted for ranking. See the placement_note column.",
            n_placement_flagged,
        )
    log.info("Screening complete", extra=summary.as_metrics())
    return rows, summary
