#!/usr/bin/env python3
"""Generate the clearly-labelled SYNTHETIC sample dataset under ``data/sample/``.

Read this before using the sample data for anything
----------------------------------------------------
* The compound **names and SMILES are real chemistry** — they are published
  structures of natural products that plausibly occur in a plant extract, and
  each SMILES is validated here against its known molecular formula.
* The **GC-MS area percentages are fabricated**. They are not measurements. They
  were produced by the seeded pseudo-random draw in this script purely so the
  pipeline has a realistic-looking abundance distribution to exercise. Every row
  carries ``data_provenance=SYNTHETIC_PLACEHOLDER`` so the fabrication cannot be
  mistaken for data.
* The **retention times are likewise fabricated** and are included only because
  real GC-MS exports carry them.

Replace this file with your own GC-MS component table before reporting any
result. Nothing in ``data/sample/`` may appear in a manuscript.

Run with::

    python scripts/make_sample_data.py
"""

from __future__ import annotations

import csv
import random
import sys
from pathlib import Path

from rdkit import Chem
from rdkit.Chem import Descriptors, rdMolDescriptors

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = REPO_ROOT / "data" / "sample"

SYNTHETIC_FLAG = "SYNTHETIC_PLACEHOLDER"

# (name, SMILES, expected molecular formula, compound class)
# Classes follow the manuscript's scheme: phenolic / terpenoid / other.
COMPOUNDS: list[tuple[str, str, str, str]] = [
    # --- Terpenoids: the lead series ---------------------------------------
    ("Loliolide", "CC1(C)CC(O)CC2(C)OC(=O)C=C12", "C11H16O3", "terpenoid"),
    ("Spathulenol", "CC1(C)C2CCC(=C)C3CCC(C)(O)C3C12", "C15H24O", "terpenoid"),
    ("alpha-Pinene", "CC1=CCC2CC1C2(C)C", "C10H16", "terpenoid"),
    ("beta-Pinene", "CC1(C)C2CCC(=C)C1C2", "C10H16", "terpenoid"),
    ("Limonene", "CC(=C)C1CCC(C)=CC1", "C10H16", "terpenoid"),
    ("Linalool", "CC(C)=CCCC(C)(O)C=C", "C10H18O", "terpenoid"),
    ("Geraniol", "CC(C)=CCCC(C)=CCO", "C10H18O", "terpenoid"),
    ("Nerolidol", "CC(C)=CCCC(C)=CCCC(C)(O)C=C", "C15H26O", "terpenoid"),
    ("Farnesol", "CC(C)=CCCC(C)=CCCC(C)=CCO", "C15H26O", "terpenoid"),
    ("beta-Caryophyllene", "CC1=CCCC(=C)C2CC(C)(C)C2CC1", "C15H24", "terpenoid"),
    ("alpha-Humulene", "CC1=CCC(C)(C)C=CCC(C)=CCC1", "C15H24", "terpenoid"),
    ("Caryophyllene oxide", "CC12CCCC(=C)C3CC(C)(C)C3CC1O2", "C15H24O", "terpenoid"),
    ("Camphor", "CC1(C)C2CCC1(C)C(=O)C2", "C10H16O", "terpenoid"),
    ("Borneol", "CC1(C)C2CCC1(C)C(O)C2", "C10H18O", "terpenoid"),
    ("alpha-Terpineol", "CC(C)(O)C1CCC(C)=CC1", "C10H18O", "terpenoid"),
    ("1,8-Cineole", "CC1(C)C2CCC(C)(CC2)O1", "C10H18O", "terpenoid"),
    ("Menthol", "CC(C)C1CCC(C)CC1O", "C10H20O", "terpenoid"),
    ("Phytol", "CC(C)CCCC(C)CCCC(C)CCCC(C)=CCO", "C20H40O", "terpenoid"),
    ("Squalene", "CC(C)=CCCC(C)=CCCC(C)=CCCC=C(C)CCC=C(C)CCC=C(C)C", "C30H50", "terpenoid"),
    ("Lupeol", None, "C30H50O", "terpenoid"),  # SMILES filled in below
    # --- Phenolics ---------------------------------------------------------
    ("Gallic acid", "OC(=O)c1cc(O)c(O)c(O)c1", "C7H6O5", "phenolic"),
    ("Protocatechuic acid", "OC(=O)c1ccc(O)c(O)c1", "C7H6O4", "phenolic"),
    ("Caffeic acid", "OC(=O)C=Cc1ccc(O)c(O)c1", "C9H8O4", "phenolic"),
    ("Ferulic acid", "COc1cc(C=CC(=O)O)ccc1O", "C10H10O4", "phenolic"),
    ("p-Coumaric acid", "OC(=O)C=Cc1ccc(O)cc1", "C9H8O3", "phenolic"),
    ("Vanillin", "COc1cc(C=O)ccc1O", "C8H8O3", "phenolic"),
    ("Vanillic acid", "COc1cc(C(=O)O)ccc1O", "C8H8O4", "phenolic"),
    ("Syringic acid", "COc1cc(C(=O)O)cc(OC)c1O", "C9H10O5", "phenolic"),
    ("Eugenol", "C=CCc1ccc(O)c(OC)c1", "C10H12O2", "phenolic"),
    ("Thymol", "CC(C)c1ccc(C)cc1O", "C10H14O", "phenolic"),
    ("Carvacrol", "CC(C)c1ccc(C)c(O)c1", "C10H14O", "phenolic"),
    ("Quercetin", "O=c1c(O)c(-c2ccc(O)c(O)c2)oc2cc(O)cc(O)c12", "C15H10O7", "phenolic"),
    ("Kaempferol", "O=c1c(O)c(-c2ccc(O)cc2)oc2cc(O)cc(O)c12", "C15H10O6", "phenolic"),
    ("Apigenin", "O=c1cc(-c2ccc(O)cc2)oc2cc(O)cc(O)c12", "C15H10O5", "phenolic"),
    ("Luteolin", "O=c1cc(-c2ccc(O)c(O)c2)oc2cc(O)cc(O)c12", "C15H10O6", "phenolic"),
    ("Catechin", "Oc1cc(O)c2CC(O)C(c3ccc(O)c(O)c3)Oc2c1", "C15H14O6", "phenolic"),
    ("Resveratrol", "Oc1ccc(C=Cc2cc(O)cc(O)c2)cc1", "C14H12O3", "phenolic"),
    ("Pyrogallol", "Oc1cccc(O)c1O", "C6H6O3", "phenolic"),
    ("Catechol", "Oc1ccccc1O", "C6H6O2", "phenolic"),
    ("4-Hydroxybenzoic acid", "OC(=O)c1ccc(O)cc1", "C7H6O3", "phenolic"),
    # --- Other: fatty acids, esters, sterols, misc -------------------------
    ("Palmitic acid", "CCCCCCCCCCCCCCCC(=O)O", "C16H32O2", "other"),
    ("Stearic acid", "CCCCCCCCCCCCCCCCCC(=O)O", "C18H36O2", "other"),
    ("Myristic acid", "CCCCCCCCCCCCCC(=O)O", "C14H28O2", "other"),
    ("Lauric acid", "CCCCCCCCCCCC(=O)O", "C12H24O2", "other"),
    ("Oleic acid", "CCCCCCCCC=CCCCCCCCC(=O)O", "C18H34O2", "other"),
    ("Linoleic acid", "CCCCCC=CCC=CCCCCCCCC(=O)O", "C18H32O2", "other"),
    ("alpha-Linolenic acid", "CCC=CCC=CCC=CCCCCCCCC(=O)O", "C18H30O2", "other"),
    ("Methyl palmitate", "CCCCCCCCCCCCCCCC(=O)OC", "C17H34O2", "other"),
    ("Methyl linoleate", "CCCCCC=CCC=CCCCCCCCC(=O)OC", "C19H34O2", "other"),
    ("Ethyl palmitate", "CCCCCCCCCCCCCCCC(=O)OCC", "C18H36O2", "other"),
    ("Glycerol monostearate", "CCCCCCCCCCCCCCCCCC(=O)OCC(O)CO", "C21H42O4", "other"),
    ("1-Hexadecanol", "CCCCCCCCCCCCCCCCO", "C16H34O", "other"),
    ("1-Octadecanol", "CCCCCCCCCCCCCCCCCCO", "C18H38O", "other"),
    ("Nonacosane", "CCCCCCCCCCCCCCCCCCCCCCCCCCCCC", "C29H60", "other"),
    ("Heptacosane", "CCCCCCCCCCCCCCCCCCCCCCCCCCC", "C27H56", "other"),
    ("beta-Sitosterol", None, "C29H50O", "other"),
    ("Stigmasterol", None, "C29H48O", "other"),
    ("Campesterol", None, "C28H48O", "other"),
    ("alpha-Tocopherol", None, "C29H50O2", "other"),
    ("Furfural", "O=Cc1ccco1", "C5H4O2", "other"),
    ("5-Hydroxymethylfurfural", "OCc1ccc(C=O)o1", "C6H6O3", "other"),
    ("Benzoic acid", "OC(=O)c1ccccc1", "C7H6O2", "other"),
    ("Phenylacetic acid", "OC(=O)Cc1ccccc1", "C8H8O2", "other"),
    ("Indole-3-carboxaldehyde", "O=Cc1c[nH]c2ccccc12", "C9H7NO", "other"),
    ("Squalane", "CC(C)CCCC(C)CCCC(C)CCCCC(C)CCCC(C)CCCC(C)C", "C30H62", "other"),
    ("Neophytadiene", "CC(C)CCCC(C)CCCC(C)CCCC(=C)C=C", "C20H38", "other"),
    ("2,3-Dihydrobenzofuran", "C1Cc2ccccc2O1", "C8H8O", "other"),
    ("Maltol", "Cc1occc(=O)c1O", "C6H6O3", "other"),
    ("Pantolactone", "CC1(C)C(O)C(=O)OC1", "C6H10O3", "other"),
]

