"""
Draft generation as a LangGraph - ClaimBridge
=============================================

The generation loop used to be a `for attempt in range(...)` inside
`member.generate_member_summary`, with `break`s for "guards passed", "LLM is
down" and "out of attempts". That is a state machine written as control flow:
readable enough at 30 lines, but the shape is implicit, nothing can resume it,
and the provider notice had to repeat it.

Here it is the same machine, declared:

               ┌──────────────┐
    START ────►│  escalate?   │──── emergency denial ───► escalate ──► END
               └──────┬───────┘
                      │ no
                      ▼
                  generate ──── LLM unavailable ───────┐
                      │                                │
                      ▼                                │
                  validate                             │
                      │                                │
        guards pass ──┼── guards fail, attempts left ──┘(back to generate,
                      │                                  with the issues as
                      ▼                                  feedback)
                     END                               │
                      ▲          out of attempts       │
                      └────────── fallback ◄───────────┘

WHAT THE GRAPH DOES NOT DECIDE
    Nothing about the claim. The outcome, the amounts, the appeal window and
    the recommendation are all settled before this runs, by the rules engine
    and the database. The graph only routes the *writing* of the explanation:
    generate, check, retry, or give up and use the template. Letting an LLM
    pick the branch here would make the same claim resolvable two different
    ways, which is exactly what an insurer cannot have.

WHY A GRAPH AND NOT THE `for` LOOP
    Resumability. Each node's result is checkpointed, so a run that dies after
    a successful generate -- database hiccup, worker restart, deploy -- picks
    up at the node it died on instead of paying for the LLM call again. With
    the in-memory checkpointer that covers a retry inside the same process;
    point `CLAIMBRIDGE_CHECKPOINT_URL` at Postgres and it survives a restart.

THREAD IDS
    One thread per generation attempt, keyed on the correlation id, NOT on the
    claim. Re-running the pipeline for a claim must produce a fresh draft that
    supersedes the old one (that is the documented behaviour, and the demo
    relies on it); if the thread were keyed on the claim, a re-run would resume
    the finished graph and hand back the previous draft.

STATE IS JSON ONLY
    Checkpointers serialise the state, so ORM objects, the LLM client and the
    assembled context stay out of it and are captured by the node callables
    instead. The state carries what a resumed run genuinely needs: the attempt
    count, the feedback, the candidate draft and the validation issues.
"""

import logging
import os
from typing import Any, Callable, Dict, List, Optional, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 2

# One pool per process, opened on first use (see checkpointer_from_env).
_POSTGRES_SAVER = None


class DraftState(TypedDict, total=False):
    """Everything the loop needs to survive a restart -- and nothing else."""
    attempts: int
    feedback: Optional[List[str]]
    data: Optional[Dict[str, Any]]
    issues: List[str]
    passed: bool
    generation_mode: str
    needs_human_review: bool
    escalation_reason: Optional[str]
    model: Optional[str]
    usage_total: int
    llm_unavailable: bool


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------

