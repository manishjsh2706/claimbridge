"""Which sources the provider template cites (pure function, no I/O)."""

from types import SimpleNamespace

from src.claimbridge.summaries.provider import template_citation_ids


def _ctx(outcome, required=(), scores=()):
    return SimpleNamespace(
        required_ids=set(required),
        adjudication=SimpleNamespace(outcome=outcome),
        policy_hits=[{"similarity_score": s} for s in scores],
    )


def test_approved_claim_cites_no_policy_even_though_sections_were_retrieved():
    # Issue 7: the template attached every retrieved source, which is how a
    # denial-mapping policy ended up on an APPROVE notice.
    assert template_citation_ids(_ctx("APPROVE", scores=[1.0, 0.24, 0.21, 0.12])) == set()


def test_approved_claim_still_cites_the_codes_on_the_claim():
    assert template_citation_ids(_ctx("APPROVE", required={"C1"}, scores=[1.0, 0.2])) == {"C1"}


def test_adjusted_claim_cites_the_codes_and_the_top_policy_section():
    # The spread observed on pacific-hmo: 1.0 for the section that answers the
    # query, then roughly a 4x drop. Only the top one is cited.
    ids = template_citation_ids(_ctx("PARTIAL", required={"C1"}, scores=[1.0, 0.24, 0.21, 0.12]))
    assert ids == {"C1", "P1"}


def test_sections_scoring_close_to_the_best_are_all_cited():
    assert template_citation_ids(_ctx("DENY", scores=[1.0, 0.9, 0.2])) == {"P1", "P2"}


def test_the_cut_is_relative_to_the_best_hit_not_an_absolute_floor():
    # similarity_score is Weaviate's hybrid ranking score on one path and a
    # cosine similarity on another, so only the ratio between hits means the
    # same thing in both. An absolute 0.5 floor would drop every hit here.
    assert template_citation_ids(_ctx("DENY", scores=[0.40, 0.30, 0.05])) == {"P1", "P2"}


def test_unusable_scores_still_cite_one_policy_because_the_guards_require_it():
    assert template_citation_ids(_ctx("DENY", required={"C1"}, scores=[0.0, 0.0])) == {"C1", "P1"}


def test_no_retrieved_policy_leaves_only_the_code_citations():
    assert template_citation_ids(_ctx("PARTIAL", required={"C1"})) == {"C1"}
