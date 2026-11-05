"""Natural-product-likeness score (Ertl & Schuffenhauer 2008).

A single number summarising how closely a molecule's fragment composition
resembles natural products rather than synthetic compounds. Positive values are
natural-product-like, negative are synthetic-like; the published scale runs
roughly -5 to +5. It is computed from RDKit's bundled NP_Score model
(``Contrib/NP_Score/publicnp.model.gz``), so it is offline and deterministic.

Why it is here
--------------
This pipeline screens natural products, yet every drug-likeness rule set it
applies (Lipinski, Veber, ...) was fitted on synthetic, orally-dosed drugs
(``LIMITATIONS.md`` 3.2). The NP-likeness score is the complementary
measurement: on an NP-trained scale it says how natural-product-like each
compound is, so a reader can see that a low rule pass-rate reflects NP chemistry
rather than a bad compound. It is a descriptor, not a filter: nothing is ranked
or excluded on it.

Ertl, P.; Schuffenhauer, A. "Natural Product-likeness Score and Its Application
for the Classification of Compound Libraries." *J. Chem. Inf. Model.* 2008, 48,
68-74.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from rdkit import Chem

from ..logging_utils import get_logger

__all__ = ["NPLikeness", "np_likeness", "np_model_available"]

log = get_logger("chem.np_likeness")

CITATION = "Ertl & Schuffenhauer 2008, J Chem Inf Model 48:68-74 (RDKit NP_Score model)"


@lru_cache(maxsize=1)
def _npscorer() -> Any:
    """Import and cache RDKit's Contrib NP_Score helper module."""
    from rdkit.Chem import RDConfig

    np_dir = str(Path(RDConfig.RDContribDir) / "NP_Score")
    if np_dir not in sys.path:
        sys.path.append(np_dir)
    import npscorer

    return npscorer


@lru_cache(maxsize=1)
def _model() -> Any:
    """Load and cache the published NP model (a fragment -> score mapping)."""
    return _npscorer().readNPModel()


def np_model_available() -> bool:
    """Whether the NP_Score model can be loaded in this environment."""
    try:
        _model()
    except (ImportError, OSError, ValueError, RuntimeError):  # pragma: no cover
        return False
    return True


def _verdict(score: float) -> str:
    if score >= 1.0:
        return "natural-product-like"
    if score <= -1.0:
        return "synthetic-like"
    return "intermediate"


@dataclass(slots=True, frozen=True)
class NPLikeness:
    """One molecule's natural-product-likeness verdict."""

    score: float
    verdict: str
    citation: str = CITATION

    def as_record(self) -> dict[str, object]:
        return {
            "np_likeness_score": round(self.score, 3),
            "np_likeness_class": self.verdict,
            "np_likeness_model": self.citation,
        }


def np_likeness(mol: Chem.Mol) -> NPLikeness | None:
    """Natural-product-likeness of ``mol``, or ``None`` if it cannot be scored.

    Returns ``None`` (with a warning) rather than raising, so a single
    unscorable molecule never aborts an ADMET run.
    """
    if mol is None:
        return None
    try:
        score = float(_npscorer().scoreMol(mol, _model()))
    except (ImportError, OSError, ValueError, RuntimeError) as exc:
        log.warning("NP-likeness scoring unavailable", extra={"error": str(exc)})
        return None
    return NPLikeness(score=score, verdict=_verdict(score))
