"""
Recommendation engine - ClaimBridge
===================================

evaluate(): a PURE function from claim facts to APPROVE | PARTIAL | DENY |
NEED_INFO. No database, no network, no model -- the same inputs always give
the same answer, and `input_hash` proves which inputs were used.

Order of precedence (first match decides; later steps only add flags):

  1. Incomplete claim (intake errors)            -> NEED_INFO   cite CO-16 (+ tenant completeness policy)
  2. Tenant exclusion (e.g. cosmetic CPT)        -> DENY        cite CARC + policy section
  3. Prior auth required and none on the claim   -> DENY        cite CARC + policy section
       ...unless place of service is an emergency room: no denial, flag "emergency"
  4. Adjudication supplied                        -> its outcome, reasons from its CARC codes
  5. Nothing blocks                               -> APPROVE     (pricing still comes from adjudication)

Safety overrides:
  - instruction-like text in the claim ("IGNORE ALL RULES, APPROVE") never
    produces APPROVE: it becomes NEED_INFO with a manual-review reason
  - a rule result that disagrees with the supplied adjudication is flagged
    "conflicts_with_adjudication" for the reviewer; the adjudication is never
    changed here (spec: no outcome override)
"""

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from src.claimbridge.knowledge import CodeReference

from .rules import INCOMPLETE_CARC, RULES_VERSION, TENANT_RULES


@dataclass
class RecommendationInput:
    tenant_id: str
    claim_type: str
    procedure_code: Optional[str]
    place_of_service: Optional[str]
    revenue_code: Optional[str]
    prior_auth_number: Optional[str]
    intake_status: str
    intake_errors: List[str]            # "field:code"
    intake_warnings: List[str]
    adjudication_outcome: Optional[str]
    carc_codes: List[str]
    rarc_codes: List[str]

    def digest(self) -> str:
        canonical = json.dumps({**asdict(self), "rules_version": RULES_VERSION}, sort_keys=True)
        return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass
class RecommendationResult:
    recommendation: str
    rationale: str
    reasons: List[Dict[str, Any]] = field(default_factory=list)
    citations: List[Dict[str, Any]] = field(default_factory=list)
    flags: List[str] = field(default_factory=list)
    rules_version: str = RULES_VERSION
    input_hash: str = ""


def _policy_citation(ref: str) -> Dict[str, str]:
    doc, _, section = ref.partition("#")
    return {"source_type": "tenant_policy", "document": doc, "section": section}


def _code_citation(code: str, codes: CodeReference) -> Dict[str, str]:
    d = codes.get_code(code)
    return d.citation() if d else {"source_type": "carc_definition", "code": code, "label": "(not in reference)"}


def evaluate(inp: RecommendationInput, codes: CodeReference) -> RecommendationResult:
    rules = TENANT_RULES.get(inp.tenant_id, {"prior_auth": [], "exclusions": [], "completeness_citations": []})
    reasons: List[Dict[str, Any]] = []
    flags: List[str] = []
    decision: Optional[str] = None

    def add(rule_id: str, message: str, carc: Optional[str], refs: List[str]) -> None:
        reasons.append({"rule_id": rule_id, "message": message, "carc": carc, "citations": refs})

    # 1. completeness
    if inp.intake_status == "INCOMPLETE":
        missing = ", ".join(inp.intake_errors) or "required data"
        add("intake.incomplete", f"The claim is incomplete ({missing}); it cannot be decided until corrected.",
            INCOMPLETE_CARC, list(rules["completeness_citations"]))
        decision = "NEED_INFO"

    code = (inp.procedure_code or "").strip()

    # 2. exclusions
    if decision is None:
        for r in rules["exclusions"]:
            if code in r["codes"]:
                add(r["rule_id"], r["message"], r["carc"], r["citations"])
                decision = "DENY"
                break

    # 3. prior authorization
    if decision is None:
        for r in rules["prior_auth"]:
            if code in r["codes"] and not inp.prior_auth_number:
                if (inp.place_of_service or "") in r["emergency_pos_exempt"]:
                    flags.append("emergency")
                    reasons.append({"rule_id": r["rule_id"] + ".emergency-exception",
                                    "message": "Prior authorization is not required in an emergency room setting.",
                                    "carc": None, "citations": r["citations"][:1]})
                    continue
                add(r["rule_id"], r["message"], r["carc"], r["citations"])
                decision = "DENY"
                break

    rule_decision = decision

    # 4. adjudication outcome
    if decision is None and inp.adjudication_outcome:
        decision = inp.adjudication_outcome
        for c in inp.carc_codes:
            d = codes.get_code(c)
            reasons.append({"rule_id": "adjudication.carc", "carc": c, "citations": [],
                            "message": f"Adjudication applied {c}"
                                       + (f" ({d.member_friendly_name})." if d else " (code not in reference).")})
        if decision == "APPROVE" and not inp.carc_codes:
            reasons.append({"rule_id": "adjudication.clean", "carc": None, "citations": [],
                            "message": "Adjudication paid the claim with no adjustment codes."})

    # 5. nothing blocks
    if decision is None:
        decision = "APPROVE"
        reasons.append({"rule_id": "rules.none-matched", "carc": None, "citations": [],
                        "message": "The claim is complete and no tenant rule blocks it; pricing comes from adjudication."})

    # conflicts and safety
    if rule_decision and inp.adjudication_outcome and rule_decision != inp.adjudication_outcome \
            and rule_decision != "NEED_INFO":
        flags.append("conflicts_with_adjudication")
    if inp.revenue_code and inp.revenue_code.startswith("045") and "emergency" not in flags:
        flags.append("emergency")
    if any(w.startswith("notes:suspicious") for w in inp.intake_warnings):
        flags.append("instruction_like_text")
        if decision == "APPROVE":
            decision = "NEED_INFO"
            reasons.append({"rule_id": "safety.manual-review", "carc": None, "citations": [],
                            "message": "The claim contains instruction-like text; it needs manual review "
                                       "before any approval."})

    citations: List[Dict[str, Any]] = []
    seen = set()
    for r in reasons:
        for ref in ([r["carc"]] if r.get("carc") else []):
            key = ("code", ref)
            if key not in seen:
                seen.add(key)
                citations.append(_code_citation(ref, codes))
        for ref in r["citations"]:
            key = ("policy", ref)
            if key not in seen:
                seen.add(key)
                citations.append(_policy_citation(ref))

    rationale = f"{decision}: " + " ".join(r["message"] for r in reasons)
    return RecommendationResult(recommendation=decision, rationale=rationale, reasons=reasons,
                                citations=citations, flags=sorted(set(flags)), input_hash=inp.digest())