# Larger terpenoid / sterol SMILES, kept separate for readability.
LARGE_SMILES: dict[str, str] = {
    "Lupeol": ("CC(=C)C1CCC2(C)CCC3(C)C(CCC4C5(C)CCC(O)C(C)(C)C5CCC34C)C12"),
    "beta-Sitosterol": ("CCC(CCC(C)C1CCC2(C)C1CCC1C2CC=C2CC(O)CCC12C)C(C)C"),
    "Stigmasterol": ("CCC(C=CC(C)C1CCC2(C)C1CCC1C2CC=C2CC(O)CCC12C)C(C)C"),
    "Campesterol": ("CC(CCC(C)C1CCC2(C)C1CCC1C2CC=C2CC(O)CCC12C)C(C)C"),
    "alpha-Tocopherol": ("CC1=C(C)C2=C(CCC(C)(CCCC(C)CCCC(C)CCCC(C)C)O2)C(C)=C1O"),
}


def validate(name: str, smiles: str, expected_formula: str) -> tuple[Chem.Mol, str]:
    """Parse a SMILES and assert it matches the published molecular formula."""
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ValueError(f"{name}: SMILES does not parse: {smiles}")
    formula = rdMolDescriptors.CalcMolFormula(mol)
    bare = formula.rstrip("+-").split("+")[0].split("-")[0]
    if bare != expected_formula:
        raise ValueError(
            f"{name}: SMILES gives formula {formula} but the published formula is "
            f"{expected_formula}. Fix the SMILES rather than the expectation."
        )
    return mol, formula


