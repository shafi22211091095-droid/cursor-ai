"""Structural alerts: PAINS, Brenk, NIH and ZINC catalogues.

Backed by RDKit's :mod:`FilterCatalog`, which ships the published SMARTS sets.
The catalogue is built once and reused, which is both faster and a guarantee
that every compound is screened against an identical rule set.

Interpretation note carried into the report: a PAINS match is a flag for assay
interference liability, not a verdict on the compound. Several privileged
natural-product scaffolds (catechols, quinones, curcuminoids) match PAINS
filters and are nonetheless genuine, well-characterised bioactives. The report
therefore lists matched filter names rather than collapsing them to a single
"PAINS: yes".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Final

from rdkit import Chem
from rdkit.Chem import FilterCatalog

from ..logging_utils import get_logger

__all__ = ["AVAILABLE_CATALOGUES", "AlertHit", "AlertScreen", "screen_alerts"]

log = get_logger("chem.alerts")

#: Config name -> RDKit catalogue enum member name.
AVAILABLE_CATALOGUES: Final[dict[str, str]] = {
    "pains": "PAINS",
    "brenk": "BRENK",
    "nih": "NIH",
    "zinc": "ZINC",
}


@dataclass(slots=True, frozen=True)
class AlertHit:
    """One matched structural alert."""

    catalogue: str
    filter_name: str
    description: str = ""


@dataclass(slots=True)
class AlertScreen:
    """Alert screening outcome for one compound."""

    hits: list[AlertHit] = field(default_factory=list)
    catalogues_applied: list[str] = field(default_factory=list)

    @property
    def n_hits(self) -> int:
        return len(self.hits)

    @property
    def clean(self) -> bool:
        """True when no alert in any applied catalogue matched."""
        return not self.hits

    def hits_for(self, catalogue: str) -> list[AlertHit]:
        return [h for h in self.hits if h.catalogue == catalogue]

    def names_for(self, catalogue: str) -> str:
        """Semicolon-joined filter names for one catalogue, for a table cell."""
        return "; ".join(sorted(h.filter_name for h in self.hits_for(catalogue)))

    def summary(self) -> dict[str, object]:
        """Flatten to table columns: one boolean and one name list per catalogue."""
        out: dict[str, object] = {"n_structural_alerts": self.n_hits}
        for catalogue in self.catalogues_applied:
            matched = self.hits_for(catalogue)
            out[f"{catalogue}_flagged"] = bool(matched)
            out[f"{catalogue}_filters"] = self.names_for(catalogue)
        return out


@lru_cache(maxsize=16)
def _build_catalogue(catalogues: tuple[str, ...]) -> FilterCatalog.FilterCatalog:
    """Build (and memoise) a combined FilterCatalog for the requested sets."""
    params = FilterCatalog.FilterCatalogParams()
    for name in catalogues:
        enum_name = AVAILABLE_CATALOGUES.get(name)
        if enum_name is None:
            raise KeyError(
                f"Unknown structural alert catalogue {name!r}. "
                f"Available: {sorted(AVAILABLE_CATALOGUES)}"
            )
        member = getattr(FilterCatalog.FilterCatalogParams.FilterCatalogs, enum_name)
        params.AddCatalog(member)
    catalogue = FilterCatalog.FilterCatalog(params)
    log.debug(
        "Built structural alert catalogue",
        extra={"catalogues": list(catalogues), "n_entries": catalogue.GetNumEntries()},
    )
    return catalogue


def screen_alerts(mol: Chem.Mol, catalogues: list[str] | None = None) -> AlertScreen:
    """Screen one molecule against the requested alert catalogues.

    Args:
        mol: A sanitised molecule.
        catalogues: Config names, e.g. ``["pains", "brenk"]``. ``None`` applies
            PAINS and Brenk.

    Raises:
        ValueError: if ``mol`` is None.
        KeyError: if an unknown catalogue is requested.
    """
    if mol is None:
        raise ValueError("screen_alerts received None")
    requested = tuple(catalogues if catalogues is not None else ["pains", "brenk"])
    if not requested:
        return AlertScreen(hits=[], catalogues_applied=[])

    catalogue = _build_catalogue(requested)
    matches = catalogue.GetMatches(mol)

    hits: list[AlertHit] = []
    for match in matches:
        props = set(match.GetPropList())
        filter_set = match.GetProp("FilterSet") if "FilterSet" in props else ""
        reference = match.GetProp("Reference") if "Reference" in props else ""
        description = match.GetDescription()
        hits.append(
            AlertHit(
                catalogue=_attribute_catalogue(filter_set, description, requested),
                filter_name=description,
                description=reference,
            )
        )

    return AlertScreen(hits=hits, catalogues_applied=list(requested))


def _attribute_catalogue(filter_set: str, description: str, requested: tuple[str, ...]) -> str:
    """Attribute a match to one of the requested catalogues.

    RDKit reports PAINS sub-catalogues as ``PAINS_A``/``PAINS_B``/``PAINS_C``,
    and not every entry carries a ``FilterSet`` property, so we fall back to the
    description text before giving up and attributing to the single requested
    catalogue (unambiguous when only one was asked for).
    """
    haystack = f"{filter_set} {description}".lower()
    for name in requested:
        if name in haystack:
            return name
    if len(requested) == 1:
        return requested[0]
    return "unattributed"