def build_draft_graph(
    *,
    generate: Callable[[Optional[List[str]]], Dict[str, Any]],
    validate: Callable[[Dict[str, Any], bool], List[str]],
    template: Callable[[], Dict[str, Any]],
    escalation: Optional[Callable[[], Dict[str, Any]]] = None,
    escalation_reason: Optional[str] = None,
    needs_escalation: Callable[[], bool] = lambda: False,
    validate_template: bool = True,
    unavailable_exc: type = Exception,
    max_attempts: int = MAX_ATTEMPTS,
    checkpointer: Any = None,
):
    """
    Compile the generation graph for one draft.

    The callables come from the caller (member.py, provider.py) so this module
    never imports them back -- it knows the shape of the workflow, not how a
    member summary is worded.

    generate(feedback)  -> {"model": str, "usage": {...}, "data": {...}}
                           raises LLMUnavailable when the model cannot be reached
    validate(data, require_policy_citation) -> list of guard issues ([] = clean)
    template()          -> a model-free draft built from the database alone
    escalation()        -> the fixed-messaging draft, when needs_escalation()
    unavailable_exc     -> the exception class meaning "model unreachable"
    validate_template   -> run the guards over the template too. The member
                           summary does (belt and braces on a draft a human
                           will send); the provider notice does not, because it
                           never has. Made explicit rather than quietly
                           changed: aligning the two is a behaviour change and
                           belongs in its own commit, with the eval re-run.
    """

    def node_escalate(state: DraftState) -> DraftState:
        return {
            "data": escalation(),
            "generation_mode": "fixed_escalation",
            "escalation_reason": escalation_reason,
            "needs_human_review": True,
            "passed": True,
            "issues": [],
        }

    def node_generate(state: DraftState) -> DraftState:
        attempt = state.get("attempts", 0) + 1
        try:
            result = generate(state.get("feedback"))
        except unavailable_exc as e:
            # Only "the model cannot be reached" routes to the template. Any
            # other exception is a bug and must surface, not be papered over
            # with a fallback draft that hides it.
            logger.error(f"[DRAFT] LLM unavailable: {e}")
            return {
                "attempts": attempt,
                "data": None,
                "issues": list(state.get("issues", [])) + [f"LLM unavailable: {e}"],
                "llm_unavailable": True,
            }
        return {
            "attempts": attempt,
            "data": result["data"],
            "model": result["model"],
            "usage_total": state.get("usage_total", 0) + result["usage"].get("total_tokens", 0),
            "llm_unavailable": False,
        }

    def node_validate(state: DraftState) -> DraftState:
        issues = validate(state["data"], True)
        if not issues:
            return {"passed": True, "issues": []}
        logger.warning(f"[DRAFT] attempt {state['attempts']} rejected: {issues}")
        return {"passed": False, "issues": issues, "feedback": issues}

    def node_fallback(state: DraftState) -> DraftState:
        # A stored draft must never contain text that failed a guard, so the
        # rejected model output is dropped and the template takes its place.
        # The rejections stay on the record, prefixed, for the reviewer.
        llm_issues = list(state.get("issues", []))
        data = template()
        template_issues = validate(data, False) if validate_template else []
        model = state.get("model")
        return {
            "data": data,
            "generation_mode": "template_fallback",
            "needs_human_review": True,
            "passed": not template_issues,
            "issues": [f"LLM draft rejected: {i}" for i in llm_issues] + template_issues,
            "model": f"template-fallback (llm: {model})" if model else "template-fallback",
        }

    def route_start(state: DraftState) -> str:
        return "escalate" if (escalation is not None and needs_escalation()) else "generate"

    def route_generate(state: DraftState) -> str:
        return "fallback" if state.get("llm_unavailable") else "validate"

    def route_validate(state: DraftState) -> str:
        if state.get("passed"):
            return END
        return "generate" if state.get("attempts", 0) < max_attempts else "fallback"

    g = StateGraph(DraftState)
    g.add_node("escalate", node_escalate)
    g.add_node("generate", node_generate)
    g.add_node("validate", node_validate)
    g.add_node("fallback", node_fallback)

    g.add_conditional_edges(START, route_start, {"escalate": "escalate", "generate": "generate"})
    g.add_edge("escalate", END)
    g.add_conditional_edges("generate", route_generate, {"validate": "validate", "fallback": "fallback"})
    g.add_conditional_edges("validate", route_validate,
                            {END: END, "generate": "generate", "fallback": "fallback"})
    g.add_edge("fallback", END)

    return g.compile(checkpointer=checkpointer if checkpointer is not None else InMemorySaver())


def run_draft(graph, correlation_id: str, audience: str) -> DraftState:
    """Run one draft to completion on its own checkpoint thread."""
    initial: DraftState = {
        "attempts": 0, "feedback": None, "data": None, "issues": [], "passed": False,
        "generation_mode": "llm", "needs_human_review": False, "escalation_reason": None,
        "model": None, "usage_total": 0, "llm_unavailable": False,
    }
    return graph.invoke(initial, {"configurable": {"thread_id": f"{correlation_id}:{audience}"}})


def checkpointer_from_env():
    """
    In-memory by default: enough to resume a retry inside one process, and it
    needs no extra table.

    Set CLAIMBRIDGE_CHECKPOINT_URL to a Postgres URL and a run survives a
    worker restart as well. It is opt-in because it writes three tables of its
    own (`setup()` creates them) and because the checkpoints hold draft text,
    which belongs in the same database as the drafts themselves, not in a
    second store nobody audits.

    Note `PostgresSaver.from_conn_string` is a context manager and closes the
    connection on exit, so it cannot be used for a saver that has to outlive
    this function -- hence the explicit pool.
    """
    url = os.getenv("CLAIMBRIDGE_CHECKPOINT_URL")
    if not url:
        return InMemorySaver()

    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    global _POSTGRES_SAVER
    if _POSTGRES_SAVER is None:
        pool = ConnectionPool(conninfo=url, max_size=5, open=True,
                              kwargs={"autocommit": True, "prepare_threshold": 0,
                                      "row_factory": dict_row})
        saver = PostgresSaver(pool)
        saver.setup()                      # idempotent: creates the tables once
        _POSTGRES_SAVER = saver
        logger.info("[DRAFT] checkpointing to Postgres")
    return _POSTGRES_SAVER
