"""A bundled, offline building-block library for decoys and null sets.

Why this exists
---------------
Property-matched decoys in the DUD-E sense are drawn from a large
physicochemically diverse database (ZINC, millions of compounds), selecting
molecules that match each active's properties while being topologically
dissimilar to it. That requires a database npcadd cannot assume is available:
the pipeline is specified to run offline and deterministically.

So npcadd generates its decoy and null pools by **deterministic combinatorial
enumeration** over the cores and substituents below. Each is a common, real
medicinal-chemistry fragment; the enumeration is exhaustive and sorted, so the
pool is identical on every machine with no random seed involved in its
construction.

What this is and is not
-----------------------
This is an honest, reproducible, offline decoy pool. It is **weaker than
DUD-E** in two specific ways, both recorded in LIMITATIONS.md:

1. The pool's chemotype coverage is bounded by these fragments, so it is less
   diverse than a database-derived pool. Enrichment computed against it is
   therefore an optimistic-to-middling estimate rather than a hard benchmark.
2. The pool is synthetic combinatorial chemistry, not catalogue compounds, so
   it contains no information about what is actually purchasable or what has
   actually been assayed and found inactive.

Decoys are *presumed* inactive because they are property-matched and
topologically dissimilar to the actives. They are not experimentally confirmed
inactives. This is the standard assumption in decoy-based validation and it is
stated wherever the resulting metrics are reported.
"""

from __future__ import annotations

from typing import Final

__all__ = ["CORES", "SUBSTITUENTS", "describe_library", "enumerate_triples"]

#: Mono- and bi-substituted cores. ``{a}`` and ``{b}`` are substitution points
#: filled by :data:`SUBSTITUENTS`. All are common scaffolds or linkers.
CORES: Final[tuple[str, ...]] = (
    # Benzenoid
    "c1ccc({a})cc1",
    "c1cc({a})ccc1{b}",
    "c1cc({a})cc({b})c1",
    # Pyridine / diazines
    "c1ccnc({a})c1",
    "c1cc({a})ncc1{b}",
    "c1cnc({a})nc1{b}",
    "c1cc({a})nc({b})n1",
    # Five-membered heteroaromatics
    "c1cc({a})[nH]c1{b}",
    "c1cc({a})oc1{b}",
    "c1cc({a})sc1{b}",
    "c1nnc({a})o1",
    "c1nc({a})n[nH]1",
    "c1nc({a})cs1",
    # Fused bicyclics
    "c1ccc2[nH]c({a})cc2c1",
    "c1ccc2nc({a})ccc2c1",
    "c1ccc2oc({a})cc2c1",
    "c1ccc2sc({a})nc2c1",
    # Saturated rings and linkers
    "C1CCN({a})CC1",
    "C1CN({a})CCN1{b}",
    "C1CCC({a})CC1",
    "C1COC({a})CC1",
    "O=C(N{a}){b}",
    "O=C(O{a}){b}",
    "{a}NC(=O)N{b}",
    "{a}S(=O)(=O)N{b}",
    "{a}OCC(O){b}",
    "{a}CCN(C){b}",
    "{a}C=CC(=O){b}",
)

