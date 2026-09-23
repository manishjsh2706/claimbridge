"""
Evals package - ClaimBridge.

Runs the provided golden cases (resources/golden/*.jsonl) against the API and
scores each output two ways:

    checks  exact, code-level: outcome, required citations, forbidden phrases,
            foreign-tenant sources. Same answer every run.
    judge   LLM-as-judge against the member-summary rubric, plus a
            faithfulness pass that lists any statement no source supports
            (the "in-network provider" class of error the guards cannot see).

Entry point: python -m src.claimbridge.scripts.run_golden_eval
"""

from .checks import CheckResult, run_checks
from .golden import GoldenCase, GoldenCaseError, load_golden_cases, skip_reason
from .judge import JUDGE_PROMPT_VERSION, RUBRIC_DIMENSIONS, judge_summary, rubric_verdict

__all__ = [
    "GoldenCase", "GoldenCaseError", "load_golden_cases", "skip_reason",
    "CheckResult", "run_checks",
    "JUDGE_PROMPT_VERSION", "RUBRIC_DIMENSIONS", "judge_summary", "rubric_verdict",
]