def main() -> int:
    rng = random.Random(0xA11CE)  # fixed seed: the fabricated numbers are reproducible
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, str]] = []
    problems: list[str] = []

    resolved: list[tuple[str, str, str, str]] = []
    for name, smiles, formula, klass in COMPOUNDS:
        resolved.append((name, smiles or LARGE_SMILES[name], formula, klass))

    # Fabricated abundances: a log-normal-ish spread so that a handful of
    # components dominate, as in a real total-ion chromatogram.
    weights = [rng.lognormvariate(0.0, 1.15) for _ in resolved]
    total = sum(weights)
    areas = [100.0 * w / total for w in weights]

    for (name, smiles, formula, klass), area in zip(resolved, areas, strict=True):
        try:
            mol, actual_formula = validate(name, smiles, formula)
        except ValueError as exc:
            problems.append(str(exc))
            continue
        rows.append(
            {
                "name": name,
                "smiles": Chem.MolToSmiles(mol),
                "compound_class": klass,
                "area_percent": f"{area:.3f}",
                "retention_time_min": f"{rng.uniform(3.0, 48.0):.2f}",
                "molecular_formula": actual_formula,
                "monoisotopic_mass": f"{Descriptors.ExactMolWt(mol):.4f}",
                "data_provenance": SYNTHETIC_FLAG,
            }
        )

    if problems:
        print("SMILES validation failed:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    rows.sort(key=lambda r: float(r["area_percent"]), reverse=True)

    out_csv = SAMPLE_DIR / "gcms_components_SYNTHETIC.csv"
    fields = list(rows[0].keys())
    with out_csv.open("w", encoding="utf-8", newline="\n") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    # A 5-compound subset for the CI smoke run and the golden determinism test.
    smoke = [r for r in rows if r["name"] in {"Loliolide", "Spathulenol"}]
    smoke += [r for r in rows if r not in smoke][:3]
    smoke_csv = SAMPLE_DIR / "gcms_smoke_SYNTHETIC.csv"
    with smoke_csv.open("w", encoding="utf-8", newline="\n") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(smoke)

    # A deliberately dirty variant, to exercise the rejection paths.
    dirty = [
        {
            "name": "Sodium acetate (salt, should be rejected: too few heavy atoms)",
            "smiles": "CC(=O)[O-].[Na+]",
            "compound_class": "other",
            "area_percent": "0.100",
            "retention_time_min": "2.00",
            "molecular_formula": "C2H3NaO2",
            "monoisotopic_mass": "82.0030",
            "data_provenance": SYNTHETIC_FLAG,
        },
        {
            "name": "Unparseable entry (should be rejected)",
            "smiles": "C1=CC=CC=C1(",
            "compound_class": "other",
            "area_percent": "0.050",
            "retention_time_min": "2.50",
            "molecular_formula": "n/a",
            "monoisotopic_mass": "0.0000",
            "data_provenance": SYNTHETIC_FLAG,
        },
        {
            "name": "Phenylboronic acid (should be rejected: element filter)",
            "smiles": "OB(O)c1ccccc1",
            "compound_class": "other",
            "area_percent": "0.020",
            "retention_time_min": "9.90",
            "molecular_formula": "C6H7BO2",
            "monoisotopic_mass": "122.0539",
            "data_provenance": SYNTHETIC_FLAG,
        },
        {
            "name": "Loliolide duplicate under a second library name",
            "smiles": "CC1(C)CC(O)CC2(C)OC(=O)C=C12",
            "compound_class": "terpenoid",
            "area_percent": "0.300",
            "retention_time_min": "18.10",
            "molecular_formula": "C11H16O3",
            "monoisotopic_mass": "196.1099",
            "data_provenance": SYNTHETIC_FLAG,
        },
    ]
    dirty_csv = SAMPLE_DIR / "gcms_with_rejects_SYNTHETIC.csv"
    with dirty_csv.open("w", encoding="utf-8", newline="\n") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(smoke + dirty)

    print(f"Wrote {len(rows)} validated components -> {out_csv.relative_to(REPO_ROOT)}")
    print(f"Wrote {len(smoke)} smoke components     -> {smoke_csv.relative_to(REPO_ROOT)}")
    print(
        f"Wrote {len(smoke) + len(dirty)} rows with rejects -> {dirty_csv.relative_to(REPO_ROOT)}"
    )
    print(f"\nAll area_percent values are {SYNTHETIC_FLAG}: fabricated, not measured.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
