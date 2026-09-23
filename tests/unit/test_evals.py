"""Unit tests for the golden eval package (no API, no database, no real LLM)."""

import json
from types import SimpleNamespace

import pytest

from src.claimbridge.evals import load_golden_cases, rubric_verdict, run_checks, skip_reason
from src.claimbridge.evals.golden import GoldenCase, GoldenCaseError
from src.claimbridge.evals.judge import JudgeError, judge_summary
from src.claimbridge.knowledge import resources_dir

DIMS = ["accuracy", "grounding", "safety", "tenant_appropriateness", "plain_language", "actionability", "structure"]
TENANT = {"member_services_phone": "1-800-555-0142", "appeal_window_days": 60}


def scores(v=5, **over):
    return {d: {"score": over.get(d, v), "reason": ""} for d in DIMS}


def case(**raw):
    base = {"id": "t", "tenant_id": "pacific-hmo", "fixture_claim_id": "CLAIM-PH-001", "audience": "member"}
    base.update(raw)
    return GoldenCase(id=base["id"], tenant_id=base["tenant_id"], claim_id=base["fixture_claim_id"],
                      audience=base["audience"], scenario="", iteration_min=base.get("iteration_min", 1), raw=base)


def body(**summary_over):
    s = {"outcome": "PARTIAL", "status": "DRAFT", "service_description": "Office visit",
         "plain_language_summary": "Your plan paid part of this visit.", "what_happened": "Paid $132.00.",
         "why_adjusted": "Charge above the allowed amount.", "next_steps": ["Call Member Services."],
         "appeal_rights_summary": "Appeal within 60 days from EOB date. Call 1-800-555-0142.",
         "citations": [{"id": "C1", "source_type": "carc_definition", "code": "CO-45"},
                       {"id": "P1", "source_type": "tenant_policy", "document": "pacific-hmo-plan-summary",
                        "section": "fee-schedule.allowed-amounts"}]}
    s.update(summary_over)
    return {"summary": s, "validation": {"passed": True, "issues": []},
            "retrieved_sections": ["pacific-hmo-plan-summary#fee-schedule.allowed-amounts"]}


def failed(results):
    return {r.name for r in results if not r.passed}


def test_provided_golden_files_load_and_dedupe():
    cases = load_golden_cases(resources_dir() / "golden")
    ids = [c.id for c in cases]
    assert len(ids) == len(set(ids)) == 11
    assert {"ph-001-member", "ph-004-member"} == {c.id for c in cases if skip_reason(c, 1) is None}


def test_conflicting_duplicate_is_an_error(tmp_path):
    (tmp_path / "a.jsonl").write_text(json.dumps({"id": "x", "tenant_id": "t", "fixture_claim_id": "C", "audience": "member"}))
    (tmp_path / "b.jsonl").write_text(json.dumps({"id": "x", "tenant_id": "OTHER", "fixture_claim_id": "C", "audience": "member"}))
    with pytest.raises(GoldenCaseError):
        load_golden_cases(tmp_path)


def test_only_future_iterations_are_skipped():
    assert skip_reason(case(audience="provider", iteration_min=2), 2) is None
    assert skip_reason(case(input_mode="full_pipeline", iteration_min=2), 2) is None
    assert "iteration_min 3" in skip_reason(case(iteration_min=3), 2)


def test_provider_checks():
    from src.claimbridge.evals.checks import run_provider_checks
    c = case(audience="provider", expected_outcome="DENY", required_citations=["CO-197"],
             must_include_codes=["CO-197", "72148"], must_include_provider_actions=["prior auth", "resubmit"],
             forbidden_phrases=["call member services only"])
    notice = {"outcome": "DENY", "codes": {"carc": ["CO-197"], "rarc": []},
              "code_citations": [{"code": "CO-197"}], "billing_codes_reference": {"cpt": "72148"},
              "technical_summary": "Denied CO-197.", "correction_actions": ["Obtain prior auth and resubmit."],
              "resubmission_instructions": "Corrected claim.", "policy_citations": []}
    body = {"notice": notice, "validation": {"passed": True}}
    assert failed(run_provider_checks(c, 201, body)) == set()
    notice["correction_actions"] = ["Call member services."]
    assert "must_include_provider_actions" in failed(run_provider_checks(c, 201, body))


def test_good_body_passes_all_checks():
    c = case(expected_outcome="PARTIAL", required_citations=["CO-45"], forbidden_phrases=["guaranteed"],
             forbidden_doc_prefixes=["coastal", "summit"])
    assert failed(run_checks(c, 201, body(), TENANT)) == set()


def test_checks_catch_each_failure():
    c = case(expected_outcome="DENY", required_citations=["CO-197"], required_policy_sections=["prior-auth.imaging"],
             forbidden_phrases=["guaranteed"], forbidden_doc_prefixes=["pacific"])
    b = body(why_adjusted="Payment is guaranteed.", appeal_rights_summary="Call us.")
    assert failed(run_checks(c, 201, b, TENANT)) == {
        "expected_outcome", "required_citations", "required_policy_sections", "no_forbidden_phrases",
        "no_foreign_tenant_sources", "tenant_contact_and_appeal_window"}


def test_retrieved_but_uncited_foreign_section_is_a_leak():
    b = body()
    b["retrieved_sections"].append("coastal-ppo-plan-summary#oon.balance-billing")
    assert "no_foreign_tenant_sources" in failed(run_checks(case(forbidden_doc_prefixes=["coastal"]), 201, b, TENANT))


def test_api_error_stops_checks():
    r = run_checks(case(expected_outcome="PARTIAL"), 404, {"detail": "not found"}, TENANT)
    assert [x.name for x in r] == ["api_created"] and not r[0].passed


def test_rubric_verdict():
    assert rubric_verdict(scores(5), {"safety": 5}, []) == (True, [])
    ok, why = rubric_verdict(scores(5, safety=4), {"safety": 5}, [])
    assert not ok and "safety 4 < required 5" in why[0]
    assert not rubric_verdict(scores(5, accuracy=1), {}, [])[0]                   # never a 1
    assert not rubric_verdict(scores(3, safety=4), {}, [])[0]                     # critical avg 3.25
    assert not rubric_verdict(scores(5), {}, [{"statement": "in-network", "why": ""}])[0]


class FakeLLM:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []

    def complete_json(self, system, user, max_tokens=0):
        self.prompts.append(user)
        return {"data": self.answers.pop(0), "model": "fake", "usage": {"total_tokens": 1}}


def test_judge_retries_once_then_errors():
    good = {"scores": scores(4), "unsupported_claims": []}
    llm = FakeLLM({"scores": "bad"}, good)
    v = judge_summary(llm, {"x": 1}, {"f": 1}, [{"id": "C1", "text": "def"}])
    assert v["scores"]["accuracy"]["score"] == 4 and "unusable" in llm.prompts[1]
    with pytest.raises(JudgeError):
        judge_summary(FakeLLM({"scores": {}}, {"scores": {"accuracy": 9}}), {}, {}, [])
