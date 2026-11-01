"""Protein-ligand interaction fingerprints via ProLIF.

Why the receptor is read with RDKit rather than MDAnalysis
---------------------------------------------------------
ProLIF can build its protein molecule from an MDAnalysis ``AtomGroup``, but
MDAnalysis infers bonds by interatomic distance, and that fails on real
protonated structures. On the bundled ABL1 receptor, PROPKA assigns His361 as
doubly protonated (HIP) at pH 7.4, which places its HD1 1.08 A from the
backbone amide hydrogen of Asp363. The distance guesser bonds those two
hydrogens to each other, and RDKit then rejects the molecule outright for
impossible hydrogen valence.

RDKit's own PDB reader with ``proximityBonding=True`` applies the same kind of
distance criterion but refuses hydrogen-hydrogen bonds, so it reads the
identical file cleanly. ``proximityBonding=False`` is not usable either: with no
CONECT records every residue comes out fragmented and ProLIF rejects it.

Interpretation
--------------
An interaction fingerprint is a geometric assertion, not an energetic one.
ProLIF reports that two groups satisfy a distance-and-angle criterion; it does
not say the interaction is favourable, nor how much it contributes to binding.
At resolutions worse than ~2.5 A, side-chain positions are not reliable enough
for this to be read at the level of individual residues, and
:mod:`npcadd.stages.receptor` attaches that warning to the receptor.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..logging_utils import get_logger

__all__ = [
    "InteractionError",
    "InteractionProfile",
    "build_interaction_matrix",
    "load_protein",
    "profile_interactions",
]

log = get_logger("docking.interactions")


class InteractionError(RuntimeError):
    """Interaction fingerprinting failed."""


@dataclass(slots=True)
class InteractionProfile:
    """Interactions detected for one ligand pose against one receptor."""

    ligand_id: str
    receptor_id: str
    pose_rank: int
    contacts: list[tuple[str, str]] = field(default_factory=list)

    @property
    def n_contacts(self) -> int:
        return len(self.contacts)

    @property
    def residues(self) -> list[str]:
        return sorted({residue for residue, _ in self.contacts})

    def counts_by_type(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for _residue, kind in self.contacts:
            counts[kind] = counts.get(kind, 0) + 1
        return dict(sorted(counts.items()))

    def as_record(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "ligand_id": self.ligand_id,
            "receptor_id": self.receptor_id,
            "pose_rank": self.pose_rank,
            "n_contacts": self.n_contacts,
            "n_contact_residues": len(self.residues),
            "contact_residues": ";".join(self.residues),
            "contacts": ";".join(f"{r}:{k}" for r, k in sorted(self.contacts)),
        }
        record.update({f"n_{k.lower()}": v for k, v in self.counts_by_type().items()})
        return record


def load_protein(pdb_path: str | Path) -> Any:
    """Load a protonated receptor PDB as a ProLIF ``Molecule``.

    Raises:
        InteractionError: if the file cannot be read as a usable protein.
    """
    import prolif
    from rdkit import Chem

    path = Path(pdb_path)
    if not path.is_file():
        raise InteractionError(f"Receptor PDB does not exist: {path}")

    mol = Chem.MolFromPDBFile(str(path), removeHs=False, proximityBonding=True, sanitize=True)
    if mol is None:
        raise InteractionError(
            f"RDKit could not read {path} as a molecule. Interaction fingerprinting "
            "needs a chemically valid, protonated receptor; check the protonation "
            "step's output for clashing or missing hydrogens."
        )
    try:
        return prolif.Molecule.from_rdkit(mol)
    except Exception as exc:
        raise InteractionError(
            f"ProLIF could not build a protein from {path}: {type(exc).__name__}: {exc}"
        ) from exc


def profile_interactions(
    protein: Any,
    ligand_mol: Any,
    ligand_id: str,
    receptor_id: str,
    interactions: Sequence[str],
    *,
    max_poses: int | None = None,
) -> list[InteractionProfile]:
    """Fingerprint every conformer of ``ligand_mol`` against ``protein``.

    Args:
        protein: Output of :func:`load_protein`.
        ligand_mol: An RDKit molecule whose conformers are the docked poses.
        ligand_id: Identifier for the output table.
        receptor_id: Identifier for the output table.
        interactions: ProLIF interaction names from config.
        max_poses: Limit the number of poses fingerprinted.

    Returns:
        One :class:`InteractionProfile` per pose, in pose order (rank 1 first).
    """
    import prolif

    n_conf = ligand_mol.GetNumConformers()
    if n_conf == 0:
        raise InteractionError(f"Ligand {ligand_id} has no conformers to fingerprint")
    limit = n_conf if max_poses is None else min(n_conf, max_poses)

    try:
        fingerprint = prolif.Fingerprint(list(interactions))
    except (ValueError, KeyError) as exc:
        raise InteractionError(
            f"Invalid ProLIF interaction list {list(interactions)}: {exc}. "
            f"Available: {sorted(prolif.Fingerprint.list_available())}"
        ) from exc

    profiles: list[InteractionProfile] = []
    for index in range(limit):
        try:
            pose = prolif.Molecule.from_rdkit(ligand_mol, conf_id=index)
            fingerprint.run_from_iterable([pose], protein, progress=False)
            frame = fingerprint.to_dataframe()
        except Exception as exc:  # noqa: BLE001 - ProLIF raises library-specific types
            log.warning(
                "Interaction fingerprinting failed for one pose",
                extra={
                    "ligand": ligand_id,
                    "receptor": receptor_id,
                    "pose": index + 1,
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            profiles.append(
                InteractionProfile(
                    ligand_id=ligand_id, receptor_id=receptor_id, pose_rank=index + 1
                )
            )
            continue

        contacts: list[tuple[str, str]] = []
        if not frame.empty:
            row = frame.iloc[0]
            for column, present in row.items():
                if not bool(present):
                    continue
                # Columns are (ligand, protein_residue, interaction_type).
                _lig, residue, kind = column
                contacts.append((str(residue), str(kind)))

        profiles.append(
            InteractionProfile(
                ligand_id=ligand_id,
                receptor_id=receptor_id,
                pose_rank=index + 1,
                contacts=contacts,
            )
        )
    return profiles


def build_interaction_matrix(
    profiles: Sequence[InteractionProfile], best_pose_only: bool = True
) -> pd.DataFrame:
    """Assemble a ligand x (residue, interaction) binary occupancy matrix.

    Rows are ligands, columns are ``residue|interaction`` pairs, values are 0/1.
    Columns are sorted by how many ligands make the contact, descending, so the
    conserved contacts of the binding site appear first.
    """
    selected = [p for p in profiles if (p.pose_rank == 1 or not best_pose_only)]
    if not selected:
        return pd.DataFrame()

    columns: set[str] = set()
    rows: dict[str, dict[str, int]] = {}
    for profile in selected:
        key = (
            profile.ligand_id if best_pose_only else f"{profile.ligand_id}#pose{profile.pose_rank}"
        )
        entry = rows.setdefault(key, {})
        for residue, kind in profile.contacts:
            column = f"{residue}|{kind}"
            columns.add(column)
            entry[column] = 1

    ordered_columns = sorted(columns)
    matrix = pd.DataFrame(
        [[rows[r].get(c, 0) for c in ordered_columns] for r in sorted(rows)],
        index=sorted(rows),
        columns=ordered_columns,
        dtype=int,
    )
    if matrix.empty:
        return matrix
    frequency = matrix.sum(axis=0).to_numpy()
    order = np.argsort(-frequency, kind="mergesort")
    return matrix.iloc[:, order]
