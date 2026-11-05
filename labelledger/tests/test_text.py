from __future__ import annotations

from labelledger import text as T


def test_compare_key_folds_typography_case_and_xrefs():
    a = "Warfarin can cause major or fatal bleeding [see Warnings and Precautions (5.1)]."
    b = "warfarin can cause major or fatal bleeding."
    assert T.compare_key(a) == T.compare_key(b)
    assert T.compare_key("patient’s INR – target") == T.compare_key("patient's INR - target")
    assert T.is_editorial("Tablets®, USP", "tablets, USP")


def test_numbers_ignore_cross_references_and_table_labels():
    assert T.numbers("usually 2 to 5 mg once daily (2.3)") == ["2", "5"]
    assert T.numbers("Table 1 displays ranges of 1,000 patients") == ["1000"]


def test_sentence_split_protects_abbreviations():
    sents = T.split_sentences("Avoid use, e.g. Warfarin with aspirin. Monitor INR. Dose: 5 mg.")
    assert sents[0].startswith("Avoid use, e.g. Warfarin")
    assert "Monitor INR." in sents


def test_heading_number():
    assert T.split_heading_number("5.4 Acute Kidney Injury") == ("5.4", "Acute Kidney Injury")
    assert T.split_heading_number("BOXED WARNING") == (None, "BOXED WARNING")
    assert T.title_key("5.10 Serious Reactions") == T.title_key("5.11 serious reactions")


def test_similarity_bounds():
    assert T.similarity("a b c", "a b c") == 1.0
    assert 0.0 <= T.similarity("abc", "xyz") < 0.5
