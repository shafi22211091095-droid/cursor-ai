"""Pydantic-validated configuration: the single source of truth for a run.

Design rules enforced by this module
------------------------------------
* No parameter used by any stage may be hard-coded in code. If a stage needs a
  number, it reads it from here.
* ``extra="forbid"`` everywhere. A typo in ``config.yaml`` is a loud error, not
  a silently ignored key that makes a reviewer think a setting took effect.
* The config is hashable (:meth:`Config.config_hash`) and the hash is written
  into every output directory alongside a verbatim copy of the resolved config.
* Scoring weights are validated to sum to 1.0 so that a weighted score is
  interpretable on its own scale.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .hashing import hash_obj

__all__ = [
    "AdmetConfig",
    "Config",
    "DockingConfig",
    "GridBox",
    "LigandPrepConfig",
    "PathsConfig",
    "RankingConfig",
    "ReceptorConfig",
    "ReportConfig",
    "ValidationConfig",
    "load_config",
]

Activity = Literal["antioxidant", "thrombolytic", "cytotoxic", "hepatoprotective"]
Direction = Literal["inhibition", "activation"]
RuleSetName = Literal["lipinski", "veber", "egan", "ghose", "muegge", "pfizer_3_75"]
AlertCatalogueName = Literal["pains", "brenk", "nih", "zinc"]
FigureFormat = Literal["pdf", "png", "svg"]

#: Defaults are module-level constants rather than inline lambdas so that they
#: carry the Literal element type and are visible in one place.
DEFAULT_RULE_SETS: list[RuleSetName] = [
    "lipinski",
    "veber",
    "egan",
    "ghose",
    "muegge",
    "pfizer_3_75",
]
DEFAULT_ALERT_CATALOGUES: list[AlertCatalogueName] = ["pains", "brenk"]
DEFAULT_FIGURE_FORMATS: list[FigureFormat] = ["pdf", "png"]

PositiveFloat = Annotated[float, Field(gt=0)]
UnitFloat = Annotated[float, Field(ge=0.0, le=1.0)]


class Strict(BaseModel):
    """Base class: forbid unknown keys, validate on assignment."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True, frozen=False)


class GridBox(Strict):
    """An AutoDock Vina search box, in Angstroms, in receptor coordinates.

    Two modes:

    ``cocrystal``
        Derive the box from the co-crystal ligand's own coordinates: centre at
        its centroid, edges at its extent plus ``padding``, floored at
        ``min_size``. This is the preferred mode. It has no hand-typed numbers,
        it is recomputed from the deposited structure on every run, and it
        cannot drift out of step with the receptor the way a transcribed centre
        can.

    ``explicit``
        A centre and size given directly. Required when the receptor has no
        co-crystal ligand, in which case the provenance of the numbers must be
        recorded in ``notes``.
    """

    mode: Literal["cocrystal", "explicit"] = "cocrystal"
    center: tuple[float, float, float] | None = None
    size: tuple[PositiveFloat, PositiveFloat, PositiveFloat] | None = None
    padding: PositiveFloat = Field(
        default=5.0,
        description="Angstroms added to each side of the co-crystal ligand extent.",
    )
    min_size: tuple[PositiveFloat, PositiveFloat, PositiveFloat] = (20.0, 20.0, 20.0)
    max_edge: PositiveFloat = Field(
        default=40.0,
        description=(
            "Reject boxes with an edge longer than this. A very large box makes the "
            "search effectively blind and the resulting scores non-comparable."
        ),
    )

    @model_validator(mode="after")
    def _mode_consistency(self) -> GridBox:
        if self.mode == "explicit":
            if self.center is None or self.size is None:
                raise ValueError(
                    "grid.mode='explicit' requires both 'center' and 'size'. Use "
                    "mode='cocrystal' to derive the box from the co-crystal ligand "
                    "instead of transcribing coordinates by hand."
                )
            self._check_edges(self.size)
        elif self.center is not None or self.size is not None:
            raise ValueError(
                "grid.mode='cocrystal' derives 'center' and 'size' from the structure; "
                "remove the explicit values or set mode='explicit'."
            )
        self._check_edges(self.min_size)
        return self

    def _check_edges(self, size: tuple[float, float, float]) -> None:
        for axis in size:
            if axis > self.max_edge:
                raise ValueError(
                    f"Grid box edge {axis} A exceeds max_edge {self.max_edge} A. "
                    "If blind docking is intended, raise max_edge explicitly for this "
                    "receptor and record the reason in DECISIONS.md."
                )
            if axis < 10.0:
                raise ValueError(
                    f"Grid box edge {axis} A is below 10 A and will clip most ligands."
                )


