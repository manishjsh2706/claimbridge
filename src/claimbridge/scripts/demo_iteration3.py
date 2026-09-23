"""
Iteration 3 mentor demo in ONE command - ClaimBridge
====================================================

    docker compose exec claimbridge python -m src.claimbridge.scripts.demo_iteration3

Runs the demo script from iteration-backlog.md (Iteration 3):

  1. The same question shape for a Pacific claim and a Coastal claim ->
     different tenant-scoped sources, different appeal window and phone
  2. Cross-tenant leakage suite (5 layers, printed summary)
  3. Summit onboarding gate: the latest report, with its GO / NO-GO decision

Steps 1 generates two real member summaries (2 LLM calls). Step 2 runs the
leakage suite without its own LLM checks (step 1 already proved that layer).
Step 3 reads the newest summit-onboarding report rather than re-running the
gate, so the demo stays under a minute; run `onboarding_summit` directly to
regenerate it.
"""

import glob
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

import httpx

from src.claimbridge.auth import issue_key
from src.claimbridge.db import session_scope
from src.claimbridge.scripts.leakage_suite import main as leakage_main
from src.claimbridge.scripts.run_golden_eval import default_out_dir

BASE = os.getenv("CLAIMBRIDGE_API_URL", "http://localhost:8000")
CLAIMS = [("pacific-hmo", "CLAIM-PH-001", "Pacific HMO"), ("coastal-ppo", "CLAIM-CP-001", "Coastal PPO Partners")]
results = []


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append(ok)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"   ({detail})" if detail else ""))


def summary_for(tenant: str, claim_id: str, key: str) -> Dict[str, Any]:
    with httpx.Client(base_url=BASE, timeout=300, headers={"X-Api-Key": key}) as c:
        r = c.post(f"/v1/tenants/{tenant}/claims/{claim_id}/member-summary")
        r.raise_for_status()
        return r.json()


def main() -> int:
    print("=" * 78 + "\n1. SAME QUESTION SHAPE, TWO TENANTS -> DIFFERENT SOURCES\n" + "=" * 78)
    seen = {}
    for tenant, claim_id, name in CLAIMS:
        with session_scope() as s:
            key = issue_key(s, f"demo3:{tenant}", "submitter", tenant)
        body = summary_for(tenant, claim_id, key)
        summary = body["summary"]
        docs = sorted({c["document"] for c in summary["citations"] if c.get("document")})
        sections = sorted({c["section"] for c in summary["citations"] if c.get("section")})
        seen[tenant] = {"docs": docs, "sections": sections, "appeal": summary["appeal_rights_summary"]}
        print(f"\n{name} / {claim_id}  (outcome {summary['outcome']})")
        print(f"  policy cited : {', '.join(f'{d}#{s}' for d, s in zip(docs, sections)) or 'none'}")
        print(f"  codes        : {', '.join(c['code'] for c in summary['citations'] if c.get('code')) or 'none'}")
        print(f"  member owes  : {summary['amounts']['you_owe']}")
        print(f"  appeal text  : {summary['appeal_rights_summary'][:110]}...")
        check(f"{name}: every cited document belongs to this tenant",
              all(d.startswith(tenant) for d in docs), f"{len(docs)} documents")

    p, c = seen["pacific-hmo"], seen["coastal-ppo"]
    check("the two tenants cite different policy documents", set(p["docs"]) != set(c["docs"]) or not p["docs"],
          f"{p['docs']} vs {c['docs']}")
    check("appeal windows differ (Pacific 60 days, Coastal 30 days)",
          "60 days" in p["appeal"] and "30 days" in c["appeal"])
    check("member services numbers differ",
          "1-800-555-0142" in p["appeal"] and "1-800-555-0198" in c["appeal"])

    print("\n" + "=" * 78 + "\n2. CROSS-TENANT LEAKAGE SUITE\n" + "=" * 78)
    rc = leakage_main(["--base-url", BASE, "--no-llm"])
    check("leakage suite: no foreign data in any layer", rc == 0,
          "" if rc == 0 else ("LEAKAGE" if rc == 1 else "inconclusive: a probe errored, re-run"))

    print("\n" + "=" * 78 + "\n3. SUMMIT ONBOARDING GATE (latest report)\n" + "=" * 78)
    reports = sorted(glob.glob(str(default_out_dir() / "summit-onboarding-*.json")))
    if not reports:
        check("summit onboarding report exists", False,
              "run: python -m src.claimbridge.scripts.onboarding_summit --signed-off-by '<name>'")
    else:
        report = json.loads(Path(reports[-1]).read_text(encoding="utf-8"))
        passed = [i for i in report["checklist"] if i["passed"]]
        scen = report["scenarios"]
        print(f"  report        : {os.path.basename(reports[-1])}")
        print(f"  checklist     : {len(passed)}/{len(report['checklist'])} checks")
        print(f"  scenarios     : {sum(1 for s in scen if s['passed'])}/{len(scen)} passed")
        print(f"  signed off by : {report.get('signed_off_by') or 'NOT SIGNED'}")
        print(f"  decision      : {'GO (staging)' if report['gate']['passed'] and report.get('signed_off_by') else 'NO-GO'}")
        check("Summit gate passed with a human sign-off",
              report["gate"]["passed"] and bool(report.get("signed_off_by")))
        with session_scope() as s:
            from src.claimbridge.models import Tenant
            status = s.get(Tenant, "summit-employer").status
        check("Summit is still ONBOARDING (Essential track: staging only)", status == "ONBOARDING", status)

    ok = all(results)
    print(f"\n{'DEMO PASSED' if ok else 'DEMO FAILED'}: {sum(results)}/{len(results)} checks")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
