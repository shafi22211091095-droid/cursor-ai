# Test fixture provenance

All files in `spl/` are **real, unmodified** FDA Structured Product Labeling
documents as published on DailyMed (https://dailymed.nlm.nih.gov). DailyMed was
not reachable from the build sandbox, so they were retrieved on 2026-11-05 from
public GitHub mirrors of DailyMed downloads and verified by structure (HL7 v3
SPL root, setId, versionNumber, LOINC-coded sections). Re-download from DailyMed
by set ID to confirm before relying on them for anything other than tests.

| File | Set ID | Version | Effective | Labeler | Application | SHA-256 |
|---|---|---|---|---|---|---|
| `warfarin_remedyrepack_v20_2026.xml` | 1244c338-9140-425a-bf2a-9da6b4ac0287 | 20 | 2026-01-02 | REMEDYREPACK INC. | ANDA040616 | e06ce38bfcb1c94fe1236444ec6228b3c82c9eaf91b98f4ba9e7ce38027e2ba7 |
| `warfarin_tya_v2_2012.xml` | ed1d5b87-84d2-4ec7-b1bc-cf8f5096e938 | 2 | 2012-09-04 | TYA Pharmaceuticals | ANDA040145 | 920ca270d203036207d2ac04c0aa307d68eff9db0128247d0562ce53f7b90d2d |
| `warfarin_northwind_v1_2015.xml` | 0a6ed338-c270-4762-8db7-a30a6255e6ee | 1 | 2015-03-23 | Northwind Pharmaceuticals, LLC | ANDA040145 | 6944f4e3b6ba32c7d264b5257c6469716197b9c45737ca82a78fec277a5c89d9 |
| `metformin_v13.xml` | 3814cfed-029f-4231-bba3-89b2b4e01392 | 13 | 2025-12-31 | Legacy Pharmaceutical Packaging, LLC | ANDA077095 | 273bf16a3797fc7ab60fcdb5d03ce03bdaf48e8cc54d00b61da5a44a2befda7a |

Sources: `mohammed-shanid/medical-rag-phase1` (warfarin v20, metformin) and
`Lucifer267/RAG_Medical` (warfarin 2012 and 2015), both mirroring DailyMed.

## The synthetic version 21

`tests/conftest.py::synthetic_revision` derives a "version 21" from the real
version-20 warfarin label by applying five documented edits (a dose change, a
new fatal-risk sentence, a new subsection, a removed adverse-reaction item and
a case-only editorial change). It exists only in memory during tests and is
never presented as a real label. Tests assert the engine finds exactly those
five edits with the expected severities.

## Interpretation caution

The 2012 and 2015 labels are repackager labels. That they lack later safety
updates illustrates drift detection on real text; it is **not** a finding about
any company's compliance — an old repackager label may simply be inactive.
