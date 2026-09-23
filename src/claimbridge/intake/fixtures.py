"""
Fixture -> submission payload - ClaimBridge
===========================================

Turns a claim from resources/sample-claims.md into the JSON a client would
POST to /v1/tenants/{tenant_id}/claims. Used by the demo script and by the
golden eval runner for "full_pipeline" cases, so demos submit exactly the
provided fixtures rather than hand-typed copies.

Fixture cells carry descriptions ("72148 (MRI lumbar spine)", "11 (Office)");
only the code is sent. A field the fixture marks **missing** is simply left
out -- completeness validation is what must notice it.
"""

from typing import Any, Dict, Optional

from src.claimbridge.knowledge import parse_sample_claims, parse_tenant_catalog, resources_dir


def _code(value: Optional[str]) -> Optional[str]:
    return value.split()[0].strip() if value else None


def load_fixture(claim_id: str):
    root = resources_dir()
    tenants = parse_tenant_catalog(root / "tenant-catalog.md")
    for f in parse_sample_claims(root / "sample-claims.md", tenants)["claims"]:
        if f.claim_id == claim_id:
            return f
    raise KeyError(f"No fixture {claim_id} in sample-claims.md")


def fixture_payload(claim_id: str) -> Dict[str, Any]:
    f = load_fixture(claim_id)
    d = f.claim_data
    payload: Dict[str, Any] = {
        "claim_id": f.claim_id,
        "claim_type": f.claim_type,
        "member_id": f.member_id,
        "date_of_service": f.date_of_service.isoformat() if f.date_of_service else None,
        "billed_amount": str(f.amounts.get("billed_amount")) if f.amounts.get("billed_amount") else None,
        "provider_name": f.provider_name,
        "place_of_service": _code(d.get("place_of_service")),
        "icd10": [_code(d["icd10"])] if d.get("icd10") else [],
        "notes": d.get("notes"),
        "cpt": _code(d.get("cpt")),
        "type_of_bill": _code(d.get("type_of_bill")),
        "revenue_code": _code(d.get("revenue_code")),
        "cpt_hcpcs": _code(d.get("cpt_hcpcs")),
        "ndc": _code(d.get("ndc")),
        "quantity": int(d["quantity"]) if d.get("quantity") else None,
        "days_supply": int(d["days_supply"]) if d.get("days_supply") else None,
        "pharmacy_npi": _code(d.get("pharmacy_npi")),
    }
    if f.is_adjudicated:
        payload["adjudication"] = {
            "outcome": f.outcome,
            "allowed_amount": str(f.amounts["allowed_amount"]) if f.amounts.get("allowed_amount") is not None else None,
            "plan_paid": str(f.amounts["plan_paid"]) if f.amounts.get("plan_paid") is not None else None,
            "patient_responsibility": (str(f.amounts["patient_responsibility"])
                                       if f.amounts.get("patient_responsibility") is not None else None),
            "carc_codes": f.carc_codes,
            "rarc_codes": f.rarc_codes,
        }
    return {k: v for k, v in payload.items() if v not in (None, [], "")}


# ---------------------------------------------------------------------------
# Adversarial fixtures (sample-claims.md "Adversarial / safety fixtures")
# ---------------------------------------------------------------------------

import re as _re

# The adversarial sections give only the attack, not a full claim. Each is
# ADAPTED onto a real fixture that would otherwise pass cleanly, so the test
# proves the attack is what changes the result:
#   CLAIM-ADV-001  injected clinical note  on CLAIM-PH-003 (clean APPROVE)
ADVERSARIAL_BASE = {"CLAIM-ADV-001": "CLAIM-PH-003"}


def adversarial_payload(claim_id: str) -> Dict[str, Any]:
    if claim_id not in ADVERSARIAL_BASE:
        raise KeyError(f"No adversarial adaptation defined for {claim_id}")
    raw = (resources_dir() / "sample-claims.md").read_text(encoding="utf-8")
    section = raw.split(f"### {claim_id}", 1)[1].split("\n### ", 1)[0]
    m = _re.search(r'contains:\s*"([^"]+)"', section)
    if not m:
        raise KeyError(f"Could not read the injected text for {claim_id}")
    payload = fixture_payload(ADVERSARIAL_BASE[claim_id])
    payload["claim_id"] = claim_id
    payload["notes"] = m.group(1)
    return payload


def any_payload(claim_id: str) -> Dict[str, Any]:
    return adversarial_payload(claim_id) if claim_id in ADVERSARIAL_BASE else fixture_payload(claim_id)
