"""pKa-aware protonation at a specified pH.

Why this module exists
----------------------
Open Babel's ``-p`` flag applies a small set of generic SMARTS rules with
hard-coded pKa-like behaviour. It does not model the pKa of the actual molecule,
it is insensitive to electronic environment, and its output is not traceable to
any pKa estimate. Docking scores depend strongly on the ligand's formal charge
and hydrogen-bond donor/acceptor pattern, so a generic rule set is not an
acceptable basis for a published affinity.

This module therefore defines a *protonator interface* and plugs in
`dimorphite-dl <https://durrantlab.pitt.edu/dimorphite-dl/>`_, which enumerates
protonation states using empirically derived pKa ranges per ionisable
substructure. It is the strongest fully-offline, deterministic, open-source
option available, but it is still a substructure-pKa method rather than a
quantum or ML pKa predictor — see :class:`DimorphiteProtonator` for the exact
limitation statement that is propagated into LIMITATIONS.md.

To plug in a different model, implement :class:`Protonator` and register it in
:data:`PROTONATORS`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from rdkit import Chem

from ..logging_utils import get_logger

__all__ = [
    "PROTONATORS",
    "DimorphiteProtonator",
    "NullProtonator",
    "ProtonationError",
    "ProtonationResult",
    "Protonator",
    "get_protonator",
]

log = get_logger("chem.protonation")


class ProtonationError(RuntimeError):
    """Raised when a protonation backend cannot produce a usable state."""


@dataclass(slots=True, frozen=True)
class ProtonationResult:
    """A protonated form of one input molecule."""

    smiles: str
    mol: Chem.Mol
    formal_charge: int
    method: str
    method_version: str
    ph: float
    is_dominant: bool
    note: str = ""
    state_uncertain: bool = False
    uncertainty_detail: str = ""


@runtime_checkable
class Protonator(Protocol):
    """Interface every protonation backend must satisfy."""

    name: str
    version: str

    def limitation(self) -> str:
        """One-paragraph statement of what this backend cannot do.

        Propagated verbatim into the run report and LIMITATIONS.md, so a
        reviewer sees the caveat next to the numbers.
        """
        ...

    def protonate(self, mol: Chem.Mol, ph: float, max_variants: int = 1) -> list[ProtonationResult]:
        """Return protonation states at ``ph``, most-dominant first."""
        ...

    def state_is_uncertain(self, mol: Chem.Mol, ph: float, window: float = 1.0) -> tuple[bool, str]:
        """Whether the dominant state flips within +/- ``window`` pH units.

        A backend that applies no pKa model returns ``(False, "")``.
        """
        ...


class NullProtonator:
    """Pass-through backend: leaves the neutral standardised form untouched.

    Selected by ``ligands.protonation_method: none``. Honest but weaker: the
    ligand is docked as its neutral form regardless of pH. Carboxylic acids and
    aliphatic amines will carry the wrong charge at physiological pH.
    """

    name = "none"
    version = "n/a"

    def limitation(self) -> str:
        return (
            "No pKa model was applied. Ligands were docked in the neutral form produced "
            "by standardisation. For any compound bearing a carboxylic acid (pKa ~4-5) or "
            "an aliphatic amine (pKa ~9-10), the formal charge at pH 7.4 is therefore "
            "wrong, which affects electrostatic and hydrogen-bonding terms in the scoring "
            "function. Affinities for such compounds must not be compared with those of "
            "non-ionisable compounds."
        )

    def state_is_uncertain(
        self,
        mol: Chem.Mol,  # noqa: ARG002 - interface compliance
        ph: float,  # noqa: ARG002 - interface compliance
        window: float = 1.0,  # noqa: ARG002 - interface compliance
    ) -> tuple[bool, str]:
        """Always False: no pKa model is applied, so nothing can flip."""
        return False, ""

    def protonate(
        self,
        mol: Chem.Mol,
        ph: float,
        max_variants: int = 1,  # noqa: ARG002 - interface compliance
    ) -> list[ProtonationResult]:
        smiles = Chem.MolToSmiles(mol)
        return [
            ProtonationResult(
                smiles=smiles,
                mol=mol,
                formal_charge=Chem.GetFormalCharge(mol),
                method=self.name,
                method_version=self.version,
                ph=ph,
                is_dominant=True,
                note="no pKa model applied",
            )
        ]


class DimorphiteProtonator:
    """pKa-aware protonation via dimorphite-dl.

    dimorphite-dl enumerates ionisation states by matching ionisable
    substructures against a curated table of empirical pKa ranges, then emitting
    every state whose pKa window overlaps the requested pH window. Running it
    with ``ph_min == ph_max == ph`` yields the state(s) dominant at that pH.
    """

    name = "dimorphite_dl"

    def __init__(self) -> None:
        try:
            import dimorphite_dl
        except ImportError as exc:  # pragma: no cover - dependency is pinned
            raise ProtonationError(
                "dimorphite-dl is not installed, so no pKa-aware protonation backend is "
                "available. Install it, or set ligands.protonation_method: none and accept "
                "the documented limitation. npcadd will not silently fall back to generic "
                "protonation rules."
            ) from exc
        self._backend = dimorphite_dl
        self.version = getattr(dimorphite_dl, "__version__", "unknown")

    def limitation(self) -> str:
        return (
            "Protonation states were assigned with dimorphite-dl, which matches ionisable "
            "substructures against curated empirical pKa ranges. It is deterministic and "
            "fully offline, but it is a substructure-lookup method, not a per-molecule pKa "
            "calculation: it does not account for long-range electronic effects, intramolecular "
            "hydrogen bonding, or the microenvironment of the binding site. Reported states are "
            "the dominant macrostate(s) in bulk water at the configured pH, not site-specific "
            "protonation. Compounds whose pKa falls within ~1 unit of the configured pH are "
            "flagged, because for those the dominant state is not well determined and both "
            "states should be docked."
        )

    def state_is_uncertain(self, mol: Chem.Mol, ph: float, window: float = 1.0) -> tuple[bool, str]:
        """Detect an ionisable group whose dominant state flips near ``ph``.

        The backend does not expose the pKa it matched, so proximity is probed
        empirically: the dominant state is computed at ``ph - window`` and at
        ``ph + window``. If the formal charge differs between them, some group
        has a pKa inside the window and the state at ``ph`` is not well
        determined -- both states should be docked, and the compound is flagged
        so the report can say so instead of presenting one state as settled.

        Vanillin is the worked example: its phenol pKa is close to 7.4, so it is
        deprotonated at pH 7.4 by a hair, and that is a modelling choice rather
        than a fact about the molecule.
        """
        parent = Chem.MolToSmiles(mol)
        try:
            low = self._backend.protonate_smiles(
                parent,
                ph_min=ph - window,
                ph_max=ph - window,
                precision=0.0,
                max_variants=1,
            )
            high = self._backend.protonate_smiles(
                parent,
                ph_min=ph + window,
                ph_max=ph + window,
                precision=0.0,
                max_variants=1,
            )
        except (ValueError, RuntimeError, KeyError):  # pragma: no cover - defensive
            return False, ""
        if not low or not high:  # pragma: no cover - defensive
            return False, ""

        low_mol = Chem.MolFromSmiles(low[0])
        high_mol = Chem.MolFromSmiles(high[0])
        if low_mol is None or high_mol is None:  # pragma: no cover - defensive
            return False, ""
        low_charge = Chem.GetFormalCharge(low_mol)
        high_charge = Chem.GetFormalCharge(high_mol)
        if low_charge == high_charge:
            return False, ""
        return True, (
            f"formal charge changes from {low_charge:+d} at pH {ph - window:g} to "
            f"{high_charge:+d} at pH {ph + window:g}, so at least one ionisable group "
            f"has a pKa within {window:g} unit(s) of pH {ph:g} and the dominant state "
            f"at pH {ph:g} is not well determined"
        )

    def protonate(self, mol: Chem.Mol, ph: float, max_variants: int = 1) -> list[ProtonationResult]:
        if max_variants < 1:
            raise ValueError("max_variants must be >= 1")

        parent_smiles = Chem.MolToSmiles(mol)
        try:
            # precision=0.0 collapses the pKa window to the single requested pH,
            # so we get the dominant macrostate rather than every state within a
            # +/- 1 pKa-unit band. Widening is a configuration decision
            # (max_protonation_variants), not a silent default.
            states = self._backend.protonate_smiles(
                parent_smiles,
                ph_min=ph,
                ph_max=ph,
                precision=0.0,
                max_variants=max_variants,
                validate_output=True,
            )
        except (ValueError, RuntimeError, KeyError) as exc:
            raise ProtonationError(
                f"dimorphite-dl failed on {parent_smiles!r}: {type(exc).__name__}: {exc}"
            ) from exc

        if not states:
            raise ProtonationError(
                f"dimorphite-dl returned no protonation state for {parent_smiles!r}"
            )

        uncertain, detail = self.state_is_uncertain(mol, ph)

        results: list[ProtonationResult] = []
        for rank, smi in enumerate(states[:max_variants]):
            protonated = Chem.MolFromSmiles(smi)
            if protonated is None:
                log.warning(
                    "Discarding unparseable protonation state",
                    extra={"parent": parent_smiles, "state": smi},
                )
                continue
            results.append(
                ProtonationResult(
                    smiles=Chem.MolToSmiles(protonated),
                    mol=protonated,
                    formal_charge=Chem.GetFormalCharge(protonated),
                    method=self.name,
                    method_version=self.version,
                    ph=ph,
                    is_dominant=rank == 0,
                    state_uncertain=uncertain,
                    uncertainty_detail=detail,
                )
            )

        if not results:
            raise ProtonationError(f"All protonation states for {parent_smiles!r} were unparseable")
        return results


#: Registry of available backends. Add an entry to plug in a new pKa model.
PROTONATORS: dict[str, type[DimorphiteProtonator] | type[NullProtonator]] = {
    "dimorphite_dl": DimorphiteProtonator,
    "none": NullProtonator,
}


def get_protonator(method: str) -> Protonator:
    """Instantiate a protonation backend by name.

    Raises:
        ProtonationError: if the method is unknown. Unknown methods are never
            silently replaced with a default.
    """
    try:
        factory = PROTONATORS[method]
    except KeyError as exc:
        raise ProtonationError(
            f"Unknown protonation method {method!r}. Available: {sorted(PROTONATORS)}. "
            "npcadd does not substitute a different method than the one configured."
        ) from exc
    return factory()
