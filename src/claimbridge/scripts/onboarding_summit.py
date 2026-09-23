"""
Summit Employer Health onboarding gate - ClaimBridge (Iteration 3)
==================================================================

    docker compose exec claimbridge python -m src.claimbridge.scripts.onboarding_summit
    ... --signed-off-by "Manish Joshi"    record the human reviewer on the report
    ... --promote                          flip ONBOARDING -> LIVE (needs a passing gate + sign-off)
    ... --cleanup                          remove the synthetic onboarding claims

Executes resources/tenant-onboarding-checklist.md phases 1-4 against the
running system and prints a GO / NO-GO decision.

  Phase 1  Configuration      tenant record, ONBOARDING mode, staging keys
  Phase 2  Policy corpus      ingested, corpus version recorded, chunk metadata
  Phase 3  Code glossary      shared CARC/RARC reference + tenant branding
  Phase 4  Eval gate          >= 10 Summit scenarios through the real pipeline:
                              rubric average >= 3.5 on critical dimensions,
                              accuracy >= 4 (the checklist's faithfulness >= 0.85),
                              citations on 100% of "why" statements,
                              zero cross-tenant citations,
                              ONBOARDING tenants cannot publish
  Phase 6  Go-live approval   only with a passing gate AND a named human

The scenarios are real claims (`CLAIM-SE-ONB-*`) submitted through the API, so
the gate exercises intake, recommendation, summary and review routing. They are
synthetic and removable with --cleanup.
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

from src.claimbridge.auth import issue_key
from src.claimbridge.db import session_scope
from src.claimbridge.evals.checks import run_checks, run_pipeline_checks, run_provider_checks
from src.claimbridge.evals.golden import GoldenCase
from src.claimbridge.evals.judge import (
    JudgeError, critical_average, judge_provider_notice, judge_summary, provider_rubric_verdict,
    rubric_verdict,
)
from src.claimbridge.evals.summit_pack import CLAIM_IDS, SCENARIOS, payload
from src.claimbridge.knowledge import get_code_reference
from src.claimbridge.models import Tenant
from src.claimbridge.scripts.run_golden_eval import (
    JUDGE_MODEL_DEFAULT, SourceTexts, _call, default_out_dir, load_facts, load_provider_facts,
    load_tenant_config,
)

TENANT = "summit-employer"
REQUIRED_SCENARIOS = 10
CRITICAL_AVERAGE_MIN = 3.5
ACCURACY_MIN = 4          # checklist: "Faithfulness >= 0.85 (or rubric >= 4 on accuracy)"


class Checklist:
    def __init__(self):
        self.items: List[Dict[str, Any]] = []

    def check(self, phase: str, name: str, passed: bool, detail: str = "") -> bool:
        self.items.append({"phase": phase, "check": name, "passed": passed, "detail": detail})
        print(f"  [{'x' if passed else ' '}] {name}" + (f"   <- {detail}" if detail else ""))
        return passed

    @property
    def failed(self):
        return [i for i in self.items if not i["passed"]]


def phase1(cl: Checklist) -> None:
    print("\nPhase 1 - Configuration")
    with session_scope() as s:
        t = s.get(Tenant, TENANT)
    if t is None:
        cl.check("1", "tenant record exists", False, "no tenants row")
        return
    cl.check("1", "tenant record: appeal window 45 days, member services phone, tone",
             t.appeal_window_days == 45 and t.member_services_phone == "1-800-555-0177"
             and t.branding_tone in ("plain", "formal"),
             f"{t.appeal_window_days}d, {t.member_services_phone}, tone={t.branding_tone}")
    cl.check("1", "feature flag: ONBOARDING mode (no production traffic)", t.status == "ONBOARDING",
             f"status={t.status}")
    cl.check("1", "auto-publish disabled for this tenant", not t.allow_auto_publish_approve, "")


def phase2(cl: Checklist) -> None:
    print("\nPhase 2 - Policy corpus")
    from src.claimbridge.langgraph.nodes import get_rag_orchestrator, initialize_rag_orchestrator
    try:
        client = (get_rag_orchestrator() or initialize_rag_orchestrator()).client
    except Exception as e:
        cl.check("2", "vector store reachable", False, str(e)[:120])
        return
    try:
        n = client.count_tenant_policies(TENANT)
        cl.check("2", "SPD excerpt and medical policy ingested", n > 0, f"{n} sections")
        hits = client.search_policies("claim completeness and appeals", tenant_id=TENANT, limit=5)
        missing = [k for k in ("tenant_id", "document_id", "section_path")
                   if hits and not hits[0].get(k)]
        cl.check("2", "chunk metadata carries tenant_id, document_id, section_path",
                 bool(hits) and not missing, f"missing {missing}" if missing else "")
        with session_scope() as s:
            t = s.get(Tenant, TENANT)
        cl.check("2", "policy_corpus_version recorded on the tenant", bool(t.policy_corpus_version),
                 t.policy_corpus_version or "not set")
    finally:
        try:
            client.close()
        except Exception:
            pass


def phase3(cl: Checklist) -> None:
    print("\nPhase 3 - Code glossary")
    codes = get_code_reference()
    cl.check("3", "shared CARC/RARC reference linked", len(codes) >= 5, f"{len(codes)} codes")
    with session_scope() as s:
        t = s.get(Tenant, TENANT)
    cl.check("3", "tenant branding wrapper (tone, appeal text, phone) configured",
             bool(t and t.branding_tone and t.appeal_window_basis and t.member_services_phone),
             f"tone={t.branding_tone if t else '-'}, basis={t.appeal_window_basis if t else '-'}")


def run_scenarios(cl: Checklist, base_url: str, judge_llm, key: str) -> List[Dict[str, Any]]:
    print(f"\nPhase 4 - Eval gate ({len(SCENARIOS)} Summit scenarios through the live pipeline)")
    sources = SourceTexts()
    results = []
    with httpx.Client(base_url=base_url, timeout=300, headers={"X-Api-Key": key}) as http:
        for sc in SCENARIOS:
            claim_id = sc["payload"]["claim_id"]
            audience = sc.get("audience", "member")
            case = GoldenCase(id=sc["id"], tenant_id=TENANT, claim_id=claim_id, audience=audience,
                              scenario="onboarding", iteration_min=3,
                              raw={k: v for k, v in sc.items() if k not in ("payload", "id")})
            base = f"/v1/tenants/{TENANT}/claims"
            s_status, s_body = _call(http, "POST", base, json=payload(sc),
                                     headers={"Idempotency-Key": f"onb-{claim_id.lower()}"})
            d_status, d_body = _call(http, "POST", f"{base}/{claim_id}/drafts")
            checks = run_pipeline_checks(case, s_status, s_body, d_status, d_body)
            output = d_body.get(audience) if d_status == 201 else None
            if output:
                checks += (run_provider_checks(case, 201, output) if audience == "provider"
                           else run_checks(case, 201, output, load_tenant_config(TENANT)))
            elif d_status == 201:
                checks.append(type(checks[0])(f"{audience}_draft_generated", False,
                                              (d_body.get("skipped") or {}).get(audience, "no draft")))
            artefact = (output or {}).get("notice" if audience == "provider" else "summary")
            reasons = [f"check {c.name}: {c.detail}" for c in checks if not c.passed]
            verdict = None
            if artefact:
                try:
                    if audience == "provider":
                        cites = [{**c, "id": c.get("id") or f"S{i}"} for i, c in
                                 enumerate((artefact.get("code_citations") or [])
                                           + (artefact.get("policy_citations") or []), 1)]
                        verdict = judge_provider_notice(judge_llm, artefact,
                                                        load_provider_facts(TENANT, claim_id),
                                                        sources.for_citations(cites))
                        ok, why = provider_rubric_verdict(verdict["scores"], sc.get("rubric_min_scores", {}),
                                                          verdict["unsupported_claims"])
                        verdict["critical_average"] = round(
                            sum(v["score"] for v in verdict["scores"].values()) / len(verdict["scores"]), 2)
                    else:
                        verdict = judge_summary(judge_llm, artefact, load_facts(TENANT, claim_id),
                                                sources.for_citations(artefact.get("citations") or []))
                        ok, why = rubric_verdict(verdict["scores"], sc.get("rubric_min_scores", {}),
                                                 verdict["unsupported_claims"])
                        verdict["critical_average"] = critical_average(verdict["scores"])
                    verdict["rubric_passed"] = ok
                    reasons += [f"rubric: {r}" for r in why]
                except JudgeError as e:
                    verdict = {"error": str(e)}
                    reasons.append(f"judge: {e}")
            passed = not reasons
            results.append({"id": sc["id"], "claim_id": claim_id, "audience": audience,
                            "adapted_from": sc["adapted_from"], "passed": passed,
                            "failure_reasons": reasons, "judge": verdict, "artefact": artefact,
                            "recommendation": (d_body.get("recommendation") or {}).get("recommendation")
                            if d_status == 201 else None,
                            "routing": d_body.get("routing") if d_status == 201 else None})
            avg = (verdict or {}).get("critical_average")
            print(f"  {'PASS' if passed else 'FAIL'}  {sc['id']:28} {audience:8} "
                  f"avg={avg if avg is not None else '-':>5}  {'; '.join(reasons)[:90]}")
    return results


def phase4(cl: Checklist, results: List[Dict[str, Any]], base_url: str, key: str) -> None:
    cl.check("4", f"golden set of >= {REQUIRED_SCENARIOS} Summit scenarios run",
             len(results) >= REQUIRED_SCENARIOS, f"{len(results)} scenarios")
    passed = [r for r in results if r["passed"]]
    cl.check("4", "every scenario passes its checks and rubric minimums",
             len(passed) == len(results), f"{len(passed)}/{len(results)} passed")

    avgs = [r["judge"]["critical_average"] for r in results
            if r.get("judge") and r["judge"].get("critical_average") is not None]
    mean = round(sum(avgs) / len(avgs), 2) if avgs else 0
    cl.check("4", f"rubric average >= {CRITICAL_AVERAGE_MIN} on critical dimensions",
             bool(avgs) and mean >= CRITICAL_AVERAGE_MIN, f"average {mean} over {len(avgs)} scenarios")

    accuracies = [r["judge"]["scores"]["accuracy"]["score"] for r in results
                  if r.get("judge") and "scores" in r["judge"] and "accuracy" in r["judge"]["scores"]]
    cl.check("4", f"faithfulness: accuracy >= {ACCURACY_MIN} on every scenario",
             bool(accuracies) and min(accuracies) >= ACCURACY_MIN,
             f"lowest accuracy {min(accuracies) if accuracies else '-'}")

    needs_why = [r for r in results if r["audience"] == "member" and r.get("artefact")
                 and (r["artefact"].get("outcome") in ("PARTIAL", "DENY"))]
    uncited = [r["id"] for r in needs_why if not r["artefact"].get("citations")]
    cl.check("4", "citation presence: 100% of adjusted/denied summaries cite a source",
             not uncited, f"uncited: {uncited}" if uncited else f"{len(needs_why)} summaries checked")

    foreign = []
    for r in results:
        a = r.get("artefact") or {}
        docs = [c.get("document") for c in (a.get("citations") or []) + (a.get("policy_citations") or [])
                if c.get("document")]
        foreign += [d for d in docs if not d.startswith(TENANT)]
    cl.check("4", "zero cross-tenant citations", not foreign, f"foreign: {sorted(set(foreign))}")

    published = [r for r in results if (r.get("routing") or {}).get("member", {}).get("status") == "PUBLISHED"]
    cl.check("4", "no draft auto-published while ONBOARDING", not published,
             f"published: {[r['id'] for r in published]}" if published else "")

    comm = next((r["routing"]["member"]["communication_id"] for r in results
                 if (r.get("routing") or {}).get("member")), None)
    if comm is None:
        cl.check("4", "publishing is blocked for an ONBOARDING tenant", False, "no draft to test with")
        return
    with httpx.Client(base_url=base_url, timeout=60) as http:
        with session_scope() as s:
            reviewer = issue_key(s, "onboarding:reviewer", "reviewer", TENANT)
        h = {"X-Api-Key": reviewer}
        http.post(f"/v1/tenants/{TENANT}/communications/{comm}/approve", headers=h,
                  json={"note": "onboarding gate check"})
        r = http.post(f"/v1/tenants/{TENANT}/communications/{comm}/publish", headers=h)
    cl.check("4", "publishing is blocked for an ONBOARDING tenant", r.status_code == 403,
             f"HTTP {r.status_code}: {r.text[:100]}")


def cleanup() -> int:
    with session_scope() as s:
        before = s.execute(text("SELECT count(*) FROM claims WHERE tenant_id = :t AND claim_id = ANY(:ids)"),
                           {"t": TENANT, "ids": CLAIM_IDS}).scalar()
        s.execute(text("DELETE FROM claims WHERE tenant_id = :t AND claim_id = ANY(:ids)"),
                  {"t": TENANT, "ids": CLAIM_IDS})
        after = s.execute(text("SELECT count(*) FROM claims WHERE tenant_id = :t AND claim_id = ANY(:ids)"),
                          {"t": TENANT, "ids": CLAIM_IDS}).scalar()
    print(f"removed {before - after} onboarding claims (audit events stay: the trail is append-only)")
    return 0


def promote(report: Dict[str, Any]) -> int:
    if not report["gate"]["passed"] or not report.get("signed_off_by"):
        print("REFUSED: promotion needs a passing gate AND --signed-off-by <name>")
        return 1
    with session_scope() as s:
        s.execute(text("UPDATE tenants SET status = 'LIVE', updated_at = now() WHERE tenant_id = :t"),
                  {"t": TENANT})
    print(f"{TENANT} promoted ONBOARDING -> LIVE (signed off by {report['signed_off_by']})")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Run the Summit onboarding gate")
    p.add_argument("--base-url", default=os.getenv("CLAIMBRIDGE_API_URL", "http://localhost:8000"))
    p.add_argument("--signed-off-by", help="human reviewer who signs the eval report")
    p.add_argument("--promote", action="store_true", help="flip ONBOARDING -> LIVE when the gate passes")
    p.add_argument("--cleanup", action="store_true", help="delete the synthetic onboarding claims")
    args = p.parse_args(argv)
    if args.cleanup:
        return cleanup()

    from src.claimbridge.llm import create_llm_client
    judge_llm = create_llm_client(model=os.getenv("JUDGE_MODEL") or JUDGE_MODEL_DEFAULT)
    with session_scope() as s:
        key = issue_key(s, "onboarding:runner", "submitter", TENANT)

    print("=" * 78 + f"\nSUMMIT ONBOARDING GATE - {TENANT}\n" + "=" * 78)
    cl = Checklist()
    phase1(cl)
    phase2(cl)
    phase3(cl)
    results = run_scenarios(cl, args.base_url, judge_llm, key)
    phase4(cl, results, args.base_url, key)

    gate_passed = not cl.failed
    report = {
        "run_id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "tenant": TENANT, "checklist": cl.items, "scenarios": results,
        "signed_off_by": args.signed_off_by,
        "gate": {"passed": gate_passed, "failed_checks": [i["check"] for i in cl.failed]},
        "judge": {"model": getattr(judge_llm, "model", None)},
    }
    out = Path(default_out_dir())
    try:
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"summit-onboarding-{report['run_id']}.json"
        path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        report_line = f"report: {path}"
    except OSError as e:
        report_line = f"report not written: {e}"

    print("\n" + "=" * 78)
    print(f"Phases 1-4: {len(cl.items) - len(cl.failed)}/{len(cl.items)} checks passed")
    unsigned = "NOT SIGNED (rerun with --signed-off-by '<name>')"
    print(f"Phase 6 sign-off: {args.signed_off_by or unsigned}")
    decision = "GO (staging)" if gate_passed and args.signed_off_by else "NO-GO"
    print(f"DECISION: {decision}")
    print(report_line)
    if args.promote:
        return promote(report)
    return 0 if gate_passed else 1


if __name__ == "__main__":
    sys.exit(main())
