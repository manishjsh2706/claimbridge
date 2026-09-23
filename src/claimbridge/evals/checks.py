"""
Deterministic golden-case checks - ClaimBridge
==============================================

Everything a golden case states exactly is checked exactly: no model involved,
same verdict on every run. Subjective quality (tone, plain language) is left to
the judge.

Each check is one CheckResult so the report shows WHICH expectation failed,
not just that the case failed.
"""

import json
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Optional

from src.claimbridge.knowledge import normalize_code

from .golden import GoldenCase

MEMBER_TEXT_FIELDS = ("service_description", "plain_language_summary", "what_happened",
                      "why_adjusted", "amounts_note", "appeal_rights_summary")


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def member_visible_text(summary: Dict[str, Any]) -> str:
    """Everything the member would read, in one string."""
    parts = [str(summary.get(f) or "") for f in MEMBER_TEXT_FIELDS]
    parts += [str(s) for s in summary.get("next_steps") or []]
    parts += [f"{e.get('plain_name', '')}: {e.get('meaning', '')}" for e in summary.get("code_explanations") or []]
    return "\n".join(parts)


def _missing(expected: Iterable[str], present: Iterable[str]) -> List[str]:
    have = set(present)
    return [e for e in expected if e not in have]


def run_checks(case: GoldenCase, http_status: int, body: Optional[Dict[str, Any]],
               tenant: Optional[Dict[str, Any]]) -> List[CheckResult]:
    """
    Args:
        http_status: status code the API returned
        body:        parsed MemberSummaryResponse (or error body)
        tenant:      tenant configuration from the database (appeal window, phone)
    """
    results: List[CheckResult] = []

    ok = http_status == 201 and isinstance(body, dict) and "summary" in body
    results.append(CheckResult("api_created", ok,
                               "" if ok else f"HTTP {http_status}: {str(body)[:200]}"))
    if not ok:
        return results   # nothing else can be evaluated

    summary = body["summary"]
    citations = summary.get("citations") or []
    text = member_visible_text(summary)
    lowered = text.lower()

    exp = case.get("expected_outcome")
    if exp:
        results.append(CheckResult("expected_outcome", summary.get("outcome") == exp,
                                   f"expected {exp}, got {summary.get('outcome')}"))

    # DRAFT from /member-summary, PENDING_REVIEW from the pipeline; never
    # PUBLISHED without a human (auto-publish is off for every tenant).
    results.append(CheckResult("not_published_without_review", summary.get("status") in ("DRAFT", "PENDING_REVIEW"),
                               f"status is {summary.get('status')}"))

    validation = body.get("validation") or {}
    results.append(CheckResult("output_guards_passed", bool(validation.get("passed")),
                               "; ".join(validation.get("issues") or [])))

    req = [normalize_code(c) for c in case.get("required_citations") or []]
    if req:
        miss = _missing(req, [normalize_code(c.get("code") or "") for c in citations if c.get("code")])
        results.append(CheckResult("required_citations", not miss, f"missing {miss}" if miss else ""))

    req_types = case.get("required_citation_types") or []
    if req_types:
        miss = _missing(req_types, [c.get("source_type") for c in citations])
        results.append(CheckResult("required_citation_types", not miss, f"missing {miss}" if miss else ""))

    req_sections = case.get("required_policy_sections") or []
    if req_sections:
        miss = _missing(req_sections, [c.get("section") for c in citations if c.get("section")])
        results.append(CheckResult("required_policy_sections", not miss,
                                   f"missing {miss}; cited "
                                   f"{[c.get('section') for c in citations if c.get('section')]}" if miss else ""))

    phrases = case.get("forbidden_phrases") or []
    if phrases:
        found = [p for p in phrases if p.lower() in lowered]
        results.append(CheckResult("no_forbidden_phrases", not found, f"found {found}" if found else ""))

    prefixes = [p.lower() for p in case.get("forbidden_doc_prefixes") or []]
    if prefixes:
        # Both what was CITED and what was RETRIEVED: a foreign section that
        # reached the prompt is a leak even if the model happened not to cite it.
        docs = [str(c.get("document") or "") for c in citations if c.get("document")]
        docs += [str(s).split("#", 1)[0] for s in body.get("retrieved_sections") or []]
        leaked = sorted({d for d in docs if any(d.lower().startswith(p) for p in prefixes)})
        results.append(CheckResult("no_foreign_tenant_sources", not leaked,
                                   f"foreign sources {leaked}" if leaked else ""))

    if tenant:
        problems = []
        appeal = summary.get("appeal_rights_summary") or ""
        phone = tenant.get("member_services_phone")
        if phone and phone not in appeal:
            problems.append(f"phone {phone} not in appeal rights")
        if summary.get("outcome") != "APPROVE":
            window = f"{tenant.get('appeal_window_days')} days"
            if window not in appeal:
                problems.append(f"appeal window '{window}' not in appeal rights")
        results.append(CheckResult("tenant_contact_and_appeal_window", not problems, "; ".join(problems)))

    return results


# ---------------------------------------------------------------------------
# Provider notices (Iteration 2)
# ---------------------------------------------------------------------------

