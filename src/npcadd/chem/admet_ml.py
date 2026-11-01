"""Pluggable ML/QSPR ADMET predictors with an explicit applicability domain.

Why an applicability domain is mandatory here
---------------------------------------------
A QSPR model returns a number for any molecule you hand it, including molecules
utterly unlike anything it was fitted on. Natural-product space — polycyclic
sesquiterpenoids, highly oxygenated lactones — sits at the edge of or outside
the drug-like chemical space most published ADMET models were trained on.
Reporting such a prediction as fact is the single most common way a
natural-product in-silico paper overclaims.

So every predictor in this module must answer two questions, and the report
prints both: *what is the predicted value* and *is this molecule inside the
domain where that value means anything*. Out-of-domain predictions are emitted
with ``in_domain=False`` and are excluded from ranking.

Bundled model
-------------
:class:`ESOLSolubilityPredictor` implements the ESOL model of Delaney (2004),
a published closed-form linear QSPR whose coefficients are given in the paper.
It is bundled because it needs no downloaded weights and no training data, so it
works offline and deterministically. Its applicability domain is enforced as a
descriptor-range box taken from the descriptor distribution of the published
training set.

To add a predictor, implement :class:`MLPredictor` and register it in
:data:`ML_PREDICTORS`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import ClassVar, Protocol, runtime_checkable

import numpy as np
from rdkit import Chem, DataStructs
from rdkit.Chem import rdFingerprintGenerator

from ..logging_utils import get_logger

__all__ = [
    "ML_PREDICTORS",
    "ADVerdict",
    "DescriptorBoxDomain",
    "ESOLSolubilityPredictor",
    "MLPredictor",
    "PredictionSet",
    "PropertyPrediction",
    "TanimotoKNNDomain",
    "get_ml_predictor",
]

log = get_logger("chem.admet_ml")


@dataclass(slots=True, frozen=True)
class ADVerdict:
    """Applicability-domain decision for one molecule."""

    in_domain: bool
    method: str
    score: float
    threshold: float
    reasons: tuple[str, ...] = ()

    def as_record(self) -> dict[str, object]:
        return {
            "ad_in_domain": self.in_domain,
            "ad_method": self.method,
            "ad_score": round(self.score, 4),
            "ad_threshold": self.threshold,
            "ad_reasons": "; ".join(self.reasons),
        }


@dataclass(slots=True, frozen=True)
class PropertyPrediction:
    """One predicted property value, inseparable from its domain verdict."""

    property_name: str
    value: float
    unit: str
    model: str
    model_version: str
    domain: ADVerdict

    @property
    def reportable(self) -> bool:
        """Whether this value may be stated as a prediction rather than a caveat."""
        return self.domain.in_domain

    def as_record(self) -> dict[str, object]:
        record: dict[str, object] = {
            f"{self.property_name}_pred": round(self.value, 4),
            f"{self.property_name}_unit": self.unit,
            f"{self.property_name}_model": f"{self.model}/{self.model_version}",
            f"{self.property_name}_in_domain": self.domain.in_domain,
        }
        if not self.domain.in_domain:
            record[f"{self.property_name}_domain_note"] = "; ".join(self.domain.reasons)
        return record


@dataclass(slots=True)
class PredictionSet:
    """All ML predictions for one compound."""

    predictions: list[PropertyPrediction] = field(default_factory=list)

    @property
    def n_out_of_domain(self) -> int:
        return sum(1 for p in self.predictions if not p.domain.in_domain)

    def as_record(self) -> dict[str, object]:
        out: dict[str, object] = {}
        for prediction in self.predictions:
            out.update(prediction.as_record())
        out["n_ml_predictions"] = len(self.predictions)
        out["n_ml_out_of_domain"] = self.n_out_of_domain
        return out


class DescriptorBoxDomain:
    """Applicability domain as a bounding box over descriptor ranges.

    Usable when the training *structures* are unavailable but their descriptor
    ranges are published. A molecule is in-domain when every monitored
    descriptor lies inside the training range. The score is the fraction of
    descriptors inside the box, so a near-miss is distinguishable from a wild
    extrapolation.
    """

    method = "descriptor_box"

    def __init__(self, ranges: Mapping[str, tuple[float, float]], threshold: float = 1.0) -> None:
        if not ranges:
            raise ValueError("DescriptorBoxDomain requires at least one descriptor range")
        for name, (low, high) in ranges.items():
            if low > high:
                raise ValueError(f"Range for {name!r} is inverted: {low} > {high}")
        self.ranges = dict(ranges)
        self.threshold = float(threshold)

    def assess(
        self,
        mol: Chem.Mol,  # noqa: ARG002 - interface symmetry with TanimotoKNNDomain
        descriptors: Mapping[str, float],
    ) -> ADVerdict:
        inside = 0
        reasons: list[str] = []
        for name, (low, high) in sorted(self.ranges.items()):
            if name not in descriptors:
                reasons.append(f"{name} unavailable")
                continue
            value = descriptors[name]
            if low <= value <= high:
                inside += 1
            else:
                reasons.append(f"{name}={value:.2f} outside training range [{low:g}, {high:g}]")
        score = inside / len(self.ranges)
        return ADVerdict(
            in_domain=score >= self.threshold and not reasons,
            method=self.method,
            score=score,
            threshold=self.threshold,
            reasons=tuple(reasons),
        )


class TanimotoKNNDomain:
    """Applicability domain as max/mean Tanimoto similarity to training structures.

    Requires the training set SMILES. The score is the mean Tanimoto of the
    ``k`` nearest training molecules (Morgan radius 2, 2048 bits).
    """

    method = "knn_tanimoto"

    def __init__(
        self,
        training_smiles: Sequence[str],
        threshold: float = 0.30,
        k: int = 5,
    ) -> None:
        if not training_smiles:
            raise ValueError(
                "TanimotoKNNDomain requires training set SMILES. If the training set "
                "is unavailable, use DescriptorBoxDomain with published descriptor "
                "ranges instead of silently skipping domain assessment."
            )
        if k < 1:
            raise ValueError("k must be >= 1")
        self.threshold = float(threshold)
        self.k = int(k)
        self._generator = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=2048)
        self._fingerprints = []
        for smiles in training_smiles:
            mol = Chem.MolFromSmiles(smiles)
            if mol is None:
                log.warning("Skipping unparseable training SMILES", extra={"smiles": smiles})
                continue
            self._fingerprints.append(self._generator.GetFingerprint(mol))
        if not self._fingerprints:
            raise ValueError("No training SMILES could be parsed")

    def assess(
        self,
        mol: Chem.Mol,
        descriptors: Mapping[str, float],  # noqa: ARG002 - interface symmetry
    ) -> ADVerdict:
        query = self._generator.GetFingerprint(mol)
        sims = np.asarray(
            DataStructs.BulkTanimotoSimilarity(query, self._fingerprints), dtype=np.float64
        )
        k = min(self.k, sims.size)
        score = float(np.sort(sims)[-k:].mean())
        reasons = (
            ()
            if score >= self.threshold
            else (
                f"mean Tanimoto to {k} nearest training molecules = {score:.3f} "
                f"< {self.threshold:g}",
            )
        )
        return ADVerdict(
            in_domain=score >= self.threshold,
            method=self.method,
            score=score,
            threshold=self.threshold,
            reasons=reasons,
        )


@runtime_checkable
class MLPredictor(Protocol):
    """Interface every ML/QSPR ADMET predictor must satisfy."""

    name: str
    version: str
    citation: str

    def properties(self) -> tuple[str, ...]:
        """Names of the properties this model predicts."""
        ...

    def predict(self, mol: Chem.Mol, descriptors: Mapping[str, float]) -> PredictionSet:
        """Predict, attaching an applicability-domain verdict to every value."""
        ...


class ESOLSolubilityPredictor:
    """Aqueous solubility by the ESOL model of Delaney (2004).

    ``log S = 0.16 - 0.63 cLogP - 0.0062 MW + 0.066 RB - 0.74 AP``

    where ``S`` is in mol/L, ``RB`` is the rotatable-bond count and ``AP`` is the
    aromatic proportion (aromatic heavy atoms / heavy atoms).

    Delaney, J. S. "ESOL: Estimating Aqueous Solubility Directly from Molecular
    Structure." *J. Chem. Inf. Comput. Sci.* 2004, 44, 1000-1005.

    Reported accuracy on the author's held-out sets is roughly 0.9-1.0 log units
    RMSE, so a prediction is a one-order-of-magnitude estimate, not a
    measurement. The report states this alongside every value.
    """

    name = "esol_delaney2004"
    version = "1.0"
    citation = "Delaney 2004, J Chem Inf Comput Sci 44:1000-1005"

    #: Descriptor ranges spanned by the published training compounds. Used as
    #: the applicability-domain box; a molecule outside any range is flagged.
    TRAINING_RANGES: ClassVar[dict[str, tuple[float, float]]] = {
        "mw": (16.0, 780.0),
        "clogp": (-5.0, 9.0),
        "rotatable_bonds": (0.0, 20.0),
    }

    def __init__(self, ad_threshold: float = 1.0) -> None:
        self._domain = DescriptorBoxDomain(self.TRAINING_RANGES, threshold=ad_threshold)

    def properties(self) -> tuple[str, ...]:
        return ("logs_esol",)

    @staticmethod
    def aromatic_proportion(mol: Chem.Mol) -> float:
        """Aromatic heavy atoms divided by heavy atoms; 0.0 for an all-aliphatic molecule."""
        heavy = [a for a in mol.GetAtoms() if a.GetAtomicNum() > 1]
        if not heavy:
            raise ValueError("Molecule has no heavy atoms")
        aromatic = sum(1 for a in heavy if a.GetIsAromatic())
        return aromatic / len(heavy)

    def predict(self, mol: Chem.Mol, descriptors: Mapping[str, float]) -> PredictionSet:
        required = {"clogp", "mw", "rotatable_bonds"}
        missing = required - descriptors.keys()
        if missing:
            raise KeyError(f"ESOL requires descriptors {sorted(missing)}")

        ap = self.aromatic_proportion(mol)
        log_s = (
            0.16
            - 0.63 * descriptors["clogp"]
            - 0.0062 * descriptors["mw"]
            + 0.066 * descriptors["rotatable_bonds"]
            - 0.74 * ap
        )
        verdict = self._domain.assess(mol, descriptors)
        return PredictionSet(
            predictions=[
                PropertyPrediction(
                    property_name="logs_esol",
                    value=float(log_s),
                    unit="log10(mol/L)",
                    model=self.name,
                    model_version=self.version,
                    domain=verdict,
                )
            ]
        )


#: Registry of available predictors. Keys match ``admet.ml_predictors[].name``.
ML_PREDICTORS: dict[str, type[ESOLSolubilityPredictor]] = {
    ESOLSolubilityPredictor.name: ESOLSolubilityPredictor,
}


def get_ml_predictor(name: str, ad_threshold: float = 1.0) -> MLPredictor:
    """Instantiate an ML predictor by name.

    Raises:
        KeyError: if the name is unknown. No default substitution.
    """
    try:
        factory = ML_PREDICTORS[name]
    except KeyError as exc:
        raise KeyError(
            f"Unknown ML ADMET predictor {name!r}. Available: {sorted(ML_PREDICTORS)}. "
            "Register new predictors in npcadd.chem.admet_ml.ML_PREDICTORS."
        ) from exc
    return factory(ad_threshold=ad_threshold)