class ReceptorConfig(Strict):
    """One docking target, tied to one assayed activity."""

    id: str = Field(description="Short internal key, e.g. 'xo' or 'egfr'.")
    pdb_id: str = Field(min_length=4, max_length=4, description="RCSB PDB ID.")
    activity: Activity
    target_name: str
    uniprot: str | None = None
    chain: str = Field(default="A", min_length=1)
    local_structure: Path | None = Field(
        default=None,
        description=(
            "Path to an already-obtained structure file, used instead of fetching from "
            "RCSB. Set this for offline runs, or to pin a structure that has been "
            "checksummed into the repository."
        ),
    )
    cocrystal_reference_sdf: Path | None = Field(
        default=None,
        description=(
            "Path to the crystallographic pose of the co-crystal ligand, used as the "
            "reference for redocking RMSD. If absent, the pose is extracted from the "
            "structure file by residue name."
        ),
    )
    cocrystal_ligand: str | None = Field(
        default=None,
        description=(
            "Residue name of the co-crystal ligand used for redocking validation. "
            "If null, this receptor cannot be validated by redocking and must be "
            "declared in LIMITATIONS.md."
        ),
    )
    grid: GridBox
    keep_hetatms: list[str] = Field(
        default_factory=list,
        description=(
            "Residue names of structurally essential heteroatoms to retain "
            "(e.g. catalytic metals, FAD, haem). Everything else is stripped."
        ),
    )
    phenotype_direction: Direction = Field(
        description=(
            "Whether the desired phenotype is inhibition or activation of this target. "
            "Docking a competitive-inhibitor-bound conformation while claiming "
            "activation is a directionality error; this field forces the claim to be "
            "explicit so the reporter can flag it."
        )
    )
    structure_is_inhibitor_bound: bool = Field(
        default=True,
        description="True if the deposited structure is a complex with an inhibitor.",
    )
    notes: str = ""

    @field_validator("pdb_id")
    @classmethod
    def _upper_alnum(cls, value: str) -> str:
        v = value.strip().upper()
        if not v.isalnum():
            raise ValueError(f"PDB ID must be alphanumeric, got {value!r}")
        return v

    @property
    def directionality_conflict(self) -> bool:
        """True when an activation phenotype is being read off an inhibitor complex."""
        return self.phenotype_direction == "activation" and self.structure_is_inhibitor_bound


class LigandPrepConfig(Strict):
    """Ligand standardisation, protonation and 3D embedding parameters."""

    input_csv: Path
    smiles_column: str = "smiles"
    name_column: str = "name"
    area_column: str = "area_percent"
    class_column: str = "compound_class"

    ph: float = Field(default=7.4, ge=0.0, le=14.0)
    protonation_method: Literal["dimorphite_dl", "none"] = "dimorphite_dl"
    max_protonation_variants: int = Field(default=1, ge=1, le=8)

    strip_salts: bool = True
    neutralise: bool = True
    canonical_tautomer: bool = True
    max_tautomers: int = Field(default=1000, ge=1)

    embed_seed: int = Field(default=0xC0FFEE, ge=0)
    n_conformers: int = Field(default=10, ge=1, le=500)
    mmff_max_iters: int = Field(default=2000, ge=1)
    energy_window_kcal: PositiveFloat = 10.0
    prune_rms_thresh: PositiveFloat = 0.5

    min_heavy_atoms: int = Field(default=6, ge=1)
    max_heavy_atoms: int = Field(default=100, ge=1)
    allowed_elements: list[str] = Field(
        default_factory=lambda: ["C", "H", "N", "O", "S", "P", "F", "Cl", "Br", "I"]
    )

    @model_validator(mode="after")
    def _atom_range(self) -> LigandPrepConfig:
        if self.min_heavy_atoms > self.max_heavy_atoms:
            raise ValueError("min_heavy_atoms must not exceed max_heavy_atoms")
        return self


