"""Unit tests for the member-summary output guards (pure functions, no I/O)."""

from decimal import Decimal

from src.claimbridge.summaries.guards import check_amounts, check_citations, check_safety


def test_missing_code_citation_feedback_names_the_code():
    issues = check_citations("Charge above allowed.", ["C1", "P1"], {"C1", "C2", "P1"}, {"C1", "C2"},
                             "PARTIAL", labels={"C2": "CO-50 (Not medically necessary)"})
    assert "Required code citations missing: ['C2']" in issues
    assert any("CO-50" in i and '"C2"' in i for i in issues)


def test_invented_citation_and_computed_amount_rejected():
    assert check_citations("x", ["C9"], {"C1"}, set(), "DENY")
    assert check_amounts(["the $120.00 difference"], [Decimal("285"), Decimal("165")])
    assert not check_amounts(["you owe $33.00"], [Decimal("33.00")])


def test_safety_phrases():
    assert check_safety(["Payment is guaranteed."])
    assert not check_safety(["The claim was missing a diagnosis code."])


def test_adjusted_claim_must_cite_a_retrieved_policy():
    issues = check_citations("Above allowed.", ["C1"], {"C1", "P1", "P2"}, {"C1"}, "PARTIAL", policy_ids={"P1", "P2"})
    assert any("No plan policy cited" in i for i in issues)
    assert not check_citations("Above allowed.", ["C1", "P2"], {"C1", "P1", "P2"}, {"C1"}, "PARTIAL", policy_ids={"P1", "P2"})
    # an approved claim needs no reason, so no policy citation either
    assert not check_citations("", [], {"P1"}, set(), "APPROVE", policy_ids={"P1"})


def test_percentages_rejected_dollars_allowed():
    from src.claimbridge.summaries.guards import check_percentages
    assert check_percentages(["The plan pays 60% of the allowed amount."])
    assert check_percentages(["The plan pays 60 percent."])
    assert not check_percentages(["The plan paid $4,960.00 of the $6,200.00 allowed amount."])


def test_example_cause_not_stated_as_fact():
    from src.claimbridge.summaries.guards import cause_terms, check_unsupported_causes
    causes = "Missing diagnosis, invalid modifier, missing referring provider NPI, incomplete UB-04 fields"
    assert cause_terms(causes) == ["diagnosis", "modifier", "npi", "ub-04"]
    assert check_unsupported_causes(["It did not include a diagnosis."], {"CO-16": causes}, '{"notes": "staging claim"}')
    assert not check_unsupported_causes(["It did not include a diagnosis."], {"CO-16": causes}, '{"notes": "diagnosis missing"}')
    assert not check_unsupported_causes(["Some information was missing."], {"CO-16": causes}, "{}")
