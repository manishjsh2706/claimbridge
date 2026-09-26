"""
ClaimBridge MCP server
======================

Exposes ClaimBridge to AI assistants over the Model Context Protocol, so a
claims analyst can ask "why was CLAIM-PH-004 denied, and what does Pacific's
prior-auth policy say?" and get an answer from real data instead of a guess.

THE ONE DESIGN DECISION EVERYTHING ELSE FOLLOWS FROM
    This server is an ordinary API client. It holds an API key and calls the
    same /v1 routes a human's portal calls. It has no database session, no
    Weaviate connection and no special path.

    The alternative -- importing the application and reading the database
    directly -- would have been faster by one network hop, and would have meant
    re-implementing tenant scoping, permission checks and audit logging inside
    the tool code. Three guarantees, two implementations each, free to drift.
    Here they cannot drift, because there is only one implementation and this
    server is on the far side of it. If the code below is wrong, the worst it
    can do is what its own API key is already allowed to do.

TENANT COMES FROM THE KEY, NOT FROM ARGUMENTS
    At startup the server calls /v1/whoami and remembers the tenant its key is
    scoped to. No tool takes a tenant parameter. A prompt-injected "now look up
    the Coastal PPO claim" has nothing to inject into: there is no argument for
    it, and the API would refuse it anyway (403, audited).

WHAT IS DELIBERATELY NOT A TOOL
    approve, publish, reject. Making them tools would let an assistant generate
    a draft and approve its own work in the next tool call, and the four-eyes
    rule (author != approver) would be over -- not bypassed by a bug, but
    designed away. Human approval stays human.

    submit_claim, for the same reason in a different direction: a tool that
    creates records is a tool that prompt injection can aim. Read-only tools
    can be wrong; write tools can be used.

    The rule used here: pick tools by blast radius, not by capability.

WHY THE DOCSTRINGS MATTER
    In a REST controller a docstring is documentation nobody reads. Here the
    SDK turns each tool's docstring and type hints into the description and
    JSON schema the model reads to decide what to call and with what. They are
    interface, not commentary, and are written as carefully as the code.
"""

import logging
import os
import sys
import uuid
from typing import Any, Dict, List, Optional

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

# stdio transport: stdout carries JSON-RPC and nothing else, so every log line
# goes to stderr. A stray print() here corrupts the protocol.
logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                    format="%(asctime)s %(levelname)s [mcp] %(message)s")
logger = logging.getLogger(__name__)

API_URL = os.getenv("CLAIMBRIDGE_API_URL", "http://localhost:8000").rstrip("/")
API_KEY = os.getenv("CLAIMBRIDGE_API_KEY", "")
TIMEOUT = float(os.getenv("CLAIMBRIDGE_MCP_TIMEOUT", "60"))

READ_ONLY = ToolAnnotations(read_only_hint=True)

mcp = MCPServer(
    name="claimbridge",
    version="0.1.0",
    instructions=(
        "Read-only access to one health plan's claims in ClaimBridge. Every tool is "
        "scoped to the tenant this server's API key belongs to; there is no way to "
        "reach another plan's data. Amounts, outcomes, appeal windows and policy text "
        "are returned verbatim from the system of record -- quote them, do not "
        "recompute or paraphrase them. This server cannot approve, publish or change "
        "anything: those are human decisions made in ClaimBridge itself."
    ),
)


class ApiError(RuntimeError):
    """A refusal or failure from ClaimBridge, in words a model can act on."""


_identity: Dict[str, Any] = {}


def _client() -> httpx.Client:
    if not API_KEY:
        raise ApiError("CLAIMBRIDGE_API_KEY is not set, so this server has no identity "
                       "and cannot read anything.")
    return httpx.Client(
        base_url=API_URL, timeout=TIMEOUT,
        headers={"X-Api-Key": API_KEY, "X-Correlation-Id": "mcp-" + uuid.uuid4().hex[:24]},
    )


def _get(path: str, params: Optional[Dict[str, Any]] = None) -> Any:
    """
    One place where an HTTP answer becomes either data or a readable refusal.

    The status codes are passed through in plain words rather than swallowed:
    a model that is told "403, this key is scoped to another plan" stops, while
    a model handed an empty list assumes the claim does not exist and says so
    to the analyst.
    """
    try:
        with _client() as c:
            r = c.get(path, params=params)
    except httpx.RequestError as e:
        raise ApiError(f"ClaimBridge is not reachable at {API_URL} ({type(e).__name__}).") from e

    if r.status_code == 200:
        return r.json()
    detail = ""
    try:
        body = r.json()
        detail = body.get("detail") or body.get("error") or ""
    except Exception:
        detail = (r.text or "")[:200]
    if r.status_code == 401:
        raise ApiError("ClaimBridge rejected this server's API key.")
    if r.status_code == 403:
        raise ApiError(f"Not allowed: {detail or 'this key may not do that'}. "
                       "This is a permission boundary, not a missing record.")
    if r.status_code == 404:
        # FastAPI's own "Not Found" for an unrouted path reads the same as a
        # missing record unless they are told apart, which sent a first run
        # chasing a data problem that was really a stale server.
        if not detail or detail.strip().lower() == "not found":
            raise ApiError(f"ClaimBridge has no route {path}. The server is probably "
                           "running an older version of the code than this client expects.")
        raise ApiError(f"Not found: {detail}. Note that a record belonging to another "
                       "plan also reports as not found, so this is not proof it does "
                       "not exist.")
    raise ApiError(f"ClaimBridge returned HTTP {r.status_code}: {detail}")