class DockingConfig(Strict):
    """AutoDock Vina execution parameters."""

    engine: Literal["vina"] = "vina"
    seed: int = Field(default=20240617, description="Explicit Vina RNG seed.")
    exhaustiveness: int = Field(default=32, ge=1, le=1024)
    n_poses: int = Field(default=9, ge=1, le=100)
    max_evals: int = Field(default=0, ge=0, description="0 = Vina default.")
    workers: int = Field(default=4, ge=1, le=128)
    cpu_per_worker: int = Field(default=1, ge=1)
    scoring: Literal["vina", "vinardo"] = "vina"
    resume: bool = True
    interaction_fingerprints: bool = True
    prolif_interactions: list[str] = Field(
        default_factory=lambda: [
            "Hydrophobic",
            "HBAcceptor",
            "HBDonor",
            "PiStacking",
            "Anionic",
            "Cationic",
            "CationPi",
            "PiCation",
        ]
    )
    placement_check: bool = Field(
        default=True,
        description=(
            "Diagnose each screened ligand's best pose for search-box containment. "
            "Vina's ranking can look healthy while the top pose is pinned against a "
            "wall or protruding from the box - a sign its energy minimum lies outside "
            "the defined pocket and the score is untrustworthy (Agboola et al. 2026; "
            "DECISIONS.md D-021). Report-only: it adds columns and a warning count, and "
            "never changes the ranking."
        ),
    )
    placement_min_fraction_in_box: UnitFloat = Field(
        default=0.90,
        description=(
            "Flag a pose when fewer than this fraction of its heavy atoms lie inside "
            "the search box."
        ),
    )
    placement_max_centroid_offset_frac: PositiveFloat = Field(
        default=0.90,
        description=(
            "Flag a pose when its centroid lies beyond this fraction of the box "
            "half-width on any axis (wall-hugging); 1.0 places the centroid on a face."
        ),
    )
    placement_box_tolerance_a: Annotated[float, Field(ge=0.0)] = Field(
        default=0.0,
        description="Angstroms added to each box half-width when testing atom containment.",
    )
    pose_consensus_top_n: int = Field(
        default=3,
        ge=1,
        le=100,
        description=(
            "How many of a ligand's best returned poses to average into the "
            "mean_top_affinity_kcal_mol consensus column (diagnostic only)."
        ),
    )
    consensus_spread_warn_kcal: PositiveFloat = Field(
        default=3.0,
        description=(
            "Flag (consensus_ok=False) a ligand whose returned-pose affinity spread "
            "exceeds this, meaning the top pose is one of several comparable solutions. "
            "Diagnostic only; never changes the ranking."
        ),
    )
    pose_stability_check: bool = Field(
        default=True,
        description=(
            "Record the alignment-free RMSD between the top two poses "
            "(top2_pose_rmsd_a): small means the search converged on one placement."
        ),
    )


class ValidationConfig(Strict):
    """Mandatory pre-screening validation gates."""

    enabled: bool = True
    redock_rmsd_threshold: PositiveFloat = Field(
        default=2.0,
        description="Halt the pipeline if co-crystal redocking RMSD exceeds this.",
    )
    halt_on_failure: bool = Field(
        default=True,
        description=(
            "Must stay true for publication runs. Setting it false is recorded in "
            "provenance and stamped on every figure caption."
        ),
    )
    decoys_per_active: int = Field(default=50, ge=1, le=1000)
    decoy_seed: int = Field(default=1234, ge=0)
    decoy_property_tolerance: dict[str, float] = Field(
        default_factory=lambda: {
            "mw": 25.0,
            "clogp": 1.0,
            "tpsa": 20.0,
            "rotatable_bonds": 2.0,
            "hbd": 1.0,
            "hba": 2.0,
            "formal_charge": 0.0,
        }
    )
    bedroc_alpha: PositiveFloat = 20.0
    ef_fractions: list[UnitFloat] = Field(default_factory=lambda: [0.01, 0.05, 0.10])
    null_set_size: int = Field(default=100, ge=10, le=10000)
    null_seed: int = Field(default=99, ge=0)
    known_actives_csv: Path | None = None

    @field_validator("ef_fractions")
    @classmethod
    def _nonempty_sorted(cls, value: list[float]) -> list[float]:
        if not value:
            raise ValueError("ef_fractions must not be empty")
        if any(f <= 0 for f in value):
            raise ValueError("ef_fractions must be strictly positive")
        return sorted(set(value))


class MLPredictorConfig(Strict):
    """Configuration for a pluggable ML ADMET predictor."""

    name: str
    enabled: bool = True
    applicability_domain: Literal["knn_tanimoto", "none"] = "knn_tanimoto"
    ad_threshold: UnitFloat = Field(
        default=0.30,
        description=(
            "Minimum max-Tanimoto similarity to the training set for a prediction to "
            "be considered inside the applicability domain."
        ),
    )
    ad_neighbours: int = Field(default=5, ge=1)


