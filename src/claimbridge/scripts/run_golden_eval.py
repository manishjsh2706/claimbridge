"""
Golden eval runner - ClaimBridge
================================

Runs the provided golden cases against the RUNNING API -- the same way the
mentor will -- and reports a pass rate against the Essential gate (>= 85%).

    docker compose exec claimbridge python -m src.claimbridge.scripts.run_golden_eval
    ... --iteration 1              only cases whose iteration_min <= 1
    ... --case ph-001-member       one case (repeatable)
    ... --no-judge                 exact checks only (no LLM cost)

For each runnable case (see run_case for the three shapes):
  1. call the API: member-summary, provider-notice, or the full pipeline
     (submit fixture -> /drafts). Drafts are real and audited, actor
     "eval:golden-runner". Full-pipeline drafts enter the review queue and
     supersede an older draft of the same claim.
  2. exact checks   (evals/checks.py)
  3. LLM judge      (evals/judge.py): rubric scores + unsupported statements
  4. case PASSES only if every check passes AND the rubric verdict passes

Cases needing features not built yet are SKIPPED with a reason; the pass rate
is passed / executed. A JSON report is written to eval-reports/ (mounted from
the project folder), and the exit code is 0 only when the gate passes -- so
the same command can later gate a CI pipeline or the Summit go-live.
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy import select

from src.claimbridge.db import session_scope
from src.claimbridge.evals import (
    JUDGE_PROMPT_VERSION, GoldenCaseError, judge_summary, load_golden_cases, rubric_verdict,
    run_checks, skip_reason,
)
from src.claimbridge.evals.checks import CheckResult, run_pipeline_checks, run_provider_checks
from src.claimbridge.evals.judge import (
    JudgeError, critical_average, judge_provider_notice, provider_rubric_verdict,
)
from src.claimbridge.intake import any_payload
from src.claimbridge.knowledge import get_code_reference, load_tenant_corpora, resources_dir
from src.claimbridge.models import Adjudication, Claim, Tenant

logger = logging.getLogger("golden_eval")

GATE_DEFAULT = 0.85          # problem-statement.md success criterion 4
# The judge must be at least as capable as the model it grades. With
# gpt-4o-mini judging gpt-4o-mini, the judge marked a sentence copied from the
# cited policy as "unsupported" and ignored its own missing-data rule (eval
# run 2026-09-21T16:03). Override with $JUDGE_MODEL.
JUDGE_MODEL_DEFAULT = "gpt-4o"
ACTOR = "eval:golden-runner"      # platform-wide submitter principal; key rotated every run


# ---------------------------------------------------------------------------
# Evidence for the judge: what the generator was allowed to know
# ---------------------------------------------------------------------------

MISSING = "NOT IN ADJUDICATION RECORD"


def _money(v) -> str:
    # Spelled out rather than null: the judge must not score a summary down for
    # "omitting" an amount that does not exist (e.g. CLAIM-SE-001 has only a
    # billed amount).
    return f"${v:,.2f}" if v is not None else MISSING


def load_facts(tenant_id: str, claim_id: str) -> Dict[str, Any]:
    """
    Claim facts, adjudication and tenant config straight from the database.
    ICD-10 is left out exactly as it is for the generator, so any diagnosis in
    a summary shows up as unsupported.
    """
    with session_scope() as s:
        t = s.get(Tenant, tenant_id)
        c = s.get(Claim, (tenant_id, claim_id))
        a = s.execute(select(Adjudication).where(Adjudication.tenant_id == tenant_id,
                                                 Adjudication.claim_id == claim_id)).scalar_one_or_none()
        facts: Dict[str, Any] = {}
        if t is not None:
            facts["plan"] = {
                "tenant_id": t.tenant_id, "name": t.display_name, "plan_type": t.plan_type,
                "appeal_window_days": t.appeal_window_days, "appeal_window_basis": t.appeal_window_basis,
                "member_services_phone": t.member_services_phone,
            }
        if c is not None:
            data = c.claim_data or {}
            facts["claim"] = {
                "claim_id": c.claim_id, "claim_type": c.claim_type, "provider": c.provider_name,
                "date_of_service": c.date_of_service.isoformat() if c.date_of_service else None,
                "place_of_service": data.get("place_of_service"),
                "procedure_code": data.get("cpt") or data.get("cpt_hcpcs"),
                "adjuster_note": data.get("notes"),
            }
        if a is not None:
            facts["adjudication"] = {
                "outcome": a.outcome, "billed_amount": _money(a.billed_amount),
                "allowed_amount": _money(a.allowed_amount), "plan_paid": _money(a.plan_paid),
                "member_owes": _money(a.patient_responsibility),
                "carc_codes": list(a.carc_codes or []), "rarc_codes": list(a.rarc_codes or []),
            }
        return facts


def load_tenant_config(tenant_id: str) -> Optional[Dict[str, Any]]:
    with session_scope() as s:
        t = s.get(Tenant, tenant_id)
        if t is None:
            return None
        return {"member_services_phone": t.member_services_phone,
                "appeal_window_days": t.appeal_window_days,
                "policy_corpus_version": t.policy_corpus_version}


class SourceTexts:
    """Full text for every citation a summary can carry."""

    def __init__(self):
        root = resources_dir()
        self.codes = get_code_reference()
        corpora = load_tenant_corpora(root / "policies", root.parent)
        self.sections = {(ch.doc_key, ch.section_path): ch.content
                         for corpus in corpora.values() for ch in corpus.chunks}

    def for_citations(self, citations: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        out = []
        for c in citations:
            if c.get("source_type") == "tenant_policy":
                text = self.sections.get((c.get("document"), c.get("section")))
                text = text or f"(section {c.get('document')}#{c.get('section')} not found in corpus)"
            else:
                d = self.codes.get_code(c.get("code") or "")
                text = d.as_prompt_text() if d else f"(code {c.get('code')} not in reference)"
            out.append({"id": c.get("id", "?"), "text": text})
        return out


# ---------------------------------------------------------------------------
# One case
# ---------------------------------------------------------------------------

def _call(http: httpx.Client, method: str, url: str, **kw):
    try:
        resp = http.request(method, url, **kw)
        try:
            return resp.status_code, resp.json()
        except ValueError:
            return resp.status_code, {"raw": resp.text[:500]}
    except httpx.HTTPError as e:
        return 0, {"error": f"API unreachable: {e}"}


def load_provider_facts(tenant_id: str, claim_id: str) -> Dict[str, Any]:
    """Providers are entitled to the full billing data, including ICD-10 and intake errors."""
    facts = load_facts(tenant_id, claim_id)
    with session_scope() as s:
        c = s.get(Claim, (tenant_id, claim_id))
        if c is not None:
            facts["claim"] = {**facts.get("claim", {}), "member_id": c.member_id,
                              "billing_data": c.claim_data, "intake_status": c.intake_status,
                              "validation_errors": [i for i in (c.validation_issues or [])
                                                    if i.get("severity") == "error"]}
    return facts


def run_case(case, http: httpx.Client, run_id: str, judge_llm: Any, sources: SourceTexts) -> Dict[str, Any]:
    """
    Three shapes of golden case:
      full_pipeline     submit the fixture -> /drafts -> check recommendation + the audience's draft
      audience=provider /provider-notice
      audience=member   /member-summary (Iteration 1 path)
    """
    correlation_id = f"eval-{run_id}-{case.id}"[:64]
    hdr = {"X-Correlation-Id": correlation_id}
    base = f"/v1/tenants/{case.tenant_id}/claims"
    checks, output, status = [], None, 0

    if case.get("input_mode") == "full_pipeline":
        payload = any_payload(case.claim_id)
        s_status, s_body = _call(http, "POST", base, json=payload,
                                 headers={**hdr, "Idempotency-Key": f"eval-{case.claim_id.lower()}"})
        status, d_body = _call(http, "POST", f"{base}/{case.claim_id}/drafts", headers=hdr)
        checks = run_pipeline_checks(case, s_status, s_body, status, d_body)
        output = d_body.get(case.audience) if status == 201 else None
        if output:
            checks += (run_provider_checks(case, 201, output) if case.audience == "provider"
                       else run_checks(case, 201, output, load_tenant_config(case.tenant_id)))
        elif status == 201:
            checks.append(CheckResult(f"{case.audience}_draft_generated", False,
                                      (d_body.get("skipped") or {}).get(case.audience, "no draft")))
    elif case.audience == "provider":
        status, body = _call(http, "POST", f"{base}/{case.claim_id}/provider-notice", headers=hdr)
        checks = run_provider_checks(case, status, body)
        output = body if status == 201 else None
    else:
        status, body = _call(http, "POST", f"{base}/{case.claim_id}/member-summary", headers=hdr)
        checks = run_checks(case, status, body, load_tenant_config(case.tenant_id))
        output = body if status == 201 else None

    checks_ok = all(c.passed for c in checks)
    artefact = (output or {}).get("notice" if case.audience == "provider" else "summary")
    result: Dict[str, Any] = {
        "id": case.id, "tenant_id": case.tenant_id, "claim_id": case.claim_id, "audience": case.audience,
        "scenario": case.scenario, "iteration_min": case.iteration_min, "status": "executed",
        "correlation_id": correlation_id, "http_status": status,
        "communication_id": (output or {}).get("communication_id"),
        "generation_mode": ((output or {}).get("validation") or {}).get("generation_mode"),
        "checks": [c.as_dict() for c in checks],
        # What was generated and from what: the report alone must be enough to
        # review a failure, without querying the database.
        "validation_issues": ((output or {}).get("validation") or {}).get("issues"),
        "retrieved_sections": (output or {}).get("retrieved_sections"),
        "summary": artefact,
        "judge": None, "failure_reasons": [f"check {c.name}: {c.detail}" for c in checks if not c.passed],
    }

    if judge_llm is not None and artefact:
        try:
            if case.audience == "provider":
                cites = [{**c, "id": c.get("id") or f"S{i}"} for i, c in
                         enumerate((artefact.get("code_citations") or []) + (artefact.get("policy_citations") or []), 1)]
                verdict = judge_provider_notice(judge_llm, artefact, load_provider_facts(case.tenant_id, case.claim_id),
                                                sources.for_citations(cites))
                rubric_ok, reasons = provider_rubric_verdict(verdict["scores"], case.rubric_min_scores,
                                                             verdict["unsupported_claims"])
                verdict["critical_average"] = round(sum(v["score"] for v in verdict["scores"].values())
                                                    / len(verdict["scores"]), 2)
            else:
                verdict = judge_summary(judge_llm, artefact, load_facts(case.tenant_id, case.claim_id),
                                        sources.for_citations(artefact.get("citations") or []))
                rubric_ok, reasons = rubric_verdict(verdict["scores"], case.rubric_min_scores,
                                                    verdict["unsupported_claims"])
                verdict["critical_average"] = critical_average(verdict["scores"])
            verdict["rubric_passed"] = rubric_ok
            result["judge"] = verdict
            result["failure_reasons"] += [f"rubric: {r}" for r in reasons]
        except JudgeError as e:
            result["judge"] = {"error": str(e)}
            result["failure_reasons"].append(f"judge: {e}")
    elif judge_llm is None:
        result["judge"] = {"skipped": "--no-judge"}

    result["passed"] = checks_ok and not result["failure_reasons"]
    return result


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def default_out_dir() -> Path:
    env = os.getenv("EVAL_REPORT_DIR")
    if env:
        return Path(env)
    app_dir = Path("/app/eval-reports")
    return app_dir if app_dir.is_dir() else resources_dir().parent / "eval-reports"


def run(args, http: Optional[httpx.Client] = None, judge_llm: Any = None) -> Dict[str, Any]:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    cases = load_golden_cases(resources_dir() / "golden")
    if args.case:
        unknown = set(args.case) - {c.id for c in cases}
        if unknown:
            raise GoldenCaseError(f"Unknown case id(s): {sorted(unknown)}")
        cases = [c for c in cases if c.id in args.case]

    if judge_llm is None and not args.no_judge:
        from src.claimbridge.llm import create_llm_client
        judge_llm = create_llm_client(model=os.getenv("JUDGE_MODEL") or JUDGE_MODEL_DEFAULT)

    own_http = http is None
    if own_http:
        from src.claimbridge.auth import issue_key
        with session_scope() as s:
            api_key = issue_key(s, ACTOR, "submitter", None)
        http = httpx.Client(base_url=args.base_url, timeout=180.0, headers={"X-Api-Key": api_key})
    sources = SourceTexts()
    results = []
    try:
        for case in cases:
            reason = skip_reason(case, args.iteration)
            if reason:
                results.append({"id": case.id, "tenant_id": case.tenant_id, "claim_id": case.claim_id,
                                "scenario": case.scenario, "iteration_min": case.iteration_min,
                                "status": "skipped", "skip_reason": reason, "passed": None})
                continue
            print(f"  running {case.id} ...", flush=True)
            results.append(run_case(case, http, run_id, None if args.no_judge else judge_llm, sources))
    finally:
        if own_http:
            http.close()

    executed = [r for r in results if r["status"] == "executed"]
    passed = [r for r in executed if r["passed"]]
    rate = len(passed) / len(executed) if executed else 0.0
    report = {
        "run_id": run_id,
        "base_url": args.base_url,
        "iteration": args.iteration,
        "judge": None if args.no_judge else {
            "model": getattr(judge_llm, "model", None), "prompt_version": JUDGE_PROMPT_VERSION},
        "gate": {"threshold": args.gate, "pass_rate": round(rate, 4),
                 "passed": bool(executed) and rate >= args.gate and not args.no_judge,
                 "note": ("exact checks only -- rubric not scored, gate cannot pass" if args.no_judge
                          else "" if executed else "no cases executed")},
        "counts": {"total": len(results), "executed": len(executed), "passed": len(passed),
                   "failed": len(executed) - len(passed),
                   "skipped": len(results) - len(executed)},
        "cases": results,
    }
    return report


def print_report(report: Dict[str, Any], path: Optional[Path]) -> None:
    print()
    if report.get("judge"):
        print(f"judge: {report['judge']['model']} ({report['judge']['prompt_version']})")
    print(f"{'CASE':<22}{'ITER':<6}{'RESULT':<9}{'CRIT AVG':<10}DETAIL")
    print("-" * 100)
    for r in report["cases"]:
        if r["status"] == "skipped":
            print(f"{r['id']:<22}{r['iteration_min']:<6}{'SKIP':<9}{'':<10}{r['skip_reason']}")
            continue
        j = r.get("judge") or {}
        avg = j.get("critical_average")
        if r["failure_reasons"]:
            detail = "; ".join(r["failure_reasons"])
        elif j.get("skipped"):
            detail = "exact checks passed (rubric not scored)"
        else:
            detail = "all checks and rubric passed"
        print(f"{r['id']:<22}{r['iteration_min']:<6}{'PASS' if r['passed'] else 'FAIL':<9}"
              f"{'' if avg is None else avg:<10}{detail[:200]}")
        for u in j.get("unsupported_claims") or []:
            print(f"{'':<47}unsupported: \"{u['statement'][:90]}\"")
    c, g = report["counts"], report["gate"]
    print("-" * 100)
    print(f"executed {c['executed']}  passed {c['passed']}  failed {c['failed']}  skipped {c['skipped']}")
    print(f"pass rate {g['pass_rate']:.0%}  (gate {g['threshold']:.0%})  ->  "
          f"GATE {'PASSED' if g['passed'] else 'FAILED'}" + (f"  [{g['note']}]" if g["note"] else ""))
    if path:
        print(f"report: {path}")


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(description="Run ClaimBridge golden eval cases against the API")
    p.add_argument("--base-url", default=os.getenv("CLAIMBRIDGE_API_URL", "http://localhost:8000"))
    p.add_argument("--iteration", type=int, default=3, help="run cases with iteration_min <= this (default 3)")
    p.add_argument("--case", action="append", help="run only this case id (repeatable)")
    p.add_argument("--no-judge", action="store_true", help="exact checks only, no LLM judge")
    p.add_argument("--gate", type=float, default=GATE_DEFAULT)
    p.add_argument("--out-dir", type=Path, default=None)
    args = p.parse_args(argv)

    try:
        report = run(args)
    except (GoldenCaseError, OSError) as e:
        print(f"EVAL SETUP ERROR: {e}", file=sys.stderr)
        return 2

    out_dir = args.out_dir or default_out_dir()
    path: Optional[Path] = None
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"golden-{report['run_id']}.json"
        path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    except OSError as e:
        print(f"WARNING: could not write report to {out_dir}: {e}", file=sys.stderr)
    print_report(report, path)
    return 0 if report["gate"]["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
