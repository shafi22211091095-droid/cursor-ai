"""Deterministic 3D conformer generation.

ETKDGv3 with an explicit ``randomSeed`` from config, MMFF94 optimisation, then
two-stage pruning:

* **Energy window** — conformers more than ``energy_window_kcal`` above the
  global minimum are discarded as thermally inaccessible.
* **RMSD pruning** — heavy-atom best-RMS (symmetry-aware, via
  ``GetBestRMS``) below ``prune_rms_thresh`` means the conformer is a duplicate.

Conformers are returned sorted by MMFF energy ascending, so index 0 is always
the lowest-energy accessible conformer. Determinism is the point of this module:
the same SMILES and the same seed give the same coordinates.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from rdkit import Chem
from rdkit.Chem import AllChem, rdMolAlign

from ..logging_utils import get_logger

__all__ = ["ConformerSet", "EmbeddingError", "generate_conformers"]

log = get_logger("chem.embed")


class EmbeddingError(RuntimeError):
    """Raised when no usable 3D conformer could be produced."""


@dataclass(slots=True)
class ConformerSet:
    """A molecule with hydrogens and an energy-ordered, pruned conformer set."""

    mol: Chem.Mol
    energies_kcal: list[float]
    conformer_ids: list[int]
    n_embedded: int
    n_after_energy_window: int
    n_after_rms_prune: int
    converged_all: bool
    seed: int
    force_field: str

    @property
    def n_conformers(self) -> int:
        return len(self.conformer_ids)

    @property
    def min_energy_kcal(self) -> float:
        if not self.energies_kcal:
            raise EmbeddingError("ConformerSet contains no conformers")
        return self.energies_kcal[0]

    def best_conformer_id(self) -> int:
        """Conformer id of the lowest-energy conformer."""
        if not self.conformer_ids:
            raise EmbeddingError("ConformerSet contains no conformers")
        return self.conformer_ids[0]


def generate_conformers(
    mol: Chem.Mol,
    *,
    n_conformers: int = 10,
    seed: int = 0xC0FFEE,
    mmff_max_iters: int = 2000,
    energy_window_kcal: float = 10.0,
    prune_rms_thresh: float = 0.5,
) -> ConformerSet:
    """Embed, optimise, prune and energy-order conformers for ``mol``.

    Args:
        mol: A sanitised molecule. Hydrogens are added internally; the input is
            not mutated.
        n_conformers: Number of conformers to attempt to embed.
        seed: ETKDGv3 random seed. Identical seeds give identical coordinates.
        mmff_max_iters: MMFF94 optimisation iteration cap.
        energy_window_kcal: Discard conformers this far above the minimum.
        prune_rms_thresh: Heavy-atom best-RMS duplicate threshold in Angstroms.

    Returns:
        A :class:`ConformerSet` ordered by ascending MMFF94 energy.

    Raises:
        EmbeddingError: if embedding or force-field setup produced nothing usable.
    """
    if n_conformers < 1:
        raise ValueError("n_conformers must be >= 1")
    if energy_window_kcal <= 0:
        raise ValueError("energy_window_kcal must be positive")

    work = Chem.AddHs(Chem.Mol(mol), addCoords=True)

    params = AllChem.ETKDGv3()
    params.randomSeed = int(seed)
    params.useSmallRingTorsions = True
    params.enforceChirality = True
    params.clearConfs = True
    # Pruning is applied after MMFF optimisation, not during embedding, so that
    # the geometry compared is the optimised geometry.
    params.pruneRmsThresh = -1.0

    conf_ids = list(AllChem.EmbedMultipleConfs(work, numConfs=int(n_conformers), params=params))
    if not conf_ids:
        # Retry once with random coordinates: ETKDG occasionally fails on
        # strained polycyclics (common among sesquiterpenoids).
        log.warning(
            "ETKDGv3 embedding produced no conformers; retrying with random coordinates",
            extra={"smiles": Chem.MolToSmiles(mol), "seed": seed},
        )
        params.useRandomCoords = True
        conf_ids = list(AllChem.EmbedMultipleConfs(work, numConfs=int(n_conformers), params=params))
    if not conf_ids:
        raise EmbeddingError(
            f"ETKDGv3 could not embed any conformer for {Chem.MolToSmiles(mol)!r} "
            f"(seed={seed}, requested={n_conformers})"
        )

    n_embedded = len(conf_ids)

    properties = AllChem.MMFFGetMoleculeProperties(work)
    if properties is None:
        raise EmbeddingError(
            f"MMFF94 could not be parameterised for {Chem.MolToSmiles(mol)!r}; "
            "the molecule contains an atom type MMFF does not cover."
        )

    results = AllChem.MMFFOptimizeMoleculeConfs(
        work, maxIters=int(mmff_max_iters), mmffVariant="MMFF94"
    )
    converged_all = all(flag == 0 for flag, _energy in results)
    if not converged_all:
        log.debug(
            "Some conformers did not converge within the iteration cap",
            extra={"n_unconverged": sum(1 for f, _ in results if f != 0)},
        )

    energies = [float(energy) for _flag, energy in results]
    order = sorted(range(len(conf_ids)), key=lambda i: energies[i])

    # Stage 1: energy window relative to the global minimum.
    e_min = energies[order[0]]
    within_window = [i for i in order if energies[i] - e_min <= energy_window_kcal]
    n_after_window = len(within_window)

    # Stage 2: symmetry-aware heavy-atom RMSD pruning against kept conformers.
    heavy_only = Chem.RemoveHs(Chem.Mol(work))
    kept: list[int] = []
    for idx in within_window:
        cid = conf_ids[idx]
        if _is_duplicate(heavy_only, cid, [conf_ids[k] for k in kept], prune_rms_thresh):
            continue
        kept.append(idx)

    if not kept:  # pragma: no cover - window always admits the minimum
        kept = [within_window[0]]

    final = Chem.Mol(work)
    final.RemoveAllConformers()
    final_ids: list[int] = []
    final_energies: list[float] = []
    for idx in kept:
        conformer = Chem.Conformer(work.GetConformer(conf_ids[idx]))
        new_id = final.AddConformer(conformer, assignId=True)
        final_ids.append(new_id)
        final_energies.append(energies[idx])

    return ConformerSet(
        mol=final,
        energies_kcal=final_energies,
        conformer_ids=final_ids,
        n_embedded=n_embedded,
        n_after_energy_window=n_after_window,
        n_after_rms_prune=len(final_ids),
        converged_all=converged_all,
        seed=int(seed),
        force_field="MMFF94",
    )


def _is_duplicate(
    heavy_mol: Chem.Mol,
    candidate_cid: int,
    kept_cids: list[int],
    threshold: float,
) -> bool:
    """True if ``candidate_cid`` is within ``threshold`` best-RMS of any kept conformer."""
    for kept_cid in kept_cids:
        try:
            rms = rdMolAlign.GetBestRMS(heavy_mol, heavy_mol, prbId=candidate_cid, refId=kept_cid)
        except (RuntimeError, ValueError) as exc:  # pragma: no cover - defensive
            log.debug("GetBestRMS failed, treating as distinct: %s", exc)
            continue
        if rms < threshold:
            return True
    return False


def conformer_coordinates(mol: Chem.Mol, conf_id: int) -> np.ndarray:
    """Return an ``(n_atoms, 3)`` float64 array of conformer coordinates."""
    conf = mol.GetConformer(conf_id)
    return np.asarray(conf.GetPositions(), dtype=np.float64)
