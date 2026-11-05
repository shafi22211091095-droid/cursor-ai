"""Stage 5: ADMET — offline, deterministic, and explicit about what it is not.

Everything in this stage is computed locally from structure: RDKit descriptors,
six drug-likeness rule sets implemented from the primary literature, PAINS and
Brenk structural alerts, and any configured QSPR model with its applicability
domain attached.

What this stage deliberately does **not** do
--------------------------------------------
It does not scrape SwissADME, pkCSM or ProTox-III. Those tools are HTML forms
without public APIs; scraping them is fragile, against their terms, and —
most importantly for a manuscript — unversioned, so a scraped number cannot be
attributed to a specific version of a specific model on a specific date.

Instead :func:`write_external_tool_batches` emits a paste-ready input file per
tool together with a strict CSV paste-back schema. The pasted-back values are
validated on read, and the tool name, version and retrieval date are recorded
in provenance. The handoff is manual by design; what it buys is attributable
numbers.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
from rdkit import Chem

from ..chem.admet_ml import get_ml_predictor
from ..chem.alerts import screen_alerts
from ..chem.descriptors import DESCRIPTOR_FIELDS, compute_descriptors
from ..chem.np_likeness import np_likeness
from ..chem.rules import apply_all_rules, pass_rate
from ..config import Config
from ..logging_utils import get_logger
from .ligands import LigandRecord

__all__ = [
    "EXTERNAL_TOOL_SCHEMAS",
    "AdmetRow",
    "AdmetSummary",
    "read_external_tool_csv",
    "run_admet",
    "write_external_tool_batches",
]

log = get_logger("stages.admet")


#: Paste-back schema per HTML-form-only tool: column -> (dtype, description).
#: A pasted CSV is validated against this before any value is used.
EXTERNAL_TOOL_SCHEMAS: dict[str, dict[str, tuple[str, str]]] = {
    "swissadme": {
        "ligand_id": ("str", "npcadd ligand_id, copied unchanged from the input batch"),
        "gi_absorption": ("category:High,Low", "Predicted gastrointestinal absorption"),
        "bbb_permeant": ("bool", "Blood-brain barrier permeant (Yes/No)"),
        "pgp_substrate": ("bool", "P-glycoprotein substrate (Yes/No)"),
        "cyp1a2_inhibitor": ("bool", "CYP1A2 inhibitor (Yes/No)"),
        "cyp2c19_inhibitor": ("bool", "CYP2C19 inhibitor (Yes/No)"),
        "cyp2c9_inhibitor": ("bool", "CYP2C9 inhibitor (Yes/No)"),
        "cyp2d6_inhibitor": ("bool", "CYP2D6 inhibitor (Yes/No)"),
        "cyp3a4_inhibitor": ("bool", "CYP3A4 inhibitor (Yes/No)"),
        "log_kp_cm_s": ("float", "Skin permeation log Kp (cm/s)"),
        "bioavailability_score": ("float", "Abbott bioavailability score"),
    },
    "pkcsm": {
        "ligand_id": ("str", "npcadd ligand_id, copied unchanged from the input batch"),
        "caco2_permeability": ("float", "log Papp in 10^-6 cm/s"),
        "intestinal_absorption_pct": ("float", "Human intestinal absorption (%)"),
        "vdss_log_l_kg": ("float", "log VDss (L/kg)"),
        "bbb_log_bb": ("float", "log BB"),
        "cns_log_ps": ("float", "log PS"),
        "total_clearance": ("float", "log mL/min/kg"),
        "ames_toxicity": ("bool", "AMES mutagenicity (Yes/No)"),
        "hepatotoxicity": ("bool", "Predicted hepatotoxicity (Yes/No)"),
        "oral_rat_ld50_mol_kg": ("float", "Oral rat acute toxicity LD50 (mol/kg)"),
    },
    "protox_iii": {
        "ligand_id": ("str", "npcadd ligand_id, copied unchanged from the input batch"),
        "predicted_ld50_mg_kg": ("float", "Predicted LD50 (mg/kg)"),
        "toxicity_class": ("int", "GHS toxicity class, 1 (most toxic) to 6"),
        "hepatotoxicity_active": ("bool", "Hepatotoxicity endpoint active (Yes/No)"),
        "carcinogenicity_active": ("bool", "Carcinogenicity endpoint active (Yes/No)"),
        "mutagenicity_active": ("bool", "Mutagenicity endpoint active (Yes/No)"),
        "cytotoxicity_active": ("bool", "Cytotoxicity endpoint active (Yes/No)"),
        "prediction_accuracy_pct": ("float", "Model confidence reported by the tool"),
    },
}

_TRUE = frozenset({"yes", "y", "true", "1", "active", "permeant"})
_FALSE = frozenset({"no", "n", "false", "0", "inactive", "non-permeant"})


@dataclass(slots=True)
class AdmetRow:
    """Full ADMET profile for one ligand."""

    ligand_id: str
    name: str
    compound_class: str
    area_percent: float
    smiles: str
    descriptors: dict[str, float] = field(default_factory=dict)
    rule_results: dict[str, bool] = field(default_factory=dict)
    rule_violations: dict[str, int] = field(default_factory=dict)
    failed_criteria: dict[str, str] = field(default_factory=dict)
    admet_pass_rate: float = 0.0
    alerts: dict[str, Any] = field(default_factory=dict)
    ml_predictions: dict[str, Any] = field(default_factory=dict)
    np_likeness: dict[str, Any] = field(default_factory=dict)

    def as_record(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "ligand_id": self.ligand_id,
            "name": self.name,
            "compound_class": self.compound_class,
            "area_percent": self.area_percent,
            "smiles": self.smiles,
        }
        record.update({k: round(v, 4) for k, v in self.descriptors.items()})
        for rule, passed in sorted(self.rule_results.items()):
            record[f"{rule}_pass"] = passed
            record[f"{rule}_violations"] = self.rule_violations.get(rule, 0)
            record[f"{rule}_failed_criteria"] = self.failed_criteria.get(rule, "")
        record["admet_pass_rate"] = round(self.admet_pass_rate, 4)
        record.update(self.alerts)
        record.update(self.ml_predictions)
        record.update(self.np_likeness)
        return record


@dataclass(slots=True)
class AdmetSummary:
    """Aggregate ADMET outcome."""

    n_ligands: int
    n_lipinski_pass: int
    n_all_rules_pass: int
    n_pains_flagged: int
    n_brenk_flagged: int
    n_ml_out_of_domain: int
    mean_pass_rate: float
    results_path: str = ""
    external_batch_paths: list[str] = field(default_factory=list)

    def as_metrics(self) -> dict[str, Any]:
        return {
            "n_ligands": self.n_ligands,
            "n_lipinski_pass": self.n_lipinski_pass,
            "n_all_rules_pass": self.n_all_rules_pass,
            "n_pains_flagged": self.n_pains_flagged,
            "n_brenk_flagged": self.n_brenk_flagged,
            "n_ml_out_of_domain": self.n_ml_out_of_domain,
            "mean_admet_pass_rate": round(self.mean_pass_rate, 4),
        }


def run_admet(
    cfg: Config, ligands: list[LigandRecord], out_dir: Path
) -> tuple[list[AdmetRow], AdmetSummary]:
    """Compute the full offline ADMET profile for every accepted ligand."""
    predictors = [
        (spec, get_ml_predictor(spec.name, ad_threshold=spec.ad_threshold))
        for spec in cfg.admet.ml_predictors
        if spec.enabled
    ]

    rows: list[AdmetRow] = []
    for ligand in ligands:
        # The docked form is profiled, not the input SMILES: that is the species
        # whose affinity is reported, so its descriptors are the relevant ones.
        mol = Chem.MolFromSmiles(ligand.docked_smiles or ligand.standard_smiles)
        if mol is None:
            log.warning(
                "Could not re-parse a prepared ligand for ADMET; skipping",
                extra={"ligand": ligand.ligand_id},
            )
            continue

        descriptors = compute_descriptors(mol)
        rules = apply_all_rules(
            descriptors,
            rule_names=list(cfg.admet.rule_sets),
            lipinski_max_violations=cfg.admet.lipinski_max_violations,
        )
        alerts = screen_alerts(mol, list(cfg.admet.structural_alerts))

        ml: dict[str, Any] = {}
        for spec, predictor in predictors:
            predictions = predictor.predict(mol, descriptors)
            ml.update(predictions.as_record())
            ml[f"{spec.name}_citation"] = predictor.citation

        np_record: dict[str, Any] = {}
        if cfg.admet.np_likeness:
            npl = np_likeness(mol)
            if npl is not None:
                np_record = npl.as_record()

        rows.append(
            AdmetRow(
                ligand_id=ligand.ligand_id,
                name=ligand.name,
                compound_class=ligand.compound_class,
                area_percent=ligand.area_percent,
                smiles=ligand.docked_smiles or ligand.standard_smiles,
                descriptors=descriptors,
                rule_results={k: v.passed for k, v in rules.items()},
                rule_violations={k: v.n_violations for k, v in rules.items()},
                failed_criteria={k: "; ".join(v.failed_criteria) for k, v in rules.items()},
                admet_pass_rate=pass_rate(rules),
                alerts=alerts.summary(),
                ml_predictions=ml,
                np_likeness=np_record,
            )
        )

    if not rows:
        raise RuntimeError("ADMET produced no rows; no ligand could be profiled")

    results_path = out_dir / "admet_results.csv"
    pd.DataFrame([r.as_record() for r in rows]).to_csv(
        results_path, index=False, lineterminator="\n"
    )

    batch_paths = write_external_tool_batches(cfg, rows, out_dir)

    summary = AdmetSummary(
        n_ligands=len(rows),
        n_lipinski_pass=sum(1 for r in rows if r.rule_results.get("lipinski")),
        n_all_rules_pass=sum(1 for r in rows if all(r.rule_results.values())),
        n_pains_flagged=sum(1 for r in rows if r.alerts.get("pains_flagged")),
        n_brenk_flagged=sum(1 for r in rows if r.alerts.get("brenk_flagged")),
        n_ml_out_of_domain=sum(
            int(r.ml_predictions.get("n_ml_out_of_domain", 0) or 0) for r in rows
        ),
        mean_pass_rate=(sum(r.admet_pass_rate for r in rows) / len(rows) if rows else 0.0),
        results_path=results_path.as_posix(),
        external_batch_paths=batch_paths,
    )
    log.info("ADMET complete", extra=summary.as_metrics())
    return rows, summary


def write_external_tool_batches(cfg: Config, rows: list[AdmetRow], out_dir: Path) -> list[str]:
    """Write paste-ready inputs and paste-back schemas for HTML-form-only tools.

    For each configured tool, three files are produced in
    ``<run>/external_tools/<tool>/``:

    * ``input_batch_NN.smi`` — SMILES plus ligand_id, chunked to
      ``admet.external_batch_size``, in the format the tool's form accepts.
    * ``paste_back_template.csv`` — the exact header the pipeline will read,
      pre-filled with ligand_ids and empty value columns.
    * ``README.md`` — the schema, with the dtype and meaning of every column,
      and the provenance fields that must be recorded.
    """
    written: list[str] = []
    for tool in cfg.admet.external_tools:
        schema = EXTERNAL_TOOL_SCHEMAS.get(tool)
        if schema is None:
            log.warning(
                "No paste-back schema defined for external tool; skipping",
                extra={"tool": tool, "known": sorted(EXTERNAL_TOOL_SCHEMAS)},
            )
            continue

        tool_dir = out_dir / "external_tools" / tool
        tool_dir.mkdir(parents=True, exist_ok=True)

        size = cfg.admet.external_batch_size
        for index in range(0, len(rows), size):
            chunk = rows[index : index + size]
            batch = tool_dir / f"input_batch_{index // size + 1:02d}.smi"
            batch.write_text(
                "".join(f"{r.smiles}\t{r.ligand_id}\n" for r in chunk), encoding="utf-8"
            )
            written.append(batch.as_posix())

        template = tool_dir / "paste_back_template.csv"
        columns = list(schema)
        with template.open("w", encoding="utf-8", newline="\n") as fh:
            writer = csv.writer(fh, lineterminator="\n")
            writer.writerow(columns)
            for row in rows:
                writer.writerow([row.ligand_id] + [""] * (len(columns) - 1))
        written.append(template.as_posix())

        readme = tool_dir / "README.md"
        readme.write_text(_external_readme(tool, schema, len(rows)), encoding="utf-8")
        written.append(readme.as_posix())

    return written


def _external_readme(tool: str, schema: dict[str, tuple[str, str]], n_ligands: int) -> str:
    lines = [
        f"# {tool}: manual retrieval handoff",
        "",
        f"npcadd has prepared {n_ligands} ligand(s) for submission to **{tool}**.",
        "",
        "## Why this is manual",
        "",
        f"{tool} is an HTML form with no public, versioned API. npcadd does not scrape",
        "it: a scraped value cannot be attributed to a specific model version on a",
        "specific date, and an unattributable number has no place in a manuscript.",
        "",
        "## Steps",
        "",
        f"1. Submit `input_batch_NN.smi` to {tool}. Each line is `SMILES<TAB>ligand_id`.",
        "2. Export the results.",
        "3. Transcribe them into `paste_back_template.csv`, keeping `ligand_id`",
        "   exactly as given. Do not reorder or rename columns.",
        "4. Save it as `results.csv` in this directory.",
        "5. Record the tool version and retrieval date in `provenance.txt` here, using",
        "   the template at the bottom of this file.",
        "",
        "npcadd validates `results.csv` against the schema below on read and refuses",
        "values it cannot parse, rather than coercing them silently.",
        "",
        "## Schema",
        "",
        "| Column | Type | Meaning |",
        "|---|---|---|",
    ]
    for column, (dtype, description) in schema.items():
        lines.append(f"| `{column}` | `{dtype}` | {description} |")
    lines += [
        "",
        "Type notes: `bool` accepts Yes/No, True/False, 1/0, Active/Inactive.",
        "`category:A,B` accepts only the listed values. Blank cells are read as",
        "missing and are reported as missing, never as a default value.",
        "",
        "## provenance.txt template",
        "",
        "```",
        f"tool: {tool}",
        "version: <the version string the tool displays>",
        "retrieved_utc: <YYYY-MM-DDTHH:MM:SSZ>",
        "url: <the exact URL used>",
        "notes: <any non-default settings chosen on the form>",
        "```",
        "",
    ]
    return "\n".join(lines)


def _coerce(value: str, dtype: str, column: str, ligand_id: str) -> Any:
    """Parse one pasted-back cell, or raise with a specific message."""
    text = (value or "").strip()
    if not text:
        return None
    if dtype == "str":
        return text
    if dtype == "bool":
        lowered = text.lower()
        if lowered in _TRUE:
            return True
        if lowered in _FALSE:
            return False
        raise ValueError(f"{ligand_id}: column {column!r} expected a yes/no value, got {text!r}")
    if dtype == "int":
        try:
            return int(float(text))
        except ValueError as exc:
            raise ValueError(
                f"{ligand_id}: column {column!r} expected an integer, got {text!r}"
            ) from exc
    if dtype == "float":
        try:
            return float(text.replace(",", ""))
        except ValueError as exc:
            raise ValueError(
                f"{ligand_id}: column {column!r} expected a number, got {text!r}"
            ) from exc
    if dtype.startswith("category:"):
        allowed = dtype.split(":", 1)[1].split(",")
        if text not in allowed:
            raise ValueError(
                f"{ligand_id}: column {column!r} expected one of {allowed}, got {text!r}"
            )
        return text
    raise ValueError(f"Unknown dtype {dtype!r} in the schema for column {column!r}")


def read_external_tool_csv(tool: str, path: Path) -> pd.DataFrame:
    """Read and validate a pasted-back results CSV.

    Raises:
        KeyError: if the tool has no schema.
        FileNotFoundError: if the file is absent.
        ValueError: if a required column is missing or any cell fails to parse.
            Nothing is coerced silently: a bad cell is an error, because a
            quietly defaulted toxicity prediction is worse than no prediction.
    """
    schema = EXTERNAL_TOOL_SCHEMAS.get(tool)
    if schema is None:
        raise KeyError(f"No paste-back schema for tool {tool!r}")
    if not path.is_file():
        raise FileNotFoundError(f"No pasted-back results for {tool} at {path}")

    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    missing = [c for c in schema if c not in frame.columns]
    if missing:
        raise ValueError(
            f"{path} is missing required column(s) {missing}. Expected exactly: {list(schema)}"
        )

    parsed: list[dict[str, Any]] = []
    errors: list[str] = []
    for row in frame.to_dict(orient="records"):
        ligand_id = str(row.get("ligand_id", "")).strip()
        if not ligand_id:
            errors.append("a row has an empty ligand_id")
            continue
        entry: dict[str, Any] = {}
        for column, (dtype, _desc) in schema.items():
            try:
                entry[f"{tool}_{column}" if column != "ligand_id" else column] = _coerce(
                    str(row.get(column, "")), dtype, column, ligand_id
                )
            except ValueError as exc:
                errors.append(str(exc))
        parsed.append(entry)

    if errors:
        raise ValueError(f"{len(errors)} problem(s) in {path}:\n  - " + "\n  - ".join(errors[:25]))
    return pd.DataFrame(parsed)


def descriptor_columns() -> list[str]:
    """Descriptor column names, in canonical order, for table assembly."""
    return list(DESCRIPTOR_FIELDS)