#: Substituents, each a complete fragment usable at a substitution point.
SUBSTITUENTS: Final[tuple[str, ...]] = (
    "C",
    "CC",
    "CCC",
    "C(C)C",
    "CCCC",
    "C(C)(C)C",
    "CC(C)C",
    "CCO",
    "CCCO",
    "CO",
    "CC(O)C",
    "CN",
    "CCN",
    "CCNC",
    "CC(=O)O",
    "CCC(=O)O",
    "CC(=O)N",
    "CC(=O)OC",
    "CS",
    "CSC",
    "CF",
    "C(F)(F)F",
    "CCl",
    "CCOC",
    "CCOCC",
    "Cc1ccccc1",
    "c1ccccc1",
    "c1ccncc1",
    "c1cc[nH]c1",
    "c1ccoc1",
    "c1ccsc1",
    "C1CCCC1",
    "C1CCCCC1",
    "C1CCNCC1",
    "C1CCOC1",
    "CC#N",
    "CC(N)=O",
    "CCS(=O)(=O)C",
    "COc1ccccc1",
    "Cc1ccc(O)cc1",
    # Larger fragments, so the pool spans the mass range of real actives
    # (300-500 Da) rather than only small-fragment space. Without these the
    # pool cannot property-match a drug-sized active at all.
    "c1ccc(-c2ccccc2)cc1",
    "c1ccc2ccccc2c1",
    "CN1CCN(Cc2ccccc2)CC1",
    "O=C(Nc1ccccc1)c1ccccc1",
    "O=S(=O)(N)c1ccccc1",
    "Cc1ccc(S(=O)(=O)N2CCCC2)cc1",
    "O=C(N1CCOCC1)c1ccccc1",
    "Nc1nccc(-c2ccccc2)n1",
    "CC(=O)Nc1ccc(O)cc1",
    "COc1ccc(CCN)cc1",
    "c1ccc(COc2ccccc2)cc1",
    "O=C(O)c1ccc(-c2ccccc2)cc1",
)


def _bit_reversal_order(n: int) -> list[int]:
    """A van der Corput (bit-reversal) permutation of ``range(n)``.

    Any *prefix* of this ordering is a low-discrepancy, near-uniform sample of
    the whole index range. That is the property needed here: the molecule pool
    is truncated at a size cap, and a truncated pool must still span the
    library's full property range rather than a corner of it.

    Deterministic and seed-free: the permutation depends only on ``n``.
    """
    if n <= 0:
        return []
    bits = max(1, (n - 1).bit_length())
    return sorted(range(n), key=lambda i: (int(f"{i:0{bits}b}"[::-1], 2), i))


def enumerate_triples() -> list[tuple[str, str, str]]:
    """All (core, substituent_a, substituent_b) triples, in round-robin order.

    Two orderings are composed, both for the same reason: the pool is truncated
    at a size cap, and a truncated pool must still span the library.

    1. **Round-robin across cores**, not core-major. Core-major order
       front-loads the pool with every product of the first core. Measured:
       core-major truncation at 2500 molecules gave a pool whose maximum MW was
       290 Da, unable to property-match a 494 Da active at all.
    2. **Bit-reversal ordering of each core's substituent combinations.**
       Iterating substituents in declaration order keeps the first substituent
       fixed for its whole inner loop, so large-times-large pairs are never
       reached before the cap and the pool's upper mass range stays empty. A van
       der Corput prefix samples the combination space uniformly instead.

    The order depends only on the declaration order of CORES and SUBSTITUENTS,
    so it is identical on every machine and involves no seed.
    """
    per_core: list[list[tuple[str, str, str]]] = []
    for core in CORES:
        combos: list[tuple[str, str, str]] = []
        if "{b}" in core:
            combos = [(core, sub_a, sub_b) for sub_a in SUBSTITUENTS for sub_b in SUBSTITUENTS]
        else:
            combos = [(core, sub_a, "") for sub_a in SUBSTITUENTS]
        per_core.append([combos[i] for i in _bit_reversal_order(len(combos))])

    ordered: list[tuple[str, str, str]] = []
    for round_index in range(max(len(c) for c in per_core)):
        for combos in per_core:
            if round_index < len(combos):
                ordered.append(combos[round_index])
    return ordered


def describe_library() -> dict[str, int | str]:
    """Summary of the library, recorded in the validation report."""
    n_mono = sum(1 for core in CORES if "{b}" not in core)
    n_bi = len(CORES) - n_mono
    n_sub = len(SUBSTITUENTS)
    return {
        "n_cores": len(CORES),
        "n_mono_substituted_cores": n_mono,
        "n_bi_substituted_cores": n_bi,
        "n_substituents": n_sub,
        "theoretical_pool_size": n_mono * n_sub + n_bi * n_sub * n_sub,
        "construction": (
            "round-robin combinatorial enumeration of bundled medicinal-chemistry "
            "fragments across all cores; no randomness in pool construction"
        ),
    }