def provider_visible_text(notice: Dict[str, Any]) -> str:
    parts = [str(notice.get("technical_summary") or ""), str(notice.get("resubmission_instructions") or ""),
             str(notice.get("appeal_path") or "")]
    parts += [str(a) for a in notice.get("correction_actions") or []]
    return "\n".join(parts)


def run_provider_checks(case: GoldenCase, http_status: int, body: Optional[Dict[str, Any]]) -> List[CheckResult]:
    """body = ProviderNoticeResponse ({"notice": ..., "validation": ...})."""
    results: List[CheckResult] = []
    ok = http_status in (200, 201) and isinstance(body, dict) and "notice" in body
    results.append(CheckResult("api_created", ok, "" if ok else f"HTTP {http_status}: {str(body)[:200]}"))
    if not ok:
        return results
    notice = body["notice"]
    text = provider_visible_text(notice)
    blob = json.dumps(notice)

    exp = case.get("expected_outcome")
    if exp:
        results.append(CheckResult("expected_outcome", notice.get("outcome") == exp,
                                   f"expected {exp}, got {notice.get('outcome')}"))
    results.append(CheckResult("output_guards_passed", bool((body.get("validation") or {}).get("passed")),
                               "; ".join((body.get("validation") or {}).get("issues") or [])))

    codes_on_notice = set((notice.get("codes") or {}).get("carc", []) + (notice.get("codes") or {}).get("rarc", []))
    req = [normalize_code(c) for c in case.get("required_citations") or []]
    if req:
        cited = {normalize_code(c.get("code") or "") for c in notice.get("code_citations") or []}
        miss = [c for c in req if c not in cited or c not in codes_on_notice]
        results.append(CheckResult("required_citations", not miss, f"missing {miss}" if miss else ""))

    must_codes = case.get("must_include_codes") or []
    if must_codes:
        miss = [c for c in must_codes if c not in blob]
        results.append(CheckResult("must_include_codes", not miss, f"missing {miss}" if miss else ""))

    actions = case.get("must_include_provider_actions") or []
    if actions:
        low = text.lower()
        miss = [a for a in actions if a.lower() not in low]
        results.append(CheckResult("must_include_provider_actions", not miss, f"missing {miss}" if miss else ""))

    fields = case.get("must_include_fields") or []
    if fields:
        ref = notice.get("billing_codes_reference") or {}
        miss = [f for f in fields if not ref.get(f)]
        results.append(CheckResult("must_include_fields", not miss, f"missing {miss}" if miss else ""))

    sections = case.get("required_policy_sections") or []
    if sections:
        cited = [c.get("section") for c in notice.get("policy_citations") or []]
        miss = [s for s in sections if s not in cited]
        results.append(CheckResult("required_policy_sections", not miss,
                                   f"missing {miss}; cited {cited}" if miss else ""))

    phrases = case.get("forbidden_phrases") or []
    if phrases:
        found = [p for p in phrases if p.lower() in text.lower()]
        results.append(CheckResult("no_forbidden_phrases", not found, f"found {found}" if found else ""))

    prefixes = [p.lower() for p in case.get("forbidden_doc_prefixes") or []]
    if prefixes:
        docs = [str(c.get("document") or "") for c in notice.get("policy_citations") or []]
        docs += [str(s).split("#", 1)[0] for s in body.get("retrieved_sections") or []]
        leaked = sorted({d for d in docs if any(d.lower().startswith(p) for p in prefixes)})
        results.append(CheckResult("no_foreign_tenant_sources", not leaked,
                                   f"foreign sources {leaked}" if leaked else ""))

    if case.get("expect_validation_errors"):
        errs = notice.get("validation_errors") or []
        results.append(CheckResult("validation_errors_reported", bool(errs), "no validation errors on notice"))
        need = case.get("required_fields_missing") or []
        got = {e.get("field") for e in errs}
        miss = [f for f in need if f not in got]
        results.append(CheckResult("required_fields_missing", not miss, f"not reported: {miss}" if miss else ""))
    return results


# ---------------------------------------------------------------------------
# Full pipeline (submit -> recommend -> drafts)
# ---------------------------------------------------------------------------

def run_pipeline_checks(case: GoldenCase, submit_status: int, submit_body: Any,
                        drafts_status: int, drafts_body: Any) -> List[CheckResult]:
    results: List[CheckResult] = []
    already = submit_status == 409 and "already exists" in json.dumps(submit_body)
    results.append(CheckResult("claim_submitted", submit_status in (200, 201) or already,
                               f"HTTP {submit_status}: {str(submit_body)[:200]}"))
    ok = drafts_status == 201 and isinstance(drafts_body, dict)
    results.append(CheckResult("pipeline_ran", ok, "" if ok else f"HTTP {drafts_status}: {str(drafts_body)[:200]}"))
    if not ok:
        return results
    rec = (drafts_body.get("recommendation") or {}).get("recommendation")
    forbidden = case.get("forbidden_recommendation")
    if forbidden:
        results.append(CheckResult("forbidden_recommendation", rec != forbidden,
                                   f"recommendation was {rec}"))
    if case.get("expect_validation_errors") and isinstance(submit_body, dict) and "intake_status" in submit_body:
        results.append(CheckResult("intake_incomplete", submit_body.get("intake_status") == "INCOMPLETE",
                                   f"intake_status {submit_body.get('intake_status')}"))
    return results
