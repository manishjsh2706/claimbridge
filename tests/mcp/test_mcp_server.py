"""
MCP server tools - ClaimBridge

These pin the boundary, not the claims logic: what a tool returns, what it
refuses, and -- most of it -- what it cannot be talked into doing.
"""

import inspect

import pytest


def test_whoami_reports_the_single_plan_the_key_is_scoped_to(mcp_server):
    who = mcp_server.whoami()
    assert who["health_plan"] == "pacific-hmo"
    assert who["role"] == "auditor"
    assert "claims:read" in who["permissions"]


def test_get_claim_returns_the_outcome_and_codes_verbatim(mcp_server):
    claim = mcp_server.get_claim("CLAIM-PH-004")
    assert claim["health_plan"] == "pacific-hmo"
    assert claim["adjudication"]["outcome"] == "DENY"
    assert claim["adjudication"]["carc_codes"] == ["CO-197"]


def test_recommendation_carries_the_rules_version_that_produced_it(mcp_server):
    rec = mcp_server.get_recommendation("CLAIM-PH-004")
    assert rec["recommendation"] == "DENY"
    # Without this an analyst cannot tell which rules decided, months later.
    assert rec["rules_version"] == "rules-2026-09-22.1"


def test_policy_search_returns_citable_section_paths(mcp_server):
    out = mcp_server.search_policy("prior authorization MRI")
    assert out["degraded"] is False
    assert out["sections"][0]["section_path"] == "prior-auth.imaging"


def test_unknown_codes_are_reported_not_guessed(mcp_server):
    out = mcp_server.explain_codes(["CO-197", "ZZ-999"])
    assert [c["code"] for c in out["known"]] == ["CO-197"]
    assert out["unknown"] == ["ZZ-999"]


def test_review_queue_is_readable_and_says_it_cannot_be_acted_on(mcp_server):
    out = mcp_server.review_queue()
    assert out["waiting"] == 1
    assert out["drafts"][0]["claim_id"] == "CLAIM-PH-004"
    assert "person" in out["note"]


def test_a_missing_claim_is_a_readable_refusal_not_a_traceback(mcp_server):
    with pytest.raises(mcp_server.ApiError) as excinfo:
        mcp_server.get_claim("CLAIM-CP-001")
    message = str(excinfo.value)
    assert "Not found" in message
    # A model told only "not found" would assure the analyst the claim does not
    # exist, when it may simply belong to another plan.
    assert "another plan" in message


def test_no_tool_takes_a_tenant_argument(mcp_server):
    """
    The tenant comes from the API key, resolved once at startup. If a tool ever
    grows a tenant parameter, prompt injection gains somewhere to aim.
    """
    for name in ("get_claim", "get_recommendation", "search_policy",
                 "explain_codes", "review_queue"):
        params = list(inspect.signature(getattr(mcp_server, name)).parameters)
        assert not any("tenant" in p for p in params), f"{name}{tuple(params)}"


def test_a_key_not_scoped_to_one_plan_is_refused_outright(mcp_server, monkeypatch):
    from tests.mcp.conftest import TENANT_WIDE_KEY
    mcp_server._identity.clear()
    monkeypatch.setattr(mcp_server, "API_KEY", TENANT_WIDE_KEY)
    with pytest.raises(mcp_server.ApiError, match="not scoped to a single health plan"):
        mcp_server.tenant()


def test_there_are_no_write_tools(mcp_server):
    """
    Approving is a human decision, and four-eyes (author != approver) only
    means something while a machine cannot do it. A tool named approve would
    let an assistant generate a draft and approve its own work in the next call.
    """
    for banned in ("approve", "publish", "reject", "submit_claim", "create_claim"):
        assert not hasattr(mcp_server, banned), f"{banned} must not exist as a tool"
