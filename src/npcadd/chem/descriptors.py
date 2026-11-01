"""RDKit-computed physicochemical descriptors.

Every descriptor here is computed locally and deterministically from the
2D/3D structure. Nothing in this module calls a web service, and nothing is
estimated by a model: these are the inputs to the rule sets in
:mod:`npcadd.chem.rules`, and to the efficiency metrics in
:mod:`npcadd.stages.rank`.

Naming follows the field names used in the output tables, so the mapping from
table column to code is one-to-one and greppable.
"""

from __future__ import annotations

from typing import Final

from rdkit import Chem
from rdkit.Chem import QED, Crippen, Descriptors, Lipinski, rdMolDescriptors

__all__ = [
    "DESCRIPTOR_FIELDS",
    "DESCRIPTOR_UNITS",
    "compute_descriptors",
    "heavy_atom_count",
]

#: Canonical descriptor field order, used for table columns and golden tests.
DESCRIPTOR_FIELDS: Final[tuple[str, ...]] = (
    "mw",
    "exact_mw",
    "heavy_atoms",
    "clogp",
    "tpsa",
    "hbd",
    "hba",
    "rotatable_bonds",
    "rings",
    "aromatic_rings",
    "fraction_csp3",
    "formal_charge",
    "molar_refractivity",
    "qed",
    "n_atoms_total",
    "n_stereocentres",
    "n_unspecified_stereocentres",
)

#: Units for figure axis labels and the SI table legend.
DESCRIPTOR_UNITS: Final[dict[str, str]] = {
    "mw": "g/mol",
    "exact_mw": "g/mol",
    "heavy_atoms": "count",
    "clogp": "log units",
    "tpsa": "A^2",
    "hbd": "count",
    "hba": "count",
    "rotatable_bonds": "count",
    "rings": "count",
    "aromatic_rings": "count",
    "fraction_csp3": "fraction",
    "formal_charge": "e",
    "molar_refractivity": "cm^3/mol",
    "qed": "dimensionless (0-1)",
    "n_atoms_total": "count",
    "n_stereocentres": "count",
    "n_unspecified_stereocentres": "count",
}


def heavy_atom_count(mol: Chem.Mol) -> int:
    """Number of non-hydrogen atoms. The denominator of ligand efficiency."""
    return int(mol.GetNumHeavyAtoms())


def compute_descriptors(mol: Chem.Mol) -> dict[str, float]:
    """Compute the full descriptor panel for one molecule.

    Args:
        mol: A sanitised molecule. Hydrogens may be implicit or explicit; the
            counts that depend on hydrogens (``hbd``, ``n_atoms_total``) are
            computed on an explicit-H copy so the values do not depend on how
            the caller happened to represent the molecule.

    Returns:
        A dict keyed by :data:`DESCRIPTOR_FIELDS`. All values are floats so the
        table has a single numeric dtype; integral quantities are exact in
        float64 at these magnitudes.

    Raises:
        ValueError: if ``mol`` is None.
    """
    if mol is None:
        raise ValueError("compute_descriptors received None")

    with_h = Chem.AddHs(Chem.Mol(mol))

    try:
        stereo = Chem.FindMolChiralCenters(
            mol, includeUnassigned=True, useLegacyImplementation=False
        )
    except (RuntimeError, ValueError):  # pragma: no cover - defensive
        stereo = []
    n_stereo = len(stereo)
    n_unspecified = sum(1 for _idx, label in stereo if label == "?")

    values: dict[str, float] = {
        "mw": float(Descriptors.MolWt(mol)),
        "exact_mw": float(Descriptors.ExactMolWt(mol)),
        "heavy_atoms": float(heavy_atom_count(mol)),
        "clogp": float(Crippen.MolLogP(mol)),
        "tpsa": float(rdMolDescriptors.CalcTPSA(mol)),
        "hbd": float(Lipinski.NumHDonors(mol)),
        "hba": float(Lipinski.NumHAcceptors(mol)),
        "rotatable_bonds": float(Lipinski.NumRotatableBonds(mol)),
        "rings": float(rdMolDescriptors.CalcNumRings(mol)),
        "aromatic_rings": float(rdMolDescriptors.CalcNumAromaticRings(mol)),
        "fraction_csp3": float(rdMolDescriptors.CalcFractionCSP3(mol)),
        "formal_charge": float(Chem.GetFormalCharge(mol)),
        "molar_refractivity": float(Crippen.MolMR(mol)),
        "qed": float(QED.qed(mol)),
        "n_atoms_total": float(with_h.GetNumAtoms()),
        "n_stereocentres": float(n_stereo),
        "n_unspecified_stereocentres": float(n_unspecified),
    }

    missing = set(DESCRIPTOR_FIELDS) - values.keys()
    if missing:  # pragma: no cover - guards against field-list drift
        raise RuntimeError(f"Descriptor panel is incomplete: missing {sorted(missing)}")
    return {field: values[field] for field in DESCRIPTOR_FIELDS}
