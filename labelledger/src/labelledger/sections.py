"""The SPL section catalogue: LOINC section codes, canonical families and risk tiers.

Every section of a Structured Product Label carries a LOINC code. Codes are not
enough to compare labels on their own, because the same clinical content lives
under different codes in old-format and PLR-format labels (``WARNINGS`` and
``PRECAUTIONS`` versus ``WARNINGS AND PRECAUTIONS``), and subsections are
usually coded ``42229-5`` (unclassified). So each code maps to a *family*, and
unclassified subsections inherit their parent's family.

Tiers drive severity triage:

* **Tier 1 — safety-critical.** Changes here can alter how the drug is
  prescribed or what risks are communicated. Reference-label changes in these
  sections are what a generic label must follow.
* **Tier 2 — clinical.** Pharmacology, studies, patient-facing labeling.
* **Tier 3 — administrative / product-specific.** Description, supply,
  package panels — legitimately different between reference and generic.

The code list follows the FDA SPL section LOINC codes used on DailyMed. Unknown
codes fall back to the ``other`` family (tier 2) rather than being dropped, so
an unrecognised section is still diffed and reported.
"""

from __future__ import annotations

from dataclasses import dataclass

UNCLASSIFIED = "42229-5"


@dataclass(frozen=True)
class Family:
    key: str
    label: str
    tier: int
    #: Expected to differ between a reference and a generic label (supply,
    #: description, packaging). Drift detection skips these by default.
    product_specific: bool = False


FAMILIES: dict[str, Family] = {
    f.key: f
    for f in (
        Family("boxed_warning", "Boxed Warning", 1),
        Family("indications", "Indications and Usage", 1),
        Family("dosage_administration", "Dosage and Administration", 1),
        Family("dosage_forms", "Dosage Forms and Strengths", 2, product_specific=True),
        Family("contraindications", "Contraindications", 1),
        Family("warnings_precautions", "Warnings and Precautions", 1),
        Family("adverse_reactions", "Adverse Reactions", 1),
        Family("drug_interactions", "Drug Interactions", 1),
        Family("specific_populations", "Use in Specific Populations", 1),
        Family("abuse_dependence", "Drug Abuse and Dependence", 1),
        Family("overdosage", "Overdosage", 1),
        Family("description", "Description", 3, product_specific=True),
        Family("clinical_pharmacology", "Clinical Pharmacology", 2),
        Family("nonclinical_toxicology", "Nonclinical Toxicology", 2),
        Family("clinical_studies", "Clinical Studies", 2),
        Family("references", "References", 3, product_specific=True),
        Family("how_supplied", "How Supplied / Storage and Handling", 3, product_specific=True),
        Family("patient_counseling", "Patient Counseling Information", 2),
        Family("patient_labeling", "Patient Labeling (Medication Guide / PPI / IFU)", 2),
        Family("recent_major_changes", "Recent Major Changes", 3, product_specific=True),
        Family(
            "package_label", "Package Label / Principal Display Panel", 3, product_specific=True
        ),
        Family("product_data", "Product Data Elements", 3, product_specific=True),
        Family("other", "Other / Unclassified", 2),
    )
}

#: LOINC section code -> family key.
CODE_FAMILY: dict[str, str] = {
    "34066-1": "boxed_warning",
    "34067-9": "indications",
    "34068-7": "dosage_administration",
    "43678-2": "dosage_forms",
    "34070-3": "contraindications",
    "43685-7": "warnings_precautions",
    "34071-1": "warnings_precautions",  # WARNINGS (old format)
    "42232-9": "warnings_precautions",  # PRECAUTIONS (old format)
    "34072-9": "warnings_precautions",  # GENERAL PRECAUTIONS
    "34075-2": "warnings_precautions",  # LABORATORY TESTS
    "34073-7": "drug_interactions",
    "34074-5": "drug_interactions",  # DRUG & OR LABORATORY TEST INTERACTIONS
    "34084-4": "adverse_reactions",
    "43684-0": "specific_populations",
    "42228-7": "specific_populations",  # PREGNANCY
    "77290-5": "specific_populations",  # LACTATION
    "77291-3": "specific_populations",  # FEMALES & MALES OF REPRODUCTIVE POTENTIAL
    "34077-8": "specific_populations",  # TERATOGENIC EFFECTS
    "34078-6": "specific_populations",  # NONTERATOGENIC EFFECTS
    "34079-4": "specific_populations",  # LABOR & DELIVERY
    "34080-2": "specific_populations",  # NURSING MOTHERS
    "34081-0": "specific_populations",  # PEDIATRIC USE
    "34082-8": "specific_populations",  # GERIATRIC USE
    "42227-9": "abuse_dependence",
    "34085-1": "abuse_dependence",  # CONTROLLED SUBSTANCE
    "34086-9": "abuse_dependence",  # ABUSE
    "34087-7": "abuse_dependence",  # DEPENDENCE
    "34088-5": "overdosage",
    "34089-3": "description",
    "34090-1": "clinical_pharmacology",
    "43679-0": "clinical_pharmacology",  # MECHANISM OF ACTION
    "43681-6": "clinical_pharmacology",  # PHARMACODYNAMICS
    "43682-4": "clinical_pharmacology",  # PHARMACOKINETICS
    "43680-8": "nonclinical_toxicology",
    "34083-6": "nonclinical_toxicology",  # CARCINOGENESIS, MUTAGENESIS, IMPAIRMENT OF FERTILITY
    "34091-9": "nonclinical_toxicology",  # ANIMAL PHARMACOLOGY &/OR TOXICOLOGY
    "34092-7": "clinical_studies",
    "34093-5": "references",
    "34069-5": "how_supplied",
    "44425-7": "how_supplied",  # STORAGE AND HANDLING
    "34076-0": "patient_counseling",  # INFORMATION FOR PATIENTS / PATIENT COUNSELING
    "68498-5": "patient_labeling",  # PATIENT MEDICATION INFORMATION
    "42231-1": "patient_labeling",  # MEDICATION GUIDE
    "42230-3": "patient_labeling",  # PATIENT PACKAGE INSERT
    "59845-8": "patient_labeling",  # INSTRUCTIONS FOR USE
    "43683-2": "recent_major_changes",
    "51945-4": "package_label",
    "48780-1": "product_data",
}

#: Families compared as one pool in drift detection, because labels file the
#: same patient-facing content under different codes.
DRIFT_POOLS: dict[str, str] = {
    "patient_counseling": "patient_information",
    "patient_labeling": "patient_information",
}

POOL_LABELS: dict[str, str] = {
    "patient_information": "Patient Information (counseling and patient labeling)"
}


def family_for(code: str | None, parent_family: str | None) -> str:
    """Resolve a section's family from its code, inheriting for unclassified codes."""
    if code and code in CODE_FAMILY:
        return CODE_FAMILY[code]
    if parent_family is not None:
        return parent_family
    return "other"


def tier_of(family: str) -> int:
    return FAMILIES.get(family, FAMILIES["other"]).tier


def label_of(family: str) -> str:
    if family in FAMILIES:
        return FAMILIES[family].label
    return POOL_LABELS.get(family, family.replace("_", " ").title())


def drift_pool(family: str) -> str:
    return DRIFT_POOLS.get(family, family)


def is_product_specific(family: str) -> bool:
    return FAMILIES.get(family, FAMILIES["other"]).product_specific
