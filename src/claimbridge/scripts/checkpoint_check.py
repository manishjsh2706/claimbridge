"""
Postgres checkpoint verification - ClaimBridge
=============================================

Proves the claim made in summaries/graph.py: that a draft run which dies
part-way resumes at the node it died on, in a DIFFERENT process, without
calling the model again.

    # 1. generate succeeds, then the run dies before the draft is validated
    docker compose exec -e CLAIMBRIDGE_CHECKPOINT_URL=$DB claimbridge \
        python -m src.claimbridge.scripts.checkpoint_check --phase crash

    # 2. a fresh process resumes the same thread
    docker compose exec -e CLAIMBRIDGE_CHECKPOINT_URL=$DB claimbridge \
        python -m src.claimbridge.scripts.checkpoint_check --phase resume

Phase 2 installs a `generate` that raises if it is ever called. If the resume
is real, it never is, and the run finishes from the checkpoint alone. If
checkpointing were not working, phase 2 would either call the model again (and
blow up loudly) or start from scratch -- both fail the check.

Two processes, not two calls in one process: an in-memory checkpointer would
pass a single-process test and prove nothing about surviving a restart.

This uses scripted callables, not a real claim: the question here is whether
the checkpointer persists and resumes, which has nothing to do with what the
summary says. The wording is covered by the golden eval.

The tables (checkpoints, checkpoint_blobs, checkpoint_writes,
checkpoint_migrations) are LangGraph's own and are created by its `setup()`,
not by an Alembic migration -- they belong to the library, and pinning them in
our migration history would mean re-creating them by hand on every upgrade.
"""

import argparse
import os
import sys

from src.claimbridge.summaries.graph import build_draft_graph, checkpointer_from_env

THREAD = "checkpoint-check"
DRAFT = {"kind": "draft-from-phase-1"}


class Crash(RuntimeError):
    """Stands in for the process dying: a deploy, an OOM kill, a lost database."""


def _graph(generate, validate, checkpointer):
    return build_draft_graph(
        generate=generate,
        validate=validate,
        template=lambda: {"kind": "template"},
        needs_escalation=lambda: False,
        checkpointer=checkpointer,
    )


def phase_crash(checkpointer, config):
    def generate(feedback):
        print("  generate  ran (this is the expensive call: a real one costs money and ~6s)")
        return {"model": "fake-model", "usage": {"total_tokens": 1234}, "data": DRAFT}

    def validate(draft, require_policy_citation):
        raise Crash("process died before the draft could be validated")

    graph = _graph(generate, validate, checkpointer)
    initial = {"attempts": 0, "feedback": None, "data": None, "issues": [], "passed": False,
               "generation_mode": "llm", "needs_human_review": False, "escalation_reason": None,
               "model": None, "usage_total": 0, "llm_unavailable": False}
    try:
        graph.invoke(initial, config)
    except Crash as e:
        print(f"  crashed   {e}")
        return 0
    print("  FAIL: the run was supposed to die and did not")
    return 1


def phase_resume(checkpointer, config):
    def generate(feedback):
        raise AssertionError("generate was called again -- the checkpoint was NOT used")

    def validate(draft, require_policy_citation):
        print("  validate  ran (the node that died last time)")
        return []

    graph = _graph(generate, validate, checkpointer)
    state = graph.get_state(config)
    if not state.values:
        print("  FAIL: no checkpoint found for this thread. Run --phase crash first,")
        print("        with CLAIMBRIDGE_CHECKPOINT_URL pointing at the same database.")
        return 1
    print(f"  resuming  from checkpoint, next node: {state.next}")

    final = graph.invoke(None, config)          # None = continue, do not restart

    checks = [
        ("generate was not called again", True),   # an AssertionError above would have stopped us
        ("the draft from the first process survived", final["data"] == DRAFT),
        ("its token count survived", final["usage_total"] == 1234),
        ("the run completed", final["passed"] is True),
    ]
    ok = True
    for label, passed in checks:
        print(f"  {'PASS' if passed else 'FAIL'}  {label}")
        ok = ok and passed
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", choices=("crash", "resume"), required=True)
    ap.add_argument("--thread", default=THREAD)
    args = ap.parse_args(argv)

    url = os.getenv("CLAIMBRIDGE_CHECKPOINT_URL")
    if not url:
        print("CLAIMBRIDGE_CHECKPOINT_URL is not set, so this would run on the in-memory")
        print("checkpointer, which cannot survive a process restart and would prove nothing.")
        return 2
    print(f"checkpointer: Postgres  thread: {args.thread}")

    checkpointer = checkpointer_from_env()
    config = {"configurable": {"thread_id": args.thread}}
    rc = phase_crash(checkpointer, config) if args.phase == "crash" else phase_resume(checkpointer, config)
    print("OK" if rc == 0 else "CHECK FAILED")
    return rc


if __name__ == "__main__":
    sys.exit(main())
