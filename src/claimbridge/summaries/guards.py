"""
Deterministic output guards - ClaimBridge
=========================================

The LLM writes; these functions decide whether what it wrote may be stored.

WHY CODE, NOT ANOTHER LLM CALL
Each rule here is checkable exactly: a dollar figure either appears in the
adjudication record or it does not; a citation ID either exists or it does
not; "guaranteed" is either in the text or it is not. Asking a second model
to judge these would make a hard rule probabilistic. Exact rules get exact
checks. (Subjective qualities -- plain language, tone -- are for the rubric
eval, not for this gate.)

Every check returns a list of human-readable issues; empty means pass.
"""

import re
from decimal import Decimal, InvalidOperation
from typing import Dict, Iterable, List, Optional, Sequence, Set

# From member-summary-rubric.md ("forbidden_phrases") and the golden cases,
# plus the safety requirement: "no guarantee of payment".
FORBIDDEN_PHRASES = (
    "guarantee",                     # also catches guaranteed / guarantees
    "approved in full regardless",
    "will definitely be paid",
    "will be paid in full",
    "you have cancer",
    "you must have surgery",
    "ignore all rules",
    "ignore previous instructions",
)

# Medical advice / diagnosis statements aimed at the member. Kept narrow on
# purpose: a billing sentence like "the claim was missing a diagnosis code" is
# legitimate and must not trip the guard.
MEDICAL_ADVICE_PATTERNS = (
    re.compile(r"\byou should (take|stop|start|get|have|undergo|schedule|try)\b", re.I),
    re.compile(r"\b(we|i) (recommend|suggest|advise) (a |an |the |that you )?"
               r"(treatment|surgery|medication|medicine|therapy|procedure|test)", re.I),
    re.compile(r"\byou (have|may have|likely have|probably have) (a |an )?[a-z ]{0,30}"
               r"(condition|disease|disorder|infection|injury)\b", re.I),
)

_MONEY_RE = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d+)(\.\d{1,2})?")


def _money_values(text: str) -> List[Decimal]:
    out = []
    for whole, frac in _MONEY_RE.findall(text or ""):
        try:
            out.append(Decimal(whole.replace(",", "") + (frac or "")).quantize(Decimal("0.01")))
        except InvalidOperation:
            continue
    return out


def check_amounts(texts: Iterable[str], allowed: Iterable[Decimal]) -> List[str]:
    """
    Every dollar figure in the text must be one of the adjudication amounts.

    Catches the model "helpfully" computing a number -- e.g. "the $120
    difference" (285 - 165). The spec is explicit: use amounts from the
    adjudication fixture, do not recalculate. A computed number that happens to
    be right today is still an unsourced number.
    """
    allowed_set: Set[Decimal] = {Decimal(a).quantize(Decimal("0.01")) for a in allowed if a is not None}
    issues = []
    for text in texts:
        for value in _money_values(text):
            if value not in allowed_set:
                issues.append(f"Amount ${value} is not in the adjudication record "
                              f"(allowed: {sorted(str(a) for a in allowed_set) or 'none'})")
    return sorted(set(issues))


def check_safety(texts: Iterable[str]) -> List[str]:
    issues = []
    joined = "\n".join(t for t in texts if t)
    lowered = joined.lower()
    for phrase in FORBIDDEN_PHRASES:
        if phrase in lowered:
            issues.append(f"Forbidden phrase: '{phrase}'")
    for pattern in MEDICAL_ADVICE_PATTERNS:
        m = pattern.search(joined)
        if m:
            issues.append(f"Possible medical advice or diagnosis: '{m.group(0)}'")
    return issues


