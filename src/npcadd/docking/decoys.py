"""Property-matched decoy generation and random null sets.

Decoys
------
For each known active, select molecules from the enumerated pool that

* match the active on every property in ``decoy_property_tolerance``, and
* are **topologically dissimilar** to every active (Morgan/Tanimoto below
  ``max_similarity_to_actives``).

Both halves are necessary. Property matching alone yields decoys that a scoring
function can separate on size or polarity, inflating enrichment; dissimilarity
alone yields decoys that differ from the actives in ways the scoring function
can trivially exploit. The pair is the DUD-E construction, applied to an
offline pool.

Null set
--------
A random sample of the same pool, with no property constraints. It answers a
different question: what score does this receptor give a ligand that has no
reason to bind at all? That calibrates every reported affinity into a z-score
and an empirical p-value.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from rdkit import Chem, DataStructs
from rdkit.Chem import rdFingerprintGenerator

from ..chem.descriptors import compute_descriptors
from ..chem.standardize import Standardiser
from ..logging_utils import get_logger
from ..resources.building_blocks import describe_library, enumerate_triples

__all__ = [
    "DecoyGenerationError",
    "DecoySet",
    "PoolMolecule",
    "build_pool",
    "generate_decoys",
    "generate_null_set",
]

log = get_logger("docking.decoys")

#: Properties compared when matching a decoy to an active.
MATCH_PROPERTIES = ("mw", "clogp", "tpsa", "rotatable_bonds", "hbd", "hba", "formal_charge")


class DecoyGenerationError(RuntimeError):
    """Decoys could not be generated to the requested specification."""


@dataclass(slots=True, frozen=True)
class PoolMolecule:
    """One enumerated pool member with its descriptors precomputed."""

    pool_id: str
    smiles: str
    mol: Chem.Mol
    descriptors: dict[str, float]
    fingerprint: Any


@dataclass(slots=True)
class DecoySet:
    """Decoys selected for one active, with the shortfall recorded honestly."""

    active_id: str
    active_smiles: str
    decoys: list[PoolMolecule] = field(default_factory=list)
    requested: int = 0
    relaxations: int = 0

    @property
    def shortfall(self) -> int:
        return max(0, self.requested - len(self.decoys))

    def as_record(self) -> dict[str, Any]:
        return {
            "active_id": self.active_id,
            "active_smiles": self.active_smiles,
            "n_decoys_requested": self.requested,
            "n_decoys_selected": len(self.decoys),
            "shortfall": self.shortfall,
            "tolerance_relaxations": self.relaxations,
        }


def _generator() -> Any:
    return rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=2048)


def build_pool(
    max_size: int = 5000,
    *,
    min_heavy_atoms: int = 8,
    max_heavy_atoms: int = 45,
    max_rotatable_bonds: int = 10,
) -> list[PoolMolecule]:
    """Enumerate the bundled building blocks into a deterministic molecule pool.

    Enumeration order is fully determined by the sorted iteration below, so the
    pool is identical on every machine and no seed is involved. Molecules are
    standardised with the same :class:`~npcadd.chem.standardize.Standardiser`
    used for the screening set, so pool and screening descriptors are
    comparable.

    Args:
        max_size: Stop after this many accepted molecules.
        min_heavy_atoms: Reject fragments too small to be plausible ligands.
        max_heavy_atoms: Reject products too large.
        max_rotatable_bonds: Reject products more flexible than this. The cap
            serves two purposes. Scientifically, it keeps the null set
            comparable in character to a drug-like screening set, matching
            Veber's rotatable-bond criterion; practically, docking cost grows
            steeply with torsional degrees of freedom, and a handful of highly
            flexible pool members can dominate the wall-clock of an entire run
            (observed: minutes each at exhaustiveness 32 in a ~12,500 A^3 box,
            against ~6-30 s for a typical natural product).

    Raises:
        DecoyGenerationError: if the pool comes out empty.
    """
    standardiser = Standardiser(min_heavy_atoms=min_heavy_atoms, max_heavy_atoms=max_heavy_atoms)
    generator = _generator()
    pool: list[PoolMolecule] = []
    seen: set[str] = set()

    for core, sub_a, sub_b in enumerate_triples():
        if len(pool) >= max_size:
            log.info("Pool size cap reached", extra={"n_pool": len(pool), "cap": max_size})
            break
        smiles = core.format(a=sub_a, b=sub_b) if "{b}" in core else core.format(a=sub_a)
        result = standardiser.standardise_smiles(smiles)
        if not result.ok or result.mol is None or result.standard_smiles is None:
            continue
        if result.standard_smiles in seen:
            continue
        seen.add(result.standard_smiles)
        try:
            descriptors = compute_descriptors(result.mol)
        except (ValueError, RuntimeError):  # pragma: no cover - defensive
            continue
        if descriptors["rotatable_bonds"] > max_rotatable_bonds:
            continue
        pool.append(
            PoolMolecule(
                pool_id=f"P{len(pool):05d}",
                smiles=result.standard_smiles,
                mol=result.mol,
                descriptors=descriptors,
                fingerprint=generator.GetFingerprint(result.mol),
            )
        )

    if not pool:
        raise DecoyGenerationError(
            "The building-block enumeration produced no valid molecules. This is a "
            "bug in npcadd.resources.building_blocks, not a configuration problem."
        )
    log.info(
        "Built decoy/null pool",
        extra={"n_pool": len(pool), **describe_library()},
    )
    return pool


def _matches(
    candidate: Mapping[str, float],
    active: Mapping[str, float],
    tolerance: Mapping[str, float],
    scale: float,
) -> bool:
    """Whether ``candidate`` is within (scaled) tolerance of ``active``."""
    for prop in MATCH_PROPERTIES:
        allowed = tolerance.get(prop)
        if allowed is None:
            continue
        if abs(candidate[prop] - active[prop]) > allowed * scale:
            return False
    return True


def generate_decoys(
    actives: Sequence[tuple[str, Chem.Mol]],
    pool: list[PoolMolecule],
    *,
    n_per_active: int = 50,
    tolerance: Mapping[str, float] | None = None,
    seed: int = 1234,
    max_similarity_to_actives: float = 0.35,
    max_relaxations: int = 3,
) -> list[DecoySet]:
    """Select property-matched, topologically dissimilar decoys per active.

    When too few pool members match at the configured tolerance, the tolerance
    is widened by 1.5x up to ``max_relaxations`` times. Every relaxation is
    counted and reported: a decoy set that needed three relaxations is a weaker
    control than one that needed none, and the report says which it was rather
    than presenting both as "50 property-matched decoys".

    Args:
        actives: ``(active_id, mol)`` pairs.
        pool: Output of :func:`build_pool`.
        n_per_active: Decoys wanted per active.
        tolerance: Per-property absolute tolerances.
        seed: Seed for the selection among qualifying candidates.
        max_similarity_to_actives: Reject a candidate whose Tanimoto to any
            active is at or above this.
        max_relaxations: How many times the tolerance may be widened.

    Raises:
        DecoyGenerationError: if no actives were supplied.
    """
    if not actives:
        raise DecoyGenerationError(
            "No known actives were supplied, so no decoys can be generated and no "
            "enrichment metric is defined. Provide validation.known_actives_csv, or "
            "accept that the enrichment analysis will be reported as not performed."
        )
    if not pool:
        raise DecoyGenerationError("Decoy pool is empty")

    tol = dict(tolerance or {})
    generator = _generator()
    active_fps = [generator.GetFingerprint(mol) for _aid, mol in actives]
    active_descriptors = {aid: compute_descriptors(mol) for aid, mol in actives}

    # Precompute each candidate's maximum similarity to any active once.
    dissimilar: list[PoolMolecule] = []
    for candidate in pool:
        sims = DataStructs.BulkTanimotoSimilarity(candidate.fingerprint, active_fps)
        if max(sims) < max_similarity_to_actives:
            dissimilar.append(candidate)

    log.info(
        "Filtered pool by dissimilarity to actives",
        extra={
            "n_pool": len(pool),
            "n_dissimilar": len(dissimilar),
            "max_similarity_to_actives": max_similarity_to_actives,
        },
    )

    rng = random.Random(seed)
    used: set[str] = set()
    sets: list[DecoySet] = []

    for active_id, mol in actives:
        target = active_descriptors[active_id]
        chosen: list[PoolMolecule] = []
        relaxations = 0
        scale = 1.0

        while len(chosen) < n_per_active and relaxations <= max_relaxations:
            candidates = [
                c
                for c in dissimilar
                if c.pool_id not in used
                and c.pool_id not in {x.pool_id for x in chosen}
                and _matches(c.descriptors, target, tol, scale)
            ]
            if candidates:
                # Deterministic: sort, then sample with a seeded RNG.
                candidates.sort(key=lambda c: c.pool_id)
                needed = n_per_active - len(chosen)
                if len(candidates) <= needed:
                    chosen.extend(candidates)
                else:
                    chosen.extend(rng.sample(candidates, needed))
            if len(chosen) < n_per_active:
                relaxations += 1
                scale *= 1.5

        used.update(c.pool_id for c in chosen)
        decoy_set = DecoySet(
            active_id=active_id,
            active_smiles=Chem.MolToSmiles(mol),
            decoys=chosen,
            requested=n_per_active,
            relaxations=relaxations,
        )
        if decoy_set.shortfall:
            log.warning(
                "Could not find enough property-matched decoys",
                extra={
                    "active_id": active_id,
                    "selected": len(chosen),
                    "requested": n_per_active,
                    "relaxations": relaxations,
                },
            )
        sets.append(decoy_set)

    return sets


def generate_null_set(pool: list[PoolMolecule], n: int = 100, seed: int = 99) -> list[PoolMolecule]:
    """Draw a random null set from the pool.

    No property constraints: the null distribution should describe what the
    receptor scores for an arbitrary ligand, which is the correct reference for
    asking whether a screening hit's score is unusual.
    """
    if not pool:
        raise DecoyGenerationError("Cannot draw a null set from an empty pool")
    if n < 5:
        raise ValueError(
            f"A null set of {n} is too small to estimate a distribution; use >= 5 "
            "(and >= 100 for a run you intend to publish)."
        )
    ordered = sorted(pool, key=lambda m: m.pool_id)
    if n >= len(ordered):
        log.warning(
            "Null set requested is at least the pool size; using the whole pool",
            extra={"requested": n, "pool_size": len(ordered)},
        )
        return ordered
    return random.Random(seed).sample(ordered, n)


def pool_property_summary(pool: list[PoolMolecule]) -> dict[str, Any]:
    """Descriptive statistics of the pool, for the validation report."""
    summary: dict[str, Any] = {"n_pool": len(pool), **describe_library()}
    for prop in MATCH_PROPERTIES:
        values = np.asarray([m.descriptors[prop] for m in pool], dtype=np.float64)
        summary[f"{prop}_min"] = round(float(values.min()), 3)
        summary[f"{prop}_median"] = round(float(np.median(values)), 3)
        summary[f"{prop}_max"] = round(float(values.max()), 3)
    return summary
