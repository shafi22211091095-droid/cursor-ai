"""Pose handling and redocking RMSD.

The single most important function here is :func:`in_place_rmsd`, and the
reason it exists as its own documented function is that getting this wrong is
a published-literature failure mode.

**Redocking RMSD must be computed without superposition.** The question a
redocking control asks is "did the docking engine put the ligand where the
crystal structure has it?". RDKit's ``GetBestRMS`` *aligns* the probe onto the
reference before measuring, which answers a different question — "is the
internal conformation similar?" — and will happily report a passing value for a
pose that is flipped end-for-end in the pocket.

Measured on the bundled ABL1/imatinib system: a pose that is in the correct
pocket but misoriented scores **12.3 A** by in-place RMSD and **2.1 A** by
``GetBestRMS``. With a 2.0 A gate, the aligned metric is within rounding
distance of passing a pose that is simply wrong. :func:`in_place_rmsd` uses
``CalcRMS``, which considers molecular symmetry and topological automorphisms
but performs no alignment.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from rdkit import Chem
from rdkit.Chem import rdMolAlign

from ..logging_utils import get_logger

__all__ = [
    "Pose",
    "PoseSet",
    "centroid_distance",
    "in_place_rmsd",
    "load_poses",
]

log = get_logger("docking.poses")


class PoseLoadError(RuntimeError):
    """A docked pose file could not be read back as a molecule."""


@dataclass(slots=True, frozen=True)
class Pose:
    """One docked pose."""

    rank: int
    affinity_kcal_mol: float
    rmsd_lb: float
    rmsd_ub: float

    @property
    def is_best(self) -> bool:
        return self.rank == 1


@dataclass(slots=True)
class PoseSet:
    """All poses for one ligand-receptor pair, with the molecule carrying them."""

    mol: Chem.Mol
    poses: list[Pose]
    pdbqt_path: Path

    @property
    def best(self) -> Pose:
        if not self.poses:
            raise PoseLoadError(f"No poses in {self.pdbqt_path}")
        return self.poses[0]

    @property
    def n_poses(self) -> int:
        return len(self.poses)

    def conformer_coords(self, index: int) -> np.ndarray:
        """Heavy-atom coordinates of pose ``index`` (0-based)."""
        return np.asarray(self.mol.GetConformer(index).GetPositions(), dtype=np.float64)


def load_poses(pdbqt_path: str | Path) -> Chem.Mol:
    """Read a multi-model Vina output PDBQT back into an RDKit molecule.

    Each docked pose becomes one conformer, in Vina's ranked order. Bond orders
    are recovered from the SMILES that Meeko embeds in the PDBQT ``REMARK``
    records, which is why ligands must be prepared through Meeko.

    Raises:
        PoseLoadError: if the file cannot be parsed into a molecule.
    """
    from meeko import PDBQTMolecule, RDKitMolCreate

    path = Path(pdbqt_path)
    if not path.is_file():
        raise PoseLoadError(f"Pose file does not exist: {path}")

    try:
        pdbqt = PDBQTMolecule.from_file(str(path), skip_typing=True)
        mols = RDKitMolCreate.from_pdbqt_mol(pdbqt)
    except (ValueError, KeyError, RuntimeError, OSError) as exc:
        raise PoseLoadError(
            f"Could not reconstruct a molecule from {path}: {type(exc).__name__}: {exc}"
        ) from exc

    usable = [m for m in mols if m is not None]
    if not usable:
        raise PoseLoadError(f"Meeko returned no usable molecule from {path}")
    if len(usable) > 1:
        log.warning(
            "Pose file contains multiple molecules; using the first",
            extra={"path": path.as_posix(), "n_molecules": len(usable)},
        )
    return usable[0]


def in_place_rmsd(
    probe: Chem.Mol,
    reference: Chem.Mol,
    probe_conf_id: int = 0,
    reference_conf_id: int = 0,
    *,
    heavy_atoms_only: bool = True,
) -> float:
    """Symmetry-corrected RMSD **without** superposition.

    This is the correct metric for a redocking control. See the module
    docstring for why alignment-based RMSD must not be used here.

    Args:
        probe: The docked molecule.
        reference: The crystallographic reference.
        probe_conf_id: Conformer index in ``probe``.
        reference_conf_id: Conformer index in ``reference``.
        heavy_atoms_only: Strip hydrogens before comparing. Hydrogen positions
            in a crystal structure are almost always modelled rather than
            observed, so including them measures the modelling, not the pose.

    Returns:
        RMSD in Angstroms.

    Raises:
        ValueError: if either molecule is None or lacks the requested conformer,
            or if the two molecules are not the same chemical species.
    """
    if probe is None or reference is None:
        raise ValueError("in_place_rmsd received None")

    prb = Chem.RemoveHs(Chem.Mol(probe)) if heavy_atoms_only else Chem.Mol(probe)
    ref = Chem.RemoveHs(Chem.Mol(reference)) if heavy_atoms_only else Chem.Mol(reference)

    if prb.GetNumConformers() <= probe_conf_id:
        raise ValueError(
            f"probe has {prb.GetNumConformers()} conformer(s); "
            f"conformer {probe_conf_id} was requested"
        )
    if ref.GetNumConformers() <= reference_conf_id:
        raise ValueError(
            f"reference has {ref.GetNumConformers()} conformer(s); "
            f"conformer {reference_conf_id} was requested"
        )

    if prb.GetNumAtoms() != ref.GetNumAtoms():
        raise ValueError(
            f"Cannot compare poses of different molecules: probe has "
            f"{prb.GetNumAtoms()} heavy atoms, reference has {ref.GetNumAtoms()}. "
            "A redocking control must dock the co-crystal ligand itself; check that "
            "the reference pose and the prepared ligand are the same species and "
            "protonation state."
        )

    try:
        # CalcRMS: considers symmetry and automorphisms, performs NO alignment.
        return float(rdMolAlign.CalcRMS(prb, ref, prbId=probe_conf_id, refId=reference_conf_id))
    except (RuntimeError, ValueError) as exc:
        raise ValueError(
            f"RMSD calculation failed; the two molecules may not be the same species: "
            f"{type(exc).__name__}: {exc}\n"
            f"  probe:     {Chem.MolToSmiles(prb)}\n"
            f"  reference: {Chem.MolToSmiles(ref)}"
        ) from exc


def centroid_distance(
    probe: Chem.Mol,
    reference: Chem.Mol,
    probe_conf_id: int = 0,
    reference_conf_id: int = 0,
) -> float:
    """Distance between pose centroids, in Angstroms.

    Reported alongside RMSD because the two separate "wrong pocket" from "right
    pocket, wrong orientation" — a distinction that a single RMSD number hides
    and that changes what a failure means.
    """
    prb = Chem.RemoveHs(Chem.Mol(probe))
    ref = Chem.RemoveHs(Chem.Mol(reference))
    a = np.asarray(prb.GetConformer(probe_conf_id).GetPositions(), dtype=np.float64)
    b = np.asarray(ref.GetConformer(reference_conf_id).GetPositions(), dtype=np.float64)
    return float(np.linalg.norm(a.mean(axis=0) - b.mean(axis=0)))