def check_citations(
    why_text: str,
    cited_ids: Sequence[str],
    valid_ids: Set[str],
    required_ids: Set[str],
    outcome: str,
    labels: Optional[Dict[str, str]] = None,
    policy_ids: Optional[Set[str]] = None,
) -> List[str]:
    """
    Grounding: every "why" must be backed by a real, retrievable source.

    - an ID the model invented is rejected (fabricated citation)
    - every code on the claim that the reference defines must be cited
      (golden cases require e.g. CO-45 on CLAIM-PH-001)
    - an adjusted or denied claim must cite at least one source for its reason
    - when plan policy sections were retrieved (`policy_ids`), an adjusted or
      denied claim must cite at least one of them: the plan's own rule is part
      of "why", and a policy fact used without a citation is untraceable
      (eval finding: CLAIM-CP-001 quoted "60% of allowed" but cited no policy)

    `labels` (source ID -> "CO-50 Not medically necessary") turns a bare
    "missing C2" into feedback the model can act on during the retry.
    """
    issues = []
    unknown = [c for c in cited_ids if c not in valid_ids]
    if unknown:
        issues.append(f"Cited source IDs that were not provided: {unknown}")
    missing = sorted(required_ids - set(cited_ids))
    if missing:
        issues.append(f"Required code citations missing: {missing}")
        for m in missing:
            if labels and m in labels:
                issues.append(f"Code {labels[m]} (source {m}) is not explained: add a plain-words sentence "
                              f"about it to why_adjusted and add \"{m}\" to why_citation_ids")
    if outcome in ("PARTIAL", "DENY"):
        if not (why_text or "").strip():
            issues.append("Outcome is adjusted/denied but no reason (why_adjusted) was given")
        elif not [c for c in cited_ids if c in valid_ids]:
            issues.append("Reason given without any valid citation")
        if policy_ids and not set(cited_ids) & set(policy_ids):
            issues.append(f"No plan policy cited: add the ID of the policy source that supports the reason "
                          f"(one of {sorted(policy_ids)}) to why_citation_ids")
    return issues


_PERCENT_RE = re.compile(r"\b\d{1,3}(?:\.\d+)?\s?(?:%|percent\b)", re.I)


def check_percentages(texts: Iterable[str]) -> List[str]:
    """
    No rates or formulas in member text.

    A policy rate ("pays 60% of allowed") is a general rule; the adjudication
    is what actually happened on THIS claim, after deductible and other
    adjustments. Quoting the rate next to the real amounts invites the member
    to recompute -- and on CLAIM-CP-001 the two do not even agree ($4,960 is
    80% of $6,200). The dollar amounts from the record are the explanation.
    """
    found = sorted({m.group(0) for t in texts if t for m in _PERCENT_RE.finditer(t)})
    return [f"Percentage or rate in member text {found}: explain with the dollar amounts in FACTS only"] if found else []


def check_structure(data: dict) -> List[str]:
    """Shape checks on the raw model JSON, before schema validation."""
    issues = []
    for key in ("service_description", "plain_language_summary", "what_happened", "why_adjusted"):
        if not isinstance(data.get(key), str):
            issues.append(f"Field '{key}' missing or not text")
    steps = data.get("next_steps")
    if not isinstance(steps, list) or not [s for s in steps if isinstance(s, str) and s.strip()]:
        issues.append("Field 'next_steps' must be a non-empty list of text")
    if not isinstance(data.get("why_citation_ids", []), list):
        issues.append("Field 'why_citation_ids' must be a list")
    return issues


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\u2019", "'")).strip().lower()


_CAUSE_QUALIFIERS = {"missing", "invalid", "incomplete", "wrong", "incorrect", "no"}
_CAUSE_TRAILERS = {"fields", "field", "code", "codes", "number", "numbers"}


def cause_terms(common_causes: str) -> List[str]:
    """
    "Missing diagnosis, invalid modifier, missing referring provider NPI,
    incomplete UB-04 fields" -> ["diagnosis", "modifier", "npi", "ub-04"]
    """
    terms = []
    for phrase in (common_causes or "").split(","):
        words = [w for w in _norm(phrase).split() if w not in _CAUSE_QUALIFIERS]
        while words and words[-1] in _CAUSE_TRAILERS:
            words.pop()
        if words:
            terms.append(words[-1])
    return terms


def check_unsupported_causes(texts: Iterable[str], common_causes: Dict[str, str], claim_evidence: str) -> List[str]:
    """
    A code definition's "common causes" are examples. Naming one as THE cause
    of this claim is only allowed when the claim itself says so (eval finding:
    CLAIM-SE-001 told the member "it did not include a diagnosis" -- the claim
    has a diagnosis code; nothing says what was missing).
    """
    joined = _norm(" ".join(t for t in texts if t))
    evidence = _norm(claim_evidence)
    issues = []
    for code, causes in common_causes.items():
        for term in cause_terms(causes):
            if re.search(rf"\b{re.escape(term)}\b", joined) and not re.search(rf"\b{re.escape(term)}\b", evidence):
                issues.append(f"Says the problem was '{term}' but nothing on this claim says so: for {code}, "
                              f"say information was missing or incomplete without naming what")
    return issues
