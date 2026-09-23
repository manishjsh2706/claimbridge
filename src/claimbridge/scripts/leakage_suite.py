"""
Cross-tenant leakage suite - ClaimBridge (Iteration 3)
======================================================

    docker compose exec claimbridge python -m src.claimbridge.scripts.leakage_suite
    ... --no-llm     skip the generated-summary checks (no OpenAI cost)

"Tenants isolated, so Pacific members never see Coastal policy language"
(iteration-backlog I3). Isolation is claimed in four places, so it is tested in
all four -- a pass in one layer does not prove the others:

  L1 vector store   every tenant's policy search returns only that tenant's
                    documents, probed with queries written to attract another
                    tenant's content (OON balance billing, cosmetic exclusion,
                    prior auth)
  L2 API            another tenant's claim is 404 (never 403 -- a 403 would
                    confirm the claim exists), and the probe is audited
  L3 API keys       a key scoped to one tenant gets 403 on another tenant's
                    routes; the review queue shows only its own drafts
  L4 database       the composite foreign key (tenant_id, claim_id) refuses a
                    communication that pairs one tenant with another's claim
  L5 generated text every citation and every retrieved section in a real
                    member summary belongs to the tenant that asked (LLM call;
                    skip with --no-llm)

Any foreign document is a FAIL. The report is written to eval-reports/.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import httpx
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from src.claimbridge.auth import issue_key
from src.claimbridge.db import session_scope
from src.claimbridge.scripts.run_golden_eval import default_out_dir

# Queries deliberately phrased in another tenant's vocabulary.
PROBES = [
    "out-of-network balance billing for surgery",          # Coastal wording
    "prior authorization required for MRI imaging",        # Pacific wording
    "cosmetic dermatology exclusion",                      # Coastal wording
    "immunization coverage and claim completeness",        # Summit wording
    "member appeal deadline and member services phone",    # every tenant
]
DEMO_CLAIM = {"pacific-hmo": "CLAIM-PH-001", "coastal-ppo": "CLAIM-CP-001", "summit-employer": "CLAIM-SE-001"}


class Suite:
    def __init__(self, base_url: str):
        self.base_url = base_url
        self.results: List[Dict[str, Any]] = []

    def check(self, layer: str, name: str, passed: bool, detail: str = "", kind: str = "leak") -> None:
        """
        kind="leak"  a failure means tenant data crossed a boundary
        kind="error" the probe itself failed (outage, timeout): the check is
                     INCONCLUSIVE, not proof of leakage. Calling an outage a
                     breach is its own kind of wrong (seen 2026-09-23).
        """
        self.results.append({"layer": layer, "check": name, "passed": passed, "detail": detail,
                             "kind": kind if not passed else "ok"})
        label = "PASS" if passed else ("FAIL" if kind == "leak" else "ERROR")
        print(f"  {label}  [{layer}] {name}" + (f"   <- {detail}" if not passed else ""))

    # -- L1 -----------------------------------------------------------------
    def vector_store(self, tenants: List[str]) -> None:
        print("\nL1 vector store: policy search is filtered inside Weaviate")
        from src.claimbridge.langgraph.nodes import get_rag_orchestrator, initialize_rag_orchestrator
        try:
            client = (get_rag_orchestrator() or initialize_rag_orchestrator()).client
        except Exception as e:
            self.check("L1", "connect to the vector store", False, str(e)[:160], kind="error")
            return
        self._vector_client = client
        for tenant in tenants:
            foreign, total, errors = [], 0, []
            for probe in PROBES:
                hits = None
                for attempt in (1, 2):      # one retry: a timed-out probe is not evidence of anything
                    try:
                        hits = client.search_policies(probe, tenant_id=tenant, limit=10)
                        break
                    except Exception as e:
                        if attempt == 2:
                            errors.append(f"{probe[:30]}...: {str(e)[:80]}")
                if hits is None:
                    continue
                total += len(hits)
                foreign += [h["doc_key"] for h in hits
                            if h["tenant_id"] != tenant or not h["doc_key"].startswith(tenant)]
            detail = f"foreign: {sorted(set(foreign))}" if foreign else ("; ".join(errors) or "no hits at all")
            self.check("L1", f"{tenant}: {total} hits over {len(PROBES)} probes, 0 foreign",
                       total > 0 and not foreign and not errors, detail,
                       kind="leak" if foreign else "error")

    # -- L2 / L3 ------------------------------------------------------------
    def api(self, tenants: List[str], keys: Dict[str, str], admin: str) -> None:
        print("\nL2 API: another tenant's claim is 404, and the probe is audited")
        with httpx.Client(base_url=self.base_url, timeout=60) as c:
            for asker in tenants:
                for owner in tenants:
                    if asker == owner:
                        continue
                    claim = DEMO_CLAIM[owner]
                    r = c.get(f"/v1/tenants/{asker}/claims/{claim}", headers={"X-Api-Key": keys[asker]})
                    self.check("L2", f"{asker} asking for {owner}'s {claim} -> 404",
                               r.status_code == 404, f"HTTP {r.status_code}: {r.text[:120]}")
            with session_scope() as s:
                n = s.execute(text("""SELECT count(*) FROM audit_events
                                      WHERE action = 'CLAIM_ACCESS_DENIED'
                                        AND details->>'exists_under_other_tenant' = 'true'""")).scalar()
            self.check("L2", "cross-tenant probes recorded in the audit trail", n > 0, f"{n} events")

            print("\nL3 API keys: a key is bound to its tenant")
            for asker in tenants:
                other = next(t for t in tenants if t != asker)
                r = c.get(f"/v1/tenants/{other}/claims/{DEMO_CLAIM[other]}", headers={"X-Api-Key": keys[asker]})
                self.check("L3", f"{asker} key on {other} route -> 403", r.status_code == 403,
                           f"HTTP {r.status_code}")
            for tenant in tenants:
                r = c.get(f"/v1/tenants/{tenant}/review-queue", headers={"X-Api-Key": admin})
                rows = r.json() if r.status_code == 200 else []
                foreign = [x["id"] for x in rows if x["tenant_id"] != tenant]
                self.check("L3", f"{tenant} review queue holds only its own drafts ({len(rows)})",
                           r.status_code == 200 and not foreign, f"foreign rows {foreign}")

    # -- L4 -----------------------------------------------------------------
    def database(self) -> None:
        print("\nL4 database: the composite foreign key refuses a cross-tenant row")
        try:
            with session_scope() as s:
                s.execute(text("""INSERT INTO communications
                        (tenant_id, claim_id, audience, status, content, citations, correlation_id, created_by)
                        VALUES ('coastal-ppo', 'CLAIM-PH-001', 'member', 'DRAFT', '{}'::jsonb, '[]'::jsonb,
                                'leakage-test', 'leakage-test')"""))
            self.check("L4", "coastal communication for a Pacific claim is rejected", False,
                       "the insert SUCCEEDED -- isolation is broken")
        except IntegrityError as e:
            self.check("L4", "coastal communication for a Pacific claim is rejected",
                       "fk_communications_claim" in str(e).lower() or "foreign key" in str(e).lower(), str(e)[:120])
        except Exception as e:                      # pragma: no cover
            self.check("L4", "coastal communication for a Pacific claim is rejected", False, str(e)[:160])

    # -- L5 -----------------------------------------------------------------
    def generated_text(self, tenants: List[str], keys: Dict[str, str]) -> None:
        print("\nL5 generated text: citations and retrieved sections stay inside the tenant")
        with httpx.Client(base_url=self.base_url, timeout=300) as c:
            for tenant in tenants:
                claim = DEMO_CLAIM[tenant]
                r = c.post(f"/v1/tenants/{tenant}/claims/{claim}/member-summary",
                           headers={"X-Api-Key": keys[tenant]})
                if r.status_code != 201:
                    self.check("L5", f"{tenant}: member summary generated", False, f"HTTP {r.status_code}")
                    continue
                body = r.json()
                docs = [ct.get("document") for ct in body["summary"]["citations"] if ct.get("document")]
                docs += [s.split("#", 1)[0] for s in body.get("retrieved_sections") or []]
                foreign = sorted({d for d in docs if not d.startswith(tenant)})
                self.check("L5", f"{tenant}: {len(docs)} sources cited/retrieved, 0 foreign",
                           not foreign, f"foreign: {foreign}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Cross-tenant leakage suite")
    p.add_argument("--base-url", default=os.getenv("CLAIMBRIDGE_API_URL", "http://localhost:8000"))
    p.add_argument("--no-llm", action="store_true", help="skip L5 (no OpenAI calls)")
    args = p.parse_args(argv)

    with session_scope() as s:
        tenants = [t for (t,) in s.execute(text("SELECT tenant_id FROM tenants ORDER BY tenant_id"))
                   if t in DEMO_CLAIM]
        keys = {t: issue_key(s, f"leakage:{t}", "submitter", t) for t in tenants}
        admin = issue_key(s, "leakage:admin", "admin", None)

    print("=" * 78 + f"\nCROSS-TENANT LEAKAGE SUITE - tenants: {', '.join(tenants)}\n" + "=" * 78)
    suite = Suite(args.base_url)
    try:
        suite.vector_store(tenants)
    finally:
        client = getattr(suite, "_vector_client", None)
        if client is not None:
            try:
                client.close()      # this script owns its own Weaviate connection
            except Exception:
                pass
    suite.api(tenants, keys, admin)
    suite.database()
    if args.no_llm:
        print("\nL5 skipped (--no-llm)")
    else:
        suite.generated_text(tenants, keys)

    failed = [r for r in suite.results if not r["passed"]]
    leaks = [r for r in failed if r["kind"] == "leak"]
    errors = [r for r in failed if r["kind"] == "error"]
    verdict = "LEAKAGE DETECTED" if leaks else ("INCONCLUSIVE (probe errors)" if errors else "NO LEAKAGE")
    report = {"run_id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"), "tenants": tenants,
              "checks": suite.results, "passed": len(suite.results) - len(failed), "failed": len(failed),
              "leaks": len(leaks), "errors": len(errors), "verdict": verdict, "llm_checks": not args.no_llm}
    out = Path(default_out_dir())
    try:
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"leakage-{report['run_id']}.json"
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        report_line = f"report: {path}"
    except OSError as e:
        report_line = f"report not written: {e}"

    print("\n" + "=" * 78)
    print(f"{report['passed']}/{len(suite.results)} checks passed -> {verdict}")
    if errors and not leaks:
        print("An errored probe proves nothing either way: re-run it before reporting a result.")
    print(report_line)
    return 1 if leaks else (2 if errors else 0)      # 0 clean, 1 leakage, 2 inconclusive


if __name__ == "__main__":
    sys.exit(main())