class AdmetConfig(Strict):
    """Descriptor computation, rule sets and external-tool handoff."""

    rule_sets: list[RuleSetName] = Field(default_factory=DEFAULT_RULE_SETS.copy)
    structural_alerts: list[AlertCatalogueName] = Field(
        default_factory=DEFAULT_ALERT_CATALOGUES.copy
    )
    lipinski_max_violations: int = Field(default=1, ge=0, le=4)
    ml_predictors: list[MLPredictorConfig] = Field(default_factory=list)

    external_tools: list[str] = Field(
        default_factory=lambda: ["swissadme", "pkcsm", "protox_iii"],
        description=(
            "HTML-form-only tools. npcadd generates paste-ready input batches and "
            "validates pasted-back CSVs against a strict schema. It never scrapes."
        ),
    )
    external_batch_size: int = Field(default=100, ge=1)
    np_likeness: bool = Field(
        default=True,
        description=(
            "Compute the Ertl-Schuffenhauer natural-product-likeness score for each "
            "ligand (offline RDKit NP_Score model). Descriptor only; it never filters "
            "or ranks."
        ),
    )


class RankingConfig(Strict):
    """Multi-criteria ranking. Raw affinity is never the ranking key."""

    weights: dict[str, UnitFloat] = Field(
        default_factory=lambda: {
            "ligand_efficiency": 0.40,
            "admet_pass_rate": 0.35,
            "gcms_area": 0.25,
        }
    )
    reference_temperature_k: PositiveFloat = 298.15
    area_highlight_threshold: float = Field(
        default=2.0,
        ge=0.0,
        description="GC-MS area %% at or above which a compound is sorted to the top.",
    )
    sensitivity_n_samples: int = Field(default=512, ge=16, le=20000)
    sensitivity_seed: int = Field(default=7, ge=0)
    sensitivity_dirichlet_alpha: PositiveFloat = 4.0
    min_ligand_efficiency: float | None = Field(
        default=None, description="Optional LE floor for the shortlist."
    )

    @field_validator("weights")
    @classmethod
    def _weights_sum_to_one(cls, value: dict[str, float]) -> dict[str, float]:
        required = {"ligand_efficiency", "admet_pass_rate", "gcms_area"}
        missing = required - value.keys()
        if missing:
            raise ValueError(f"ranking.weights is missing required keys: {sorted(missing)}")
        unknown = value.keys() - required
        if unknown:
            raise ValueError(f"ranking.weights has unknown keys: {sorted(unknown)}")
        total = sum(value.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"ranking.weights must sum to 1.0 for the score to be interpretable; "
                f"got {total:.6f}"
            )
        return dict(sorted(value.items()))


