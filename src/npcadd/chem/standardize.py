"""Molecule standardisation built on RDKit's MolStandardize.

The pipeline is deliberately ordered. Order matters chemically:

1. **Sanitise / parse** — reject anything RDKit cannot read.
2. **Cleanup** — normalise functional-group drawing conventions (nitro, sulfoxide,
   azide, …) and reionise, so that the same chemistry drawn two ways converges.
3. **Largest-fragment selection** (salt stripping) — a GC-MS hit list is a list
   of neutral organics, but exported SMILES frequently carry counter-ions.
   Done *after* cleanup so fragment charges are already normalised.
4. **Uncharge** (neutralisation) — bring the molecule to its neutral form, which
   is the correct starting point before applying a pKa model at a chosen pH.
   Protonation is a separate, explicit step (see :mod:`npcadd.chem.protonation`).
5. **Canonical tautomer** — one representative per tautomer family, so that
   descriptors and docking are not silently computed on an arbitrary input
   tautomer.
6. **Stereo handling** — unspecified stereocentres are detected and reported,
   never invented.

Every rejection carries a machine-readable reason so that
``data/interim/rejected_ligands.csv`` is auditable.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from rdkit import Chem, RDLogger
from rdkit.Chem import Descriptors
from rdkit.Chem.MolStandardize import rdMolStandardize

from ..logging_utils import get_logger

__all__ = [
    "RejectionReason",
    "StandardisationResult",
    "Standardiser",
    "count_unspecified_stereocentres",
]

log = get_logger("chem.standardize")

# RDKit's C++ logger is noisy on malformed input; we surface our own messages
# and keep the structured log clean.
RDLogger.DisableLog("rdApp.*")


class RejectionReason(StrEnum):
    """Why a molecule did not make it into the screening set."""

    UNPARSEABLE_SMILES = "unparseable_smiles"
    EMPTY_SMILES = "empty_smiles"
    SANITISATION_FAILED = "sanitisation_failed"
    STANDARDISATION_FAILED = "standardisation_failed"
    TOO_FEW_HEAVY_ATOMS = "too_few_heavy_atoms"
    TOO_MANY_HEAVY_ATOMS = "too_many_heavy_atoms"
    DISALLOWED_ELEMENT = "disallowed_element"
    NO_CARBON = "no_carbon"
    MULTIPLE_FRAGMENTS_AFTER_CLEANUP = "multiple_fragments_after_cleanup"
    PROTONATION_FAILED = "protonation_failed"
    EMBEDDING_FAILED = "embedding_failed"
    MMFF_SETUP_FAILED = "mmff_setup_failed"
    PDBQT_EXPORT_FAILED = "pdbqt_export_failed"
    DUPLICATE_STRUCTURE = "duplicate_structure"


@dataclass(slots=True)
class StandardisationResult:
    """Outcome of standardising one input SMILES."""

    ok: bool
    mol: Chem.Mol | None
    input_smiles: str
    standard_smiles: str | None = None
    inchikey: str | None = None
    reason: RejectionReason | None = None
    detail: str = ""
    changed: bool = False
    n_unspecified_stereocentres: int = 0
    stripped_fragments: int = 0

    def as_record(self) -> dict[str, Any]:
        """Flatten for CSV export."""
        return {
            "input_smiles": self.input_smiles,
            "standard_smiles": self.standard_smiles or "",
            "inchikey": self.inchikey or "",
            "ok": self.ok,
            "reason": str(self.reason) if self.reason else "",
            "detail": self.detail,
            "changed_by_standardisation": self.changed,
            "n_unspecified_stereocentres": self.n_unspecified_stereocentres,
            "stripped_fragments": self.stripped_fragments,
        }


def count_unspecified_stereocentres(mol: Chem.Mol) -> int:
    """Count stereocentres whose configuration is not specified.

    Reported rather than resolved: assigning a configuration that the GC-MS
    identification does not support would be inventing data. Downstream, each
    unspecified centre is a documented caveat on that compound's pose.
    """
    try:
        centres = Chem.FindMolChiralCenters(
            mol, includeUnassigned=True, useLegacyImplementation=False
        )
    except (RuntimeError, ValueError) as exc:  # pragma: no cover - defensive
        log.debug("Stereocentre detection failed: %s", exc)
        return 0
    return sum(1 for _idx, label in centres if label == "?")


def _require(value: Chem.Mol | None, step: str) -> Chem.Mol:
    """Return ``value`` or raise, so the standardisation try-block stays flat.

    RDKit's standardiser calls signal failure by returning None rather than
    raising. Converting that to an exception in one place keeps the pipeline in
    :meth:`Standardiser.standardise_mol` linear and gives a uniform error type.
    """
    if value is None:
        raise ValueError(f"{step} returned None")
    return value


class Standardiser:
    """Reusable, deterministic standardisation pipeline.

    The RDKit standardiser objects are constructed once and reused: they carry
    internal transform catalogues whose construction is the expensive part, and
    reuse also guarantees every molecule sees an identical rule set.
    """

    def __init__(
        self,
        *,
        strip_salts: bool = True,
        neutralise: bool = True,
        canonical_tautomer: bool = True,
        max_tautomers: int = 1000,
        min_heavy_atoms: int = 6,
        max_heavy_atoms: int = 100,
        allowed_elements: frozenset[str] | None = None,
    ) -> None:
        self.strip_salts = strip_salts
        self.neutralise = neutralise
        self.canonical_tautomer = canonical_tautomer
        self.min_heavy_atoms = min_heavy_atoms
        self.max_heavy_atoms = max_heavy_atoms
        self.allowed_elements = allowed_elements or frozenset(
            {"C", "H", "N", "O", "S", "P", "F", "Cl", "Br", "I"}
        )

        self._uncharger = rdMolStandardize.Uncharger()
        self._fragment_chooser = rdMolStandardize.LargestFragmentChooser()
        params = rdMolStandardize.CleanupParameters()
        params.maxTautomers = max_tautomers
        self._tautomer_enumerator = rdMolStandardize.TautomerEnumerator(params)
        self._cleanup_params = params

    def standardise_smiles(self, smiles: str) -> StandardisationResult:
        """Standardise one SMILES string."""
        raw = (smiles or "").strip()
        if not raw:
            return StandardisationResult(
                ok=False,
                mol=None,
                input_smiles=smiles,
                reason=RejectionReason.EMPTY_SMILES,
                detail="SMILES field was empty or whitespace",
            )

        mol = Chem.MolFromSmiles(raw, sanitize=True)
        if mol is None:
            return StandardisationResult(
                ok=False,
                mol=None,
                input_smiles=raw,
                reason=RejectionReason.UNPARSEABLE_SMILES,
                detail="RDKit could not parse the SMILES",
            )
        return self.standardise_mol(mol, input_smiles=raw)

    def standardise_mol(self, mol: Chem.Mol, input_smiles: str = "") -> StandardisationResult:
        """Standardise an already-parsed molecule."""
        original = input_smiles or Chem.MolToSmiles(mol)
        n_fragments_before = len(Chem.GetMolFrags(mol))

        try:
            work = _require(rdMolStandardize.Cleanup(mol, self._cleanup_params), "Cleanup")

            if self.strip_salts:
                work = _require(
                    self._fragment_chooser.choose(work), "LargestFragmentChooser.choose"
                )

            if self.neutralise:
                work = _require(self._uncharger.uncharge(work), "Uncharger.uncharge")

            if self.canonical_tautomer:
                work = _require(
                    self._tautomer_enumerator.Canonicalize(work),
                    "TautomerEnumerator.Canonicalize",
                )

            Chem.SanitizeMol(work)
            Chem.AssignStereochemistry(work, cleanIt=True, force=True)
        except (ValueError, RuntimeError, Chem.KekulizeException, Chem.AtomValenceException) as exc:
            return StandardisationResult(
                ok=False,
                mol=None,
                input_smiles=original,
                reason=RejectionReason.STANDARDISATION_FAILED,
                detail=f"{type(exc).__name__}: {exc}",
            )

        n_fragments_after = len(Chem.GetMolFrags(work))
        if n_fragments_after > 1:
            return StandardisationResult(
                ok=False,
                mol=None,
                input_smiles=original,
                reason=RejectionReason.MULTIPLE_FRAGMENTS_AFTER_CLEANUP,
                detail=f"{n_fragments_after} disconnected fragments remain after cleanup",
            )

        gate = self._check_composition(work)
        if gate is not None:
            reason, detail = gate
            return StandardisationResult(
                ok=False,
                mol=None,
                input_smiles=original,
                reason=reason,
                detail=detail,
            )

        standard_smiles = Chem.MolToSmiles(work)
        try:
            inchikey: str | None = Chem.MolToInchiKey(work) or None
        except (RuntimeError, ValueError):  # pragma: no cover - InChI edge cases
            inchikey = None

        return StandardisationResult(
            ok=True,
            mol=work,
            input_smiles=original,
            standard_smiles=standard_smiles,
            inchikey=inchikey,
            changed=standard_smiles != Chem.MolToSmiles(mol),
            n_unspecified_stereocentres=count_unspecified_stereocentres(work),
            stripped_fragments=max(0, n_fragments_before - n_fragments_after),
        )

    def _check_composition(self, mol: Chem.Mol) -> tuple[RejectionReason, str] | None:
        """Apply size and element gates. Returns None when the molecule passes."""
        heavy = mol.GetNumHeavyAtoms()
        if heavy < self.min_heavy_atoms:
            return (
                RejectionReason.TOO_FEW_HEAVY_ATOMS,
                f"{heavy} heavy atoms < minimum {self.min_heavy_atoms}",
            )
        if heavy > self.max_heavy_atoms:
            return (
                RejectionReason.TOO_MANY_HEAVY_ATOMS,
                f"{heavy} heavy atoms > maximum {self.max_heavy_atoms}",
            )

        symbols = {atom.GetSymbol() for atom in mol.GetAtoms()}
        disallowed = symbols - self.allowed_elements
        if disallowed:
            return (
                RejectionReason.DISALLOWED_ELEMENT,
                f"contains disallowed element(s): {sorted(disallowed)}",
            )
        if "C" not in symbols:
            return (
                RejectionReason.NO_CARBON,
                "no carbon atoms; not an organic small molecule",
            )
        return None

    @staticmethod
    def molecular_weight(mol: Chem.Mol) -> float:
        """Exact average molecular weight, for convenience in dedup reporting."""
        return float(Descriptors.MolWt(mol))
