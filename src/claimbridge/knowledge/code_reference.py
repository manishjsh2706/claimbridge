"""
CARC / RARC code reference - ClaimBridge
========================================

Loads resources/carc-rarc-reference.md into an exact-match lookup.

WHY A LOOKUP AND NOT VECTOR SEARCH
A claim arrives with its adjustment codes already attached: CARC CO-45, RARC
N290. The question is never "which code is semantically similar to this
claim?" -- it is "what does CO-45 mean?". That is a dictionary lookup.

Putting codes in a vector index would make the answer probabilistic: a query
for CO-45 could rank CO-50 above it, and the member would be told their claim
was denied as non-covered when it was actually a fee-schedule reduction. The
rubric scores a wrong denial reason as a 1 on accuracy -- an automatic fail.
Exact codes deserve exact retrieval.

The codes are shared across tenants (the onboarding checklist calls it the
"shared CARC/RARC reference"), so this module is deliberately NOT tenant-scoped.
Tenant-specific wording (appeal windows, phone numbers) comes from tenant
config, not from here.

UNKNOWN CODES
get_code() returns None for a code that is not in the reference. Callers must
treat that as "cannot explain this code from an approved source" -- never ask
the model to guess what an unlisted code means. The grounding rule is explicit:
no "why" statement without a retrievable source.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_CODE_HEADING_RE = re.compile(r"^###\s+([A-Z]{1,2}-?\d+|[A-Z]\d+)\s+[—-]\s+(.+?)\s*$", re.MULTILINE)
_ROW_RE = re.compile(r"^\|\s*\*\*(.+?)\*\*\s*\|\s*(.+?)\s*\|\s*$", re.MULTILINE)
_RULE_RE = re.compile(r"^\d+\.\s+\*\*(.+?)\*\*\s+[—-]\s+(.+?)\s*$", re.MULTILINE)


class CodeReferenceError(ValueError):
    pass


@dataclass
class CodeDefinition:
    code: str               # "CO-45", "N290"
    kind: str               # "CARC" | "RARC"
    title: str              # "Charges exceed fee schedule / maximum allowable"
    fields: Dict[str, str] = field(default_factory=dict)

    @property
    def member_friendly_name(self) -> str:
        return self.fields.get("member_friendly_name", self.title)

    def citation(self) -> Dict[str, str]:
        """Citation in the format recommended by the reference document."""
        return {"source_type": "carc_definition" if self.kind == "CARC" else "rarc_definition",
                "code": self.code, "label": self.title}

    def as_prompt_text(self) -> str:
        lines = [f"[{self.kind} {self.code}] {self.title}"]
        for key, value in self.fields.items():
            lines.append(f"  {key.replace('_', ' ')}: {value}")
        return "\n".join(lines)


def _snake(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")


def normalize_code(code: str) -> str:
    """'co45', ' CO-45 ', 'CO 45' -> 'CO-45'. RARCs like 'n290' -> 'N290'."""
    c = re.sub(r"\s+", "", code or "").upper()
    m = re.fullmatch(r"([A-Z]{2})-?(\d+)", c)
    return f"{m.group(1)}-{m.group(2)}" if m else c


class CodeReference:
    def __init__(self, codes: Dict[str, CodeDefinition], summarization_rules: List[str]):
        self._codes = codes
        self.summarization_rules = summarization_rules

    def get_code(self, code: str) -> Optional[CodeDefinition]:
        return self._codes.get(normalize_code(code))

    def resolve(self, codes: List[str]) -> Dict[str, List]:
        """
        Split a claim's codes into known definitions and unknown codes.

        Unknown codes are returned, not dropped, so the caller can surface them
        ("we could not explain code X from approved sources") and route to review.
        """
        known, unknown = [], []
        for raw in codes or []:
            if not raw or raw.strip() in ("—", "-"):
                continue
            definition = self.get_code(raw)
            (known if definition else unknown).append(definition or normalize_code(raw))
        return {"known": known, "unknown": unknown}

    def __len__(self) -> int:
        return len(self._codes)

    def codes(self) -> List[str]:
        return sorted(self._codes)


def load_code_reference(path: Path) -> CodeReference:
    if not path.is_file():
        raise CodeReferenceError(f"CARC/RARC reference not found: {path}")
    raw = path.read_text(encoding="utf-8")

    # Walk "## Common CARC codes" / "## Common RARC codes" sections so each
    # code knows which family it belongs to.
    sections = [(m.group(1), m.start()) for m in _SECTION_RE.finditer(raw)]
    sections.append(("END", len(raw)))

    codes: Dict[str, CodeDefinition] = {}
    rules: List[str] = []

    for (name, start), (_, end) in zip(sections, sections[1:]):
        text = raw[start:end]
        lower = name.lower()

        if "summarization rules" in lower:
            rules = [f"{t}: {d}" for t, d in _RULE_RE.findall(text)]
            continue

        if "carc" in lower:
            kind = "CARC"
        elif "rarc" in lower:
            kind = "RARC"
        else:
            continue

        headings = list(_CODE_HEADING_RE.finditer(text))
        for i, h in enumerate(headings):
            block = text[h.end(): headings[i + 1].start() if i + 1 < len(headings) else len(text)]
            code = normalize_code(h.group(1))
            fields = {_snake(k): v.strip() for k, v in _ROW_RE.findall(block)}
            if code in codes:
                raise CodeReferenceError(f"Duplicate code in reference: {code}")
            codes[code] = CodeDefinition(code=code, kind=kind, title=h.group(2).strip(), fields=fields)

    if not codes:
        raise CodeReferenceError(f"No codes parsed from {path}")
    return CodeReference(codes, rules)
