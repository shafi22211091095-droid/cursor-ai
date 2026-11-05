"""Bemis-Murcko scaffolds and Butina clustering for shortlist chemical diversity.

A ranked shortlist that is all one chemotype is a weaker result than the same
number of hits spread across scaffolds: the latter is more likely to contain a
genuinely separate lead and less likely to reflect one overrepresented
substructure. This module summarises that diversity. It is analysis, not a
filter; it changes no ranking.

Bemis, G. W.; Murcko, M. A. "The Properties of Known Drugs. 1. Molecular
Frameworks." *J. Med. Chem.* 1996, 39, 2887-2893. Butina, D. "Unsupervised Data
Base Clustering..." *J. Chem. Inf. Comput. Sci.* 1999, 39, 747-750.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from rdkit import Chem, DataStructs
from rdkit.Chem import rdFingerprintGenerator
from rdkit.Chem.Scaffolds import MurckoScaffold
from rdkit.ML.Cluster import Butina

from ..logging_utils import get_logger

__all__ = ["ScaffoldAnalysis", "analyse_scaffolds", "butina_clusters", "murcko_scaffold"]

log = get_logger("chem.scaffolds")

_ACYCLIC = "(acyclic / no ring scaffold)"


def murcko_scaffold(smiles: str) -> str | None:
    """Canonical Bemis-Murcko scaffold SMILES, or ``None`` if unparseable.

    An acyclic molecule has an empty Murcko scaffold; RDKit returns that as an
    empty string, which is mapped to ``None`` here so the caller buckets it
    explicitly rather than silently.
    """
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None
    try:
        scaffold = MurckoScaffold.GetScaffoldForMol(mol)
    except (ValueError, RuntimeError):  # pragma: no cover - defensive
        return None
    return Chem.MolToSmiles(scaffold) or None


def butina_clusters(smiles: Sequence[str], cutoff: float = 0.35) -> list[list[int]]:
    """Butina clusters of the molecules, as lists of indices into ``smiles``.

    Morgan radius-2 (2048-bit) fingerprints, Tanimoto distance, ``cutoff`` as the
    distance threshold (so 0.35 groups molecules more similar than Tanimoto
    0.65). Unparseable SMILES are dropped; the returned indices always refer to
    positions in the original input.
    """
    generator = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=2048)
    fingerprints = []
    original_index: list[int] = []
    for i, smi in enumerate(smiles):
        mol = Chem.MolFromSmiles(smi)
        if mol is None:
            continue
        fingerprints.append(generator.GetFingerprint(mol))
        original_index.append(i)

    n = len(fingerprints)
    if n == 0:
        return []
    distances: list[float] = []
    for i in range(1, n):
        sims = DataStructs.BulkTanimotoSimilarity(fingerprints[i], fingerprints[:i])
        distances.extend(1.0 - s for s in sims)
    raw_clusters: Any = Butina.ClusterData(distances, n, cutoff, isDistData=True)
    return [[original_index[j] for j in cluster] for cluster in raw_clusters]


@dataclass(slots=True, frozen=True)
class ScaffoldAnalysis:
    """Chemical-diversity summary of a set of molecules."""

    n_compounds: int
    n_parsed: int
    n_unique_scaffolds: int
    n_clusters: int
    cluster_cutoff: float
    singleton_fraction: float
    scaffold_counts: dict[str, int]

    def as_record(self) -> dict[str, Any]:
        return {
            "n_compounds": self.n_compounds,
            "n_parsed": self.n_parsed,
            "n_unique_murcko_scaffolds": self.n_unique_scaffolds,
            "n_butina_clusters": self.n_clusters,
            "butina_distance_cutoff": self.cluster_cutoff,
            "singleton_cluster_fraction": round(self.singleton_fraction, 4),
        }


def analyse_scaffolds(smiles: Sequence[str], cluster_cutoff: float = 0.35) -> ScaffoldAnalysis:
    """Summarise scaffold and cluster diversity of a set of SMILES.

    ``n_unique_scaffolds`` counts distinct Murcko frameworks among the parseable
    molecules, with all acyclic molecules collapsed into one bucket.
    ``singleton_fraction`` is the share of Butina clusters containing a single
    molecule: a high value means a chemically diverse set.
    """
    items = list(smiles)
    counts: dict[str, int] = {}
    n_parsed = 0
    for smi in items:
        if Chem.MolFromSmiles(smi) is None:
            continue
        n_parsed += 1
        scaffold = murcko_scaffold(smi)
        key = scaffold if scaffold else _ACYCLIC
        counts[key] = counts.get(key, 0) + 1

    clusters = butina_clusters(items, cutoff=cluster_cutoff)
    n_singletons = sum(1 for cluster in clusters if len(cluster) == 1)
    return ScaffoldAnalysis(
        n_compounds=len(items),
        n_parsed=n_parsed,
        n_unique_scaffolds=len(counts),
        n_clusters=len(clusters),
        cluster_cutoff=cluster_cutoff,
        singleton_fraction=(n_singletons / len(clusters)) if clusters else 0.0,
        scaffold_counts=counts,
    )
