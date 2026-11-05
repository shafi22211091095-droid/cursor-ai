from __future__ import annotations

import json
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from labelledger.ai import (
    DEFAULT_MODEL,
    FALLBACK_BETA,
    ChangeClaim,
    ImpactSummary,
    SummaryResult,
    build_payload,
    deterministic_summary,
    summarize,
    verify_claims,
)
from labelledger.diff import diff_documents

from .conftest import SYNTHETIC_EDITS


@pytest.fixture(scope="module")
def diff(warfarin_v20, warfarin_v21):
    return diff_documents(warfarin_v20, warfarin_v21)


def _cid(diff, heading_part):
    return next(c for c in diff.changes if heading_part in c.heading)


class FakeMessages:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


def fake_client(response=None, error=None):
    messages = FakeMessages(response, error)
    return SimpleNamespace(beta=SimpleNamespace(messages=messages)), messages


def good_summary(diff):
    aki = _cid(diff, "5.4")
    dose = _cid(diff, "2.3")
    return ImpactSummary(
        headline="New fatal AKI statement and lower usual starting dose",
        overview="Two clinically important updates.",
        changes=[
            ChangeClaim(
                change_id=aki.cid,
                hunk_ids=[aki.hunks[0].hid],
                summary="Adds a statement on fatal acute kidney injury.",
                evidence_quotes=["Cases of fatal acute kidney injury have been reported"],
                significance="major",
            ),
            ChangeClaim(
                change_id=dose.cid,
                hunk_ids=[dose.hunks[0].hid],
                summary="Usual initial dose range changes.",
                evidence_quotes=["is usually 2 to 4 mg once daily"],
                significance="critical",
            ),
            ChangeClaim(
                change_id=dose.cid,
                hunk_ids=[dose.hunks[0].hid],
                summary="Hallucinated claim.",
                evidence_quotes=["is usually 1 to 3 mg twice daily"],
                significance="critical",
            ),
        ],
        actions_for_generic_labeling=["Update section 5.4."],
        open_questions=[],
    )


def response_for(parsed, stop="end_turn"):
    usage = SimpleNamespace(
        input_tokens=1200,
        output_tokens=300,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=900,
    )
    return SimpleNamespace(
        stop_reason=stop, parsed_output=parsed, usage=usage, model=DEFAULT_MODEL, stop_details=None
    )


def test_deterministic_summary_quotes_only_diff_text(diff):
    s = deterministic_summary(diff)
    assert s.source == "deterministic" and s.verification_rate == 1.0
    assert s.headline.startswith("4 material change(s)")
    quotes = " ".join(q for c in s.claims for q in c.evidence_quotes)
    assert SYNTHETIC_EDITS["aki_sentence"].split(" have")[0] in quotes
    assert any("Dosage and Administration" in a for a in s.actions)
    assert s.claims[0].significance == "critical"


def test_verify_claims_accepts_true_quotes_and_flags_fabricated_ones(diff):
    verified = verify_claims(good_summary(diff).changes, diff)
    assert [v.verified for v in verified] == [True, True, False]
    assert any("quote not found" in p for p in verified[2].problems)


def test_verify_claims_unknown_ids(diff):
    bad = ChangeClaim(
        change_id="c99",
        hunk_ids=["c99.h1"],
        summary="x",
        evidence_quotes=["x"],
        significance="minor",
    )
    (v,) = verify_claims([bad], diff)
    assert not v.verified and "unknown change_id" in v.problems[0]
    no_quote = ChangeClaim(
        change_id=diff.changes[0].cid,
        hunk_ids=[],
        summary="x",
        evidence_quotes=[],
        significance="critical",
    )
    (v,) = verify_claims([no_quote], diff)
    assert not v.verified and "no evidence quotes" in v.problems


def test_summarize_with_model_verifies_and_sends_expected_request(diff):
    client, messages = fake_client(response_for(good_summary(diff)))
    result = summarize(diff, mode="on", client=client)
    assert result.source == "ai" and result.model == DEFAULT_MODEL
    assert round(result.verification_rate, 2) == 0.67
    assert "could not be verified" in (result.notice or "")
    assert result.usage == {
        "input_tokens": 1200,
        "output_tokens": 300,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 900,
    }
    (call,) = messages.calls
    assert call["model"] == DEFAULT_MODEL
    assert call["output_format"] is ImpactSummary
    assert call["fallbacks"] == "default" and call["betas"] == [FALLBACK_BETA]
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert call["output_config"] == {"effort": "medium"}
    assert "temperature" not in call
    payload = json.loads(call["messages"][0]["content"])
    assert {c["change_id"] for c in payload["changes"]} == {c.cid for c in diff.changes}


@pytest.mark.parametrize(
    ("stop", "parsed", "fragment"),
    [
        ("refusal", None, "declined"),
        ("max_tokens", None, "truncated"),
        ("end_turn", None, "could not be parsed"),
    ],
)
def test_summarize_falls_back_on_bad_responses(diff, stop, parsed, fragment):
    client, _ = fake_client(response_for(parsed, stop))
    result = summarize(diff, mode="on", client=client)
    assert result.source == "deterministic" and fragment in (result.notice or "")


def _status_error(cls, code):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls(message="boom", response=httpx.Response(code, request=request), body=None)


@pytest.mark.parametrize(
    ("error", "fragment"),
    [
        (lambda: _status_error(anthropic.AuthenticationError, 401), "credentials were rejected"),
        (lambda: _status_error(anthropic.RateLimitError, 429), "rate limited"),
        (lambda: _status_error(anthropic.BadRequestError, 400), "request rejected"),
        (lambda: _status_error(anthropic.InternalServerError, 500), "API error 500"),
        (
            lambda: anthropic.APIConnectionError(request=httpx.Request("POST", "https://x")),
            "could not reach",
        ),
    ],
)
def test_summarize_degrades_gracefully_on_api_errors(diff, error, fragment):
    client, _ = fake_client(error=error())
    result = summarize(diff, mode="on", client=client)
    assert result.source == "deterministic" and fragment in (result.notice or "")


def test_auto_mode_without_credentials_never_calls_the_api(diff, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("LABELLEDGER_AI", raising=False)
    result = summarize(diff)
    assert result.source == "deterministic" and result.notice is None
    assert summarize(diff, mode="off").source == "deterministic"


def test_no_material_changes_skip_the_model(warfarin_v20):
    client, messages = fake_client(response_for(None))
    result = summarize(diff_documents(warfarin_v20, warfarin_v20), mode="on", client=client)
    assert result.headline == "No changes detected" and messages.calls == []


def test_payload_budget_lists_omitted_changes(diff):
    payload = json.loads(build_payload(diff, budget=1500))
    assert payload["omitted"]["change_ids"]
    full = json.loads(build_payload(diff))
    assert "omitted" not in full
    editorial = next(c for c in full["changes"] if c["rules_severity"] == "editorial")
    assert "hunks" not in editorial


def test_summary_round_trip(diff):
    s = deterministic_summary(diff, notice="n")
    again = SummaryResult.from_dict(json.loads(json.dumps(s.to_dict())))
    assert again.to_dict() == s.to_dict()