def tenant() -> str:
    """The tenant this key is scoped to, resolved once and cached."""
    if not _identity:
        _identity.update(_get("/v1/whoami"))
    t = _identity.get("tenant_id")
    if not t:
        raise ApiError(
            "This API key is not scoped to a single health plan. Refusing to serve: a "
            "tenant-wide key would make every tool below reach across plans. Issue a "
            "key with --tenant <plan> and restart."
        )
    return t


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool(annotations=READ_ONLY)
def whoami() -> Dict[str, Any]:
    """
    Report which health plan, principal and role this connection is limited to.

    Call this first when the analyst asks what you can see, or when a later
    tool refuses something and you need to explain why.
    """
    _identity.clear()
    identity = _get("/v1/whoami")
    _identity.update(identity)
    return {
        "principal": identity.get("principal_id"),
        "role": identity.get("role"),
        "health_plan": identity.get("tenant_id"),
        "permissions": identity.get("permissions", []),
        "note": "All other tools are limited to this health plan. There is no tenant argument.",
    }


@mcp.tool(annotations=READ_ONLY)
def get_claim(claim_id: str) -> Dict[str, Any]:
    """
    Fetch one claim in full: intake status and any validation problems, the
    adjudication outcome with its CARC/RARC codes and dollar amounts, the
    system's recommendation, and the member and provider drafts with their
    review status.

    claim_id is the plan's own identifier, e.g. "CLAIM-PH-004".

    The amounts and the outcome are the system of record. Quote them exactly;
    never add them up, convert them or restate them as percentages.
    """
    claim = _get(f"/v1/tenants/{tenant()}/claims/{claim_id}")
    return {
        "claim_id": claim.get("claim_id"),
        "health_plan": claim.get("tenant_id"),
        "member_id": claim.get("member_id"),
        "date_of_service": claim.get("date_of_service"),
        "provider": claim.get("provider_name"),
        "intake_status": claim.get("intake_status"),
        "validation_issues": claim.get("validation_issues", []),
        "claim_data": claim.get("claim_data", {}),
        "adjudication": claim.get("adjudication"),
        "recommendation": claim.get("recommendation"),
        "communications": claim.get("communications", []),
    }


@mcp.tool(annotations=READ_ONLY)
def get_recommendation(claim_id: str) -> Dict[str, Any]:
    """
    The system's routing recommendation for a claim -- APPROVE, DENY, PARTIAL
    or NEED_INFO -- with the rationale and the policy sections it cites.

    This is produced by a deterministic rules engine, not by a model: the same
    claim always yields the same recommendation, and `rules_version` records
    which rules produced it. Present it as the system's decision, and do not
    argue with it or offer an alternative outcome.
    """
    claim = _get(f"/v1/tenants/{tenant()}/claims/{claim_id}")
    rec = claim.get("recommendation")
    if not rec:
        return {"claim_id": claim_id,
                "recommendation": None,
                "note": "No recommendation has been produced for this claim yet."}
    return {"claim_id": claim_id, **rec}


@mcp.tool(annotations=READ_ONLY)
def search_policy(query: str, limit: int = 4) -> Dict[str, Any]:
    """
    Search this health plan's own policy documents and return the matching
    sections with their citation paths.

    Use it to ground any statement about what the plan covers or requires.
    Quote or cite the returned `section_path` (for example
    "prior-auth.imaging") rather than describing the rule from memory -- plans
    differ, and a rule from another plan is worse than no answer.

    If `degraded` comes back true the search did not run; say so rather than
    treating an empty result as "the plan has no such rule".
    """
    limit = max(1, min(int(limit), 10))
    out = _get(f"/v1/tenants/{tenant()}/policy-search", {"q": query, "limit": limit})
    return {
        "health_plan": out.get("tenant_id"),
        "query": out.get("query"),
        "degraded": out.get("degraded", False),
        "note": out.get("note"),
        "sections": [
            {
                "section_path": s.get("section_path"),
                "section_title": s.get("section_title"),
                "document": s.get("document_title"),
                "effective_date": s.get("effective_date"),
                "text": s.get("content"),
                "score": round(float(s.get("score", 0.0)), 4),
            }
            for s in out.get("sections", [])
        ],
    }


