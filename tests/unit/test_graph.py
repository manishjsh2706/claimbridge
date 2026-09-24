"""
Draft graph - ClaimBridge
=========================

These tests pin the SHAPE of the generation workflow, not the wording of any
summary. They feed the graph scripted callables, so no database, no Weaviate
and no OpenAI call is involved, and they run in CI on every push.

Each test is one branch of the loop the graph replaced. If a refactor quietly
drops the retry, or lets a rejected draft through, or retries a model that is
already known to be down, one of these goes red.
"""

import pytest

from src.claimbridge.summaries.graph import build_draft_graph, run_draft


class FakeUnavailable(Exception):
    """Stands in for llm.LLMUnavailable, so the test needs no LLM client."""


def harness(*, gen_results, validate_results, escalate=False, max_attempts=2):
    """
    gen_results: one entry per expected generate() call -- either a fake LLM
    result or an exception instance to raise.
    validate_results: one list of issues per expected validate() call ([] = clean).
    """
    calls = {"generate": 0, "validate": 0, "template": 0, "escalate": 0,
             "feedback": [], "require_policy_citation": []}
    remaining = list(validate_results)

    def generate(feedback):
        calls["generate"] += 1
        calls["feedback"].append(feedback)
        result = gen_results[calls["generate"] - 1]
        if isinstance(result, Exception):
            raise result
        return result

    def validate(draft, require_policy_citation):
        calls["validate"] += 1
        calls["require_policy_citation"].append(require_policy_citation)
        return remaining.pop(0)

    def template():
        calls["template"] += 1
        return {"kind": "template"}

    def escalation():
        calls["escalate"] += 1
        return {"kind": "escalation"}

    graph = build_draft_graph(
        generate=generate, validate=validate, template=template,
        escalation=escalation, escalation_reason="Emergency-related denial",
        needs_escalation=lambda: escalate, unavailable_exc=FakeUnavailable,
        max_attempts=max_attempts,
    )
    return run_draft(graph, "corr-test", "member"), calls


def llm_result(tokens):
    return {"model": "gpt-4o-mini", "usage": {"total_tokens": tokens}, "data": {"kind": "llm"}}


def test_clean_first_attempt_is_stored_as_is():
    state, calls = harness(gen_results=[llm_result(100)], validate_results=[[]])
    assert state["passed"] is True
    assert state["attempts"] == 1
    assert state["generation_mode"] == "llm"
    assert state["data"] == {"kind": "llm"}
    assert state["usage_total"] == 100
    assert state["issues"] == []
    assert calls["template"] == 0


def test_failed_guards_are_fed_back_into_the_retry():
    state, calls = harness(gen_results=[llm_result(100), llm_result(120)],
                           validate_results=[["amount wrong"], []])
    assert state["passed"] is True
    assert state["attempts"] == 2
    assert state["generation_mode"] == "llm"
    # The whole point of the retry: the model is told what it got wrong.
    assert calls["feedback"] == [None, ["amount wrong"]]
    assert state["usage_total"] == 220, "tokens from both attempts must be counted"
    assert calls["template"] == 0


def test_two_failed_attempts_fall_back_to_the_template():
    state, calls = harness(gen_results=[llm_result(100), llm_result(120)],
                           validate_results=[["amount wrong"], ["still wrong"], []])
    assert state["generation_mode"] == "template_fallback"
    assert state["needs_human_review"] is True
    # A stored draft must never contain text that failed a guard.
    assert state["data"] == {"kind": "template"}
    assert state["passed"] is True
    assert state["issues"] == ["LLM draft rejected: still wrong"]
    assert state["model"] == "template-fallback (llm: gpt-4o-mini)"
    # The template cannot judge which policy section is relevant, so it is
    # validated without the policy-citation requirement.
    assert calls["require_policy_citation"] == [True, True, False]


def test_a_template_that_fails_a_guard_is_reported_not_hidden():
    state, _ = harness(gen_results=[llm_result(100), llm_result(120)],
                       validate_results=[["a"], ["b"], ["template bad"]])
    assert state["passed"] is False
    assert state["issues"] == ["LLM draft rejected: b", "template bad"]


def test_an_unreachable_model_is_not_retried():
    state, calls = harness(gen_results=[FakeUnavailable("connection refused")],
                           validate_results=[[]])
    assert calls["generate"] == 1, "retrying a model that is down only adds latency"
    assert state["attempts"] == 1
    assert state["generation_mode"] == "template_fallback"
    assert state["issues"] == ["LLM draft rejected: LLM unavailable: connection refused"]
    assert state["model"] == "template-fallback"


def test_an_emergency_denial_never_reaches_the_model():
    state, calls = harness(gen_results=[], validate_results=[], escalate=True)
    assert calls["generate"] == 0
    assert calls["validate"] == 0
    assert calls["escalate"] == 1
    assert state["generation_mode"] == "fixed_escalation"
    assert state["passed"] is True
    assert state["needs_human_review"] is True
    assert state["escalation_reason"] == "Emergency-related denial"


def test_an_unexpected_error_is_not_swallowed_as_a_fallback():
    """
    Only "the model is unreachable" routes to the template. A bug in prompt
    building must surface, not be papered over with a template draft that
    hides it.
    """
    with pytest.raises(ValueError):
        harness(gen_results=[ValueError("bug in build_prompts")], validate_results=[])
