"""
Iteration 2 mentor demo in ONE command - ClaimBridge
====================================================

    docker compose exec claimbridge python -m src.claimbridge.scripts.demo_iteration2

Runs the demo script from iteration-backlog.md against the running API and
prints each step with PASS/FAIL:

  1. Submit CLAIM-PH-002 (incomplete)  -> validation errors, NEED_INFO
  2. Submit CLAIM-PH-003 (clean)       -> APPROVE + drafts
  3. Submit CLAIM-PH-004 (prior auth)  -> DENY + member & provider drafts
     -> publish without approval (must fail) -> approve -> publish
  4. RBAC: the submitter cannot approve
  5. Audit trail for CLAIM-PH-004

Two principals are used, exactly like real users: "cli:adjuster-demo"
(submitter) and "cli:reviewer-demo" (reviewer). Keys are issued for this run
only (the script runs inside the API container with database access).
Re-running is safe: submissions replay (Idempotency-Key), new drafts supersede
old ones in the review queue.
"""

import os
import sys

import httpx

from src.claimbridge.auth import issue_key
from src.claimbridge.db import session_scope
from src.claimbridge.intake import fixture_payload

BASE = os.getenv("CLAIMBRIDGE_API_URL", "http://localhost:8000")
T = "pacific-hmo"
results = []


def step(title):
    print(f"\n=== {title} ===")


def check(label, ok, detail=""):
    results.append(ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"   ({detail})" if detail else ""))


def main() -> int:
    with session_scope() as s:
        k_sub = issue_key(s, "cli:adjuster-demo", "submitter", T)
        k_rev = issue_key(s, "cli:reviewer-demo", "reviewer", T)
    sub = httpx.Client(base_url=f"{BASE}/v1/tenants/{T}", timeout=300, headers={"X-Api-Key": k_sub})
    rev = httpx.Client(base_url=f"{BASE}/v1/tenants/{T}", timeout=60, headers={"X-Api-Key": k_rev})

    def submit(cid):
        """Submit, or -- if the claim was already submitted under another key -- read it back."""
        r = sub.post("/claims", json=fixture_payload(cid), headers={"Idempotency-Key": f"demo-{cid.lower()}"})
        j = r.json()
        if r.status_code == 409 and "already exists" in str(j):
            d = sub.get(f"/claims/{cid}").json()
            issues = d.get("validation_issues") or []
            j = {"intake_status": d.get("intake_status"), "replayed": True, "recommendation": d.get("recommendation"),
                 "validation": {"errors": [i for i in issues if i["severity"] == "error"]}}
            return 200, j
        return r.status_code, j

    step("1. Submit CLAIM-PH-002 (incomplete)")
    code, j = submit("CLAIM-PH-002")
    errs = [e["field"] for e in j.get("validation", {}).get("errors", [])]
    check("accepted and stored", code in (200, 201), f"HTTP {code}{', replay' if j.get('replayed') else ''}")
    check("intake INCOMPLETE, icd10 missing", j.get("intake_status") == "INCOMPLETE" and errs == ["icd10"], errs)
    rec = (j.get("recommendation") or {}).get("recommendation") or \
        sub.post("/claims/CLAIM-PH-002/recommendation").json().get("recommendation")
    check("recommendation NEED_INFO", rec == "NEED_INFO", rec)

    step("2. Submit CLAIM-PH-003 (clean) and generate drafts")
    code, j = submit("CLAIM-PH-003")
    check("recommendation APPROVE", (j.get("recommendation") or {}).get("recommendation") == "APPROVE")
    d = sub.post("/claims/CLAIM-PH-003/drafts").json()
    check("member draft waiting for review (auto-publish is off)",
          d["routing"].get("member", {}).get("status") == "PENDING_REVIEW", d["routing"].get("member"))

    step("3. Submit CLAIM-PH-004 (no prior auth) -> DENY -> review -> publish")
    code, j = submit("CLAIM-PH-004")
    r = j.get("recommendation") or {}
    check("recommendation DENY with CO-197 + policy citation",
          r.get("recommendation") == "DENY" and any(c.get("code") == "CO-197" for c in r.get("citations", [])))
    d = sub.post("/claims/CLAIM-PH-004/drafts").json()
    mid, pid = d["routing"]["member"]["communication_id"], d["routing"]["provider"]["communication_id"]
    print(f"  member summary  = communication {mid}  ({d['member']['validation']['generation_mode']})")
    print(f"  provider notice = communication {pid}  ({d['provider']['validation']['generation_mode']})")
    print(f"  member says     : {d['member']['summary']['why_adjusted'][:160]}...")
    print(f"  provider actions: {d['provider']['notice']['correction_actions'][:2]}")
    x = rev.post(f"/communications/{mid}/publish")
    check("publish WITHOUT approval is refused", x.status_code == 409, f"HTTP {x.status_code}")
    x = sub.post(f"/communications/{mid}/approve")
    check("RBAC: the submitter cannot approve", x.status_code == 403, f"HTTP {x.status_code}")
    for cid, who in ((mid, "member"), (pid, "provider")):
        a = rev.post(f"/communications/{cid}/approve", json={"note": "checked codes, amounts and citations"})
        p = rev.post(f"/communications/{cid}/publish")
        check(f"{who}: reviewer approves, then publishes",
              a.status_code == 200 and p.status_code == 200 and p.json().get("status") == "PUBLISHED",
              f"approved_by={a.json().get('approved_by')}, status={p.json().get('status')}")

    step("4. Audit trail for CLAIM-PH-004 (latest 8 events)")
    ev = rev.get("/claims/CLAIM-PH-004/audit-events").json()
    for e in ev[-8:]:
        print(f"  {e['created_at'][:19]}  {e['action']:34} {e['actor']}")
    actions = {e["action"] for e in ev}
    check("trail shows decision, AI drafts, refused publish and human approval",
          {"RECOMMENDATION_CREATED", "MEMBER_SUMMARY_GENERATED", "PROVIDER_NOTICE_GENERATED",
           "COMMUNICATION_TRANSITION_REJECTED", "COMMUNICATION_STATUS_CHANGED"} <= actions)

    ok = all(results)
    print(f"\n{'DEMO PASSED' if ok else 'DEMO FAILED'}: {sum(results)}/{len(results)} checks")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
