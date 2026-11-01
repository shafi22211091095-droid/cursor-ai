"""Drug-likeness rule sets, implemented in code from the primary literature.

Each rule set is a pure function of the descriptor dict produced by
:func:`npcadd.chem.descriptors.compute_descriptors`. No rule set is taken from a
web tool, so every verdict in the output tables is traceable to the inequality
written here and to the citation in its docstring.

A deliberate design choice: each rule returns the *individual criteria* and not
just a pass/fail boolean. Reviewers ask which criterion a compound failed, and
"Lipinski: fail" cannot answer that.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Final

__all__ = [
    "RULE_SETS",
    "RuleResult",
    "apply_all_rules",
    "egan",
    "ghose",
    "lipinski",
    "muegge",
    "pfizer_3_75",
    "veber",
]


@dataclass(slots=True)
class RuleResult:
    """Outcome of one rule set for one compound."""

    name: str
    passed: bool
    n_violations: int
    criteria: dict[str, bool] = field(default_factory=dict)
    citation: str = ""

    @property
    def failed_criteria(self) -> list[str]:
        """Names of the criteria that were not met, sorted for stable output."""
        return sorted(k for k, ok in self.criteria.items() if not ok)


def _criteria_result(
    name: str,
    criteria: Mapping[str, bool],
    citation: str,
    max_violations: int = 0,
) -> RuleResult:
    """Build a :class:`RuleResult`, passing when violations <= ``max_violations``."""
    n_violations = sum(1 for ok in criteria.values() if not ok)
    return RuleResult(
        name=name,
        passed=n_violations <= max_violations,
        n_violations=n_violations,
        criteria=dict(criteria),
        citation=citation,
    )


def lipinski(d: Mapping[str, float], max_violations: int = 1) -> RuleResult:
    """Lipinski's rule of five.

    Lipinski, Lombardo, Dominy & Feeney, *Adv. Drug Deliv. Rev.* 1997, 23, 3-25.
    The original formulation tolerates one violation, which is why
    ``max_violations`` defaults to 1 and is configurable rather than hard-coded.
    """
    return _criteria_result(
        "lipinski",
        {
            "mw_le_500": d["mw"] <= 500.0,
            "clogp_le_5": d["clogp"] <= 5.0,
            "hbd_le_5": d["hbd"] <= 5.0,
            "hba_le_10": d["hba"] <= 10.0,
        },
        "Lipinski et al. 1997, Adv Drug Deliv Rev 23:3-25",
        max_violations=max_violations,
    )


def veber(d: Mapping[str, float]) -> RuleResult:
    """Veber's oral bioavailability criteria.

    Veber, Johnson, Cheng, Smith, Ward & Kopple, *J. Med. Chem.* 2002, 45,
    2615-2623. Both criteria must hold; the paper presents them jointly.
    """
    return _criteria_result(
        "veber",
        {
            "rotatable_bonds_le_10": d["rotatable_bonds"] <= 10.0,
            "tpsa_le_140": d["tpsa"] <= 140.0,
        },
        "Veber et al. 2002, J Med Chem 45:2615-2623",
    )


def egan(d: Mapping[str, float]) -> RuleResult:
    """Egan's absorption "egg".

    Egan, Merz & Baldwin, *J. Med. Chem.* 2000, 43, 3867-3877.
    """
    return _criteria_result(
        "egan",
        {
            "tpsa_le_131_6": d["tpsa"] <= 131.6,
            "clogp_le_5_88": d["clogp"] <= 5.88,
        },
        "Egan et al. 2000, J Med Chem 43:3867-3877",
    )


def ghose(d: Mapping[str, float]) -> RuleResult:
    """Ghose's drug-likeness window.

    Ghose, Viswanadhan & Wendoloski, *J. Comb. Chem.* 1999, 1, 55-68. The
    qualifying ranges are two-sided, which matters for natural products: small
    terpenoids frequently fail the *lower* bounds, and a one-sided check would
    hide that.
    """
    return _criteria_result(
        "ghose",
        {
            "mw_160_480": 160.0 <= d["mw"] <= 480.0,
            "clogp_m0_4_to_5_6": -0.4 <= d["clogp"] <= 5.6,
            "atoms_20_70": 20.0 <= d["n_atoms_total"] <= 70.0,
            "refractivity_40_130": 40.0 <= d["molar_refractivity"] <= 130.0,
        },
        "Ghose et al. 1999, J Comb Chem 1:55-68",
    )


def muegge(d: Mapping[str, float]) -> RuleResult:
    """Muegge's pharmacophore-point drug-likeness filter.

    Muegge, Heald & Brittelli, *J. Med. Chem.* 2001, 44, 1841-1846.
    """
    return _criteria_result(
        "muegge",
        {
            "mw_200_600": 200.0 <= d["mw"] <= 600.0,
            "clogp_m2_to_5": -2.0 <= d["clogp"] <= 5.0,
            "tpsa_le_150": d["tpsa"] <= 150.0,
            "rings_le_7": d["rings"] <= 7.0,
            "carbons_gt_4": d["heavy_atoms"] - d["hba"] > 4.0,
            "heteroatoms_gt_1": d["hba"] + d["hbd"] > 1.0,
            "rotatable_bonds_le_15": d["rotatable_bonds"] <= 15.0,
            "hba_le_10": d["hba"] <= 10.0,
            "hbd_le_5": d["hbd"] <= 5.0,
        },
        "Muegge et al. 2001, J Med Chem 44:1841-1846",
    )


def pfizer_3_75(d: Mapping[str, float]) -> RuleResult:
    """Pfizer 3/75 in-vivo toxicity heuristic.

    Price, Blagg, Jones, Greene & Wager, *Expert Opin. Drug Metab. Toxicol.*
    2009, 5, 921-931; Hughes et al., *Bioorg. Med. Chem. Lett.* 2008, 18,
    4872-4875.

    A compound is flagged as higher-risk when cLogP > 3 **and** TPSA < 75; the
    rule is a conjunction, so "passing" means not being in that quadrant.
    """
    in_risk_quadrant = d["clogp"] > 3.0 and d["tpsa"] < 75.0
    return _criteria_result(
        "pfizer_3_75",
        {"outside_high_risk_quadrant": not in_risk_quadrant},
        "Hughes et al. 2008, Bioorg Med Chem Lett 18:4872-4875; Price et al. 2009",
    )


#: Dispatch table. Keys match the ``admet.rule_sets`` config values.
RULE_SETS: Final[dict[str, Callable[[Mapping[str, float]], RuleResult]]] = {
    "lipinski": lipinski,
    "veber": veber,
    "egan": egan,
    "ghose": ghose,
    "muegge": muegge,
    "pfizer_3_75": pfizer_3_75,
}


def apply_all_rules(
    descriptors: Mapping[str, float],
    rule_names: list[str] | None = None,
    lipinski_max_violations: int = 1,
) -> dict[str, RuleResult]:
    """Apply the requested rule sets to one descriptor dict.

    Args:
        descriptors: Output of :func:`compute_descriptors`.
        rule_names: Which rule sets to apply. ``None`` means all of them.
        lipinski_max_violations: Tolerated Lipinski violations.

    Raises:
        KeyError: if an unknown rule set is requested. Unknown rule names are
            never silently skipped, because a missing column in the output table
            would read as "this compound was not assessed" rather than
            "the configuration was wrong".
    """
    names = rule_names if rule_names is not None else sorted(RULE_SETS)
    unknown = [n for n in names if n not in RULE_SETS]
    if unknown:
        raise KeyError(f"Unknown rule set(s): {unknown}. Available: {sorted(RULE_SETS)}")

    out: dict[str, RuleResult] = {}
    for name in names:
        out[name] = (
            lipinski(descriptors, max_violations=lipinski_max_violations)
            if name == "lipinski"
            else RULE_SETS[name](descriptors)
        )
    return out


def pass_rate(results: Mapping[str, RuleResult]) -> float:
    """Fraction of applied rule sets that the compound passed.

    This is the ``admet_pass_rate`` term in the multi-criteria score. It is a
    blunt instrument by construction — the rule sets are correlated and not
    independent evidence — which is why it is only one weighted term and why
    the sensitivity analysis over weights exists.
    """
    if not results:
        raise ValueError("pass_rate requires at least one rule result")
    return sum(1 for r in results.values() if r.passed) / len(results)