class ReportConfig(Strict):
    """Figure and workbook output settings."""

    dpi: int = Field(default=300, ge=150)
    formats: list[FigureFormat] = Field(default_factory=DEFAULT_FIGURE_FORMATS.copy)
    palette: Literal["okabe_ito", "viridis"] = "okabe_ito"
    font_family: str = "DejaVu Sans"
    base_font_size: PositiveFloat = 9.0
    figure_width_in: PositiveFloat = 7.0
    caption_includes_versions: bool = True
    xlsx_name: str = "npcadd_results.xlsx"
    top_n_table: int = Field(default=25, ge=1)

    @field_validator("formats")
    @classmethod
    def _at_least_one_vector(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("report.formats must not be empty")
        if not ({"pdf", "svg"} & set(value)):
            raise ValueError(
                "report.formats must include a vector format ('pdf' or 'svg') for "
                "publication figures."
            )
        return value


class PathsConfig(Strict):
    """All filesystem locations. ``data/raw`` is read-only by convention."""

    raw: Path = Path("data/raw")
    sample: Path = Path("data/sample")
    interim: Path = Path("data/interim")
    processed: Path = Path("data/processed")
    receptor_cache: Path = Path("data/raw/pdb_cache")
    runs: Path = Path("runs")
    reports: Path = Path("reports")
    figures: Path = Path("reports/figures")


class Config(Strict):
    """Top-level run configuration."""

    project: str = "npcadd"
    study: str = Field(description="Which manuscript this run supports.")
    description: str = ""
    allow_network: bool = Field(
        default=True,
        description=(
            "If false, any stage that would reach the network raises instead of "
            "attempting a fetch. Offline runs must rely on the receptor cache."
        ),
    )
    paths: PathsConfig = Field(default_factory=PathsConfig)
    ligands: LigandPrepConfig
    receptors: list[ReceptorConfig] = Field(min_length=1)
    docking: DockingConfig = Field(default_factory=DockingConfig)
    validation: ValidationConfig = Field(default_factory=ValidationConfig)
    admet: AdmetConfig = Field(default_factory=AdmetConfig)
    ranking: RankingConfig = Field(default_factory=RankingConfig)
    report: ReportConfig = Field(default_factory=ReportConfig)

    # Populated at load time, excluded from the hash to avoid self-reference.
    source_path: Path | None = Field(default=None, exclude=True)

    @field_validator("receptors")
    @classmethod
    def _unique_ids(cls, value: Sequence[ReceptorConfig]) -> list[ReceptorConfig]:
        ids = [r.id for r in value]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"Duplicate receptor ids: {sorted(dupes)}")
        return list(value)

    @model_validator(mode="after")
    def _activities_are_assayed(self) -> Config:
        """Docking targets are restricted to activities that were actually assayed.

        This encodes the methodological decision recorded in DECISIONS.md: a
        docking panel wider than the assay panel produces claims that no
        experiment in the manuscript can support.
        """
        if not self.receptors:
            raise ValueError("At least one receptor is required")
        return self

    def config_hash(self) -> str:
        """SHA256 over the canonical JSON of the resolved config."""
        return hash_obj(self.model_dump(mode="json"))

    def receptor(self, receptor_id: str) -> ReceptorConfig:
        """Look up a receptor by its internal id."""
        for r in self.receptors:
            if r.id == receptor_id:
                return r
        raise KeyError(
            f"No receptor with id {receptor_id!r}; known ids: {[r.id for r in self.receptors]}"
        )

    @property
    def activities(self) -> list[str]:
        """Sorted unique activities covered by the receptor panel."""
        return sorted({r.activity for r in self.receptors})

    def run_dir(self, run_id: str) -> Path:
        """Directory for a given run's outputs."""
        return self.paths.runs / run_id

    def to_yaml(self) -> str:
        """Serialise the resolved config back to YAML (for copying into outputs)."""
        return yaml.safe_dump(
            self.model_dump(mode="json"), sort_keys=True, default_flow_style=False
        )


def load_config(path: str | Path) -> Config:
    """Load, validate and resolve ``config.yaml``.

    Relative paths inside the config are resolved against the config file's own
    directory, so a config is portable and does not depend on the shell's
    working directory.

    Raises:
        FileNotFoundError: if the config file is absent.
        ValueError: if the YAML is not a mapping or fails validation.
    """
    cfg_path = Path(path).expanduser().resolve()
    if not cfg_path.is_file():
        raise FileNotFoundError(f"Config file not found: {cfg_path}")

    with cfg_path.open("r", encoding="utf-8") as fh:
        raw: Any = yaml.safe_load(fh)
    if raw is None:
        raise ValueError(f"Config file is empty: {cfg_path}")
    if not isinstance(raw, dict):
        # ValueError, not TypeError: the CLI reports every configuration problem
        # through one handler, and a malformed config file is a value problem
        # from the caller's point of view.
        raise ValueError(  # noqa: TRY004
            f"Config root must be a mapping, got {type(raw).__name__}"
        )

    cfg = Config.model_validate(raw)
    base = cfg_path.parent
    _resolve_paths(cfg, base)
    cfg.source_path = cfg_path
    return cfg


def _resolve_paths(cfg: Config, base: Path) -> None:
    """Rewrite relative paths in-place, relative to ``base``."""

    def absolutise(p: Path) -> Path:
        return p if p.is_absolute() else (base / p).resolve()

    paths = cfg.paths
    for field in paths.__class__.model_fields:
        setattr(paths, field, absolutise(getattr(paths, field)))

    cfg.ligands.input_csv = absolutise(cfg.ligands.input_csv)
    if cfg.validation.known_actives_csv is not None:
        cfg.validation.known_actives_csv = absolutise(cfg.validation.known_actives_csv)

    for receptor in cfg.receptors:
        if receptor.local_structure is not None:
            receptor.local_structure = absolutise(receptor.local_structure)
        if receptor.cocrystal_reference_sdf is not None:
            receptor.cocrystal_reference_sdf = absolutise(receptor.cocrystal_reference_sdf)
