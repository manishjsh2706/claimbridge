"""
Golden eval cases - ClaimBridge
===============================

Loads the provided regression cases from resources/golden/*.jsonl and decides,
per case, whether the system can be evaluated on it yet.

The files overlap on purpose (essential-all.jsonl also contains the leakage and
adversarial cases), so cases are de-duplicated by `id`; if two files disagree
on the same id, that is a broken fixture and we fail loudly rather than pick
one silently.

A case is SKIPPED -- never silently passed -- when it needs a feature that is
not built yet. Skips are listed in the report with the reason, so the pass
rate is always "passed / executed" and the skipped work stays visible.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


class GoldenCaseError(ValueError):
    """A golden file is malformed or two files disagree about a case."""


@dataclass
class GoldenCase:
    id: str
    tenant_id: str
    claim_id: str
    audience: str
    scenario: str
    iteration_min: int
    raw: Dict[str, Any] = field(default_factory=dict)
    source_files: List[str] = field(default_factory=list)

    def get(self, key: str, default: Any = None) -> Any:
        return self.raw.get(key, default)

    @property
    def rubric_min_scores(self) -> Dict[str, int]:
        return dict(self.raw.get("rubric_min_scores") or {})


def load_golden_cases(golden_dir: Path) -> List[GoldenCase]:
    files = sorted(golden_dir.glob("*.jsonl"))
    if not files:
        raise GoldenCaseError(f"No golden files found in {golden_dir}")

    cases: Dict[str, GoldenCase] = {}
    for f in files:
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as e:
                raise GoldenCaseError(f"{f.name}:{n} is not valid JSON: {e}") from e
            for key in ("id", "tenant_id", "fixture_claim_id", "audience"):
                if not raw.get(key):
                    raise GoldenCaseError(f"{f.name}:{n} is missing '{key}'")

            existing = cases.get(raw["id"])
            if existing is not None:
                if existing.raw != raw:
                    raise GoldenCaseError(
                        f"Case '{raw['id']}' differs between {existing.source_files} and {f.name}")
                existing.source_files.append(f.name)
                continue
            cases[raw["id"]] = GoldenCase(
                id=raw["id"], tenant_id=raw["tenant_id"], claim_id=raw["fixture_claim_id"],
                audience=raw["audience"], scenario=raw.get("scenario", ""),
                iteration_min=int(raw.get("iteration_min", 1)), raw=raw, source_files=[f.name],
            )
    return sorted(cases.values(), key=lambda c: (c.iteration_min, c.tenant_id, c.id))


def skip_reason(case: GoldenCase, max_iteration: int) -> Optional[str]:
    """None when the case can run against the current system."""
    if case.iteration_min > max_iteration:
        return f"iteration_min {case.iteration_min} > --iteration {max_iteration}"
    return None