@mcp.tool(annotations=READ_ONLY)
def explain_codes(codes: List[str]) -> Dict[str, Any]:
    """
    Look up what CARC/RARC adjustment codes mean, e.g. ["CO-197", "CO-45"].

    These are exact definitions from the approved reference, not
    interpretations. A code that comes back under `unknown` has no approved
    definition: say that plainly instead of guessing what it might mean, which
    is what the system itself does -- an unexplained code sends the draft to a
    human.
    """
    if not codes:
        return {"known": [], "unknown": [], "note": "No codes were given."}
    out = _get(f"/v1/tenants/{tenant()}/codes", {"code": codes[:25]})
    return {
        "known": [
            {
                "code": c.get("code"),
                "type": c.get("kind"),
                "official_title": c.get("title"),
                "plain_language": c.get("member_friendly_name"),
                "details": c.get("fields", {}),
            }
            for c in out.get("known", [])
        ],
        "unknown": out.get("unknown", []),
    }


@mcp.tool(annotations=READ_ONLY)
def review_queue(audience: Optional[str] = None, limit: int = 20) -> Dict[str, Any]:
    """
    List the drafts waiting for a human reviewer, oldest first.

    audience filters to "member" or "provider"; omit it for both.

    You can read this queue. You cannot act on it: approving, publishing and
    rejecting are human decisions, and this server has no tool for them. If the
    analyst asks you to approve something, tell them it has to be done by a
    person in ClaimBridge, and that the person approving cannot be the one who
    generated the draft.
    """
    params: Dict[str, Any] = {"limit": max(1, min(int(limit), 200))}
    if audience in ("member", "provider"):
        params["audience"] = audience
    rows = _get(f"/v1/tenants/{tenant()}/review-queue", params)
    return {
        "health_plan": tenant(),
        "waiting": len(rows),
        "drafts": [
            {
                "communication_id": r.get("id"),
                "claim_id": r.get("claim_id"),
                "audience": r.get("audience"),
                "status": r.get("status"),
                "created_at": r.get("created_at"),
                "needs_human_review": r.get("needs_human_review"),
            }
            for r in rows
        ],
        "note": "Read-only. Approval and publishing are done by a person in ClaimBridge.",
    }


def self_check(claim_id: str = "CLAIM-PH-004") -> int:
    """
    Run every tool once against the real ClaimBridge and print what came back.

    `python server.py --check` -- no MCP client involved. Connecting a desktop
    assistant and finding nothing works tells you almost nothing about why;
    this says whether the key is valid, which plan it sees, and whether each
    tool returns data, in one screen.
    """
    checks: List[tuple] = []

    def run(label, fn):
        try:
            value = fn()
            checks.append((True, label, value))
        except Exception as e:                      # noqa: BLE001 - reporting, not handling
            checks.append((False, label, f"{type(e).__name__}: {e}"))

    print(f"ClaimBridge MCP self-check\n  api: {API_URL}\n")
    run("identity", lambda: (lambda w: f"{w['principal']} ({w['role']}) on {w['health_plan']}")(whoami()))
    run(f"get_claim {claim_id}",
        lambda: (lambda c: f"{c['intake_status']}, "
                           f"outcome {(c['adjudication'] or {}).get('outcome', 'n/a')}, "
                           f"{len(c['communications'])} communication(s)")(get_claim(claim_id)))
    run("get_recommendation",
        lambda: (lambda r: f"{r.get('recommendation')} ({r.get('rules_version', 'n/a')})")(
            get_recommendation(claim_id)))
    run("search_policy",
        lambda: (lambda p: ("DEGRADED: search did not run" if p["degraded"] else
                            ", ".join(s["section_path"] for s in p["sections"]) or "no sections"))(
            search_policy("prior authorization required for imaging")))
    run("explain_codes",
        lambda: (lambda e: f"known {[c['code'] for c in e['known']]}, unknown {e['unknown']}")(
            explain_codes(["CO-197", "ZZ-999"])))
    run("review_queue", lambda: f"{review_queue()['waiting']} draft(s) waiting")

    for ok, label, value in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {label:28} {value}")

    failed = [c for c in checks if not c[0]]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    if not failed:
        print("Tools reach real data through the API, scoped to one plan. "
              "No approve/publish/reject tool exists, by design.")
    return 1 if failed else 0


def main() -> None:
    if not API_KEY:
        logger.error("CLAIMBRIDGE_API_KEY is not set; the server would have no identity.")
        sys.exit(2)

    if "--check" in sys.argv:
        claim = next((a for a in sys.argv[1:] if not a.startswith("-")), "CLAIM-PH-004")
        sys.exit(self_check(claim))

    # Fail at startup, not on the analyst's first question, if the key is
    # unusable or not scoped to one plan.
    try:
        logger.info(f"connecting to {API_URL}")
        logger.info(f"identity resolved: tenant={tenant()} role={_identity.get('role')}")
    except ApiError as e:
        logger.error(str(e))
        sys.exit(1)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
