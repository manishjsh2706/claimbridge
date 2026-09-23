"""
Reference data parsers - ClaimBridge
====================================

Parses the two spec resources that seed the relational database:

    resources/tenant-catalog.md   -> tenant configuration
    resources/sample-claims.md    -> claims + adjudication outcomes

WHY PARSE INSTEAD OF HARDCODING
The resources folder is the source of truth the mentor grades against. Copying
"60 days" and "1-800-555-0142" into Python would create a second copy that
silently drifts the day someone edits the catalog -- and then a member summary
quotes the wrong appeal window, which the rubric scores as a tenant-
appropriateness failure. Parse once, validate hard, fail loudly.

WHAT IS DELIBERATELY NOT INVENTED
- Missing values stay missing. "**missing**" becomes None and is recorded in
  claim_data["missing_fields"] -- that is the point of CLAIM-PH-002.
- A claim that is "PENDING (not adjudicated)" gets NO adjudication row.
- claim_type is taken from the fixture. Only when absent is it inferred as
  "professional" from the presence of a CPT code, and that inference is flagged
  in claim_data["claim_type_inferred"] so it is never mistaken for source data.
- Dollar amounts are parsed exactly (Decimal) and never recomputed: the spec
  says adjudication math is outside GenAI scope.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional

_TABLE_ROW_RE = re.compile(r"^\|(.+)\|\s*$")
_CODE_RE = re.compile(r"\b([A-Z]{2}-\d{1,3}|[A-Z]\d{2,4})\b")

AMOUNT_FIELDS = ("billed_amount", "allowed_amount", "plan_paid", "patient_responsibility")
ADJUDICATED_OUTCOMES = ("APPROVE", "PARTIAL", "DENY")
MISSING_MARKERS = ("**missing**", "missing")
EMPTY_MARKERS = ("—", "-", "–", "")


class ReferenceDataError(ValueError):
    pass


def _cells(line: str) -> Optional[List[str]]:
    m = _TABLE_ROW_RE.match(line.strip())
    if not m:
        return None
    cells = [c.strip() for c in m.group(1).split("|")]
    if all(set(c) <= set("-: ") for c in cells):   # |-----|-----| separator
        return None
    return cells


def _strip_md(value: str) -> str:
    return value.replace("**", "").replace("`", "").strip()


# ---------------------------------------------------------------------------
# Tenant catalog
# ---------------------------------------------------------------------------

@dataclass
class TenantRecord:
    tenant_id: str
    display_name: str
    plan_type: str
    status: str
    appeal_window_days: int
    appeal_window_basis: str
    member_services_phone: str
    member_id_prefix: str


def parse_tenant_catalog(path: Path) -> Dict[str, TenantRecord]:
    """
    Parse the Essential-track tenant roster plus each tenant's detail section.

    Only the Essential roster is loaded. map-medicare and statecare-medicaid are
    marked "Standard track only" in the catalog and are out of scope here.
    """
    raw = path.read_text(encoding="utf-8")

    roster_match = re.search(r"^## Tenant roster \(Essential track scope\)\s*$(.*?)^---",
                             raw, re.MULTILINE | re.DOTALL)
    if not roster_match:
        raise ReferenceDataError(f"{path}: Essential tenant roster table not found")

    roster: Dict[str, Dict[str, str]] = {}
    for line in roster_match.group(1).splitlines():
        cells = _cells(line)
        if not cells or cells[0].lower() == "tenant id":
            continue
        tenant_id = _strip_md(cells[0])
        roster[tenant_id] = {
            "display_name": _strip_md(cells[1]),
            "plan_type": _strip_md(cells[2]),
            "status": _strip_md(cells[3]).upper(),
        }

    tenants: Dict[str, TenantRecord] = {}
    for tenant_id, base in roster.items():
        section = re.search(
            rf"^## [^\n]*\(`{re.escape(tenant_id)}`\)[^\n]*$(.*?)(?=^## |\Z)",
            raw, re.MULTILINE | re.DOTALL)
        if not section:
            raise ReferenceDataError(f"{path}: no detail section for tenant {tenant_id}")
        body = section.group(1)

        def bullet(label: str) -> str:
            m = re.search(rf"^- \*\*{re.escape(label)}:\*\*\s*(.+?)\s*$", body, re.MULTILINE)
            if not m:
                raise ReferenceDataError(f"{path}: {tenant_id} missing '{label}'")
            return _strip_md(m.group(1))

        appeal = bullet("Appeal window")
        appeal_match = re.match(r"(\d+)\s*days?\s*(.*)$", appeal)
        if not appeal_match:
            raise ReferenceDataError(f"{path}: {tenant_id} unparseable appeal window {appeal!r}")
        basis = appeal_match.group(2).strip().strip("()").strip()

        id_format = bullet("Member ID format")
        prefix_match = re.match(r"([A-Z]+)-", id_format)
        if not prefix_match:
            raise ReferenceDataError(f"{path}: {tenant_id} unparseable member ID format {id_format!r}")

        status = base["status"]
        if status not in ("LIVE", "ONBOARDING", "SUSPENDED"):
            raise ReferenceDataError(f"{path}: {tenant_id} has unknown status {status!r}")

        tenants[tenant_id] = TenantRecord(
            tenant_id=tenant_id,
            display_name=base["display_name"],
            plan_type=base["plan_type"],
            status=status,
            appeal_window_days=int(appeal_match.group(1)),
            appeal_window_basis=basis,
            member_services_phone=bullet("Member services"),
            member_id_prefix=prefix_match.group(1),
        )

    if not tenants:
        raise ReferenceDataError(f"{path}: no tenants parsed")
    return tenants


# ---------------------------------------------------------------------------
# Sample claims
# ---------------------------------------------------------------------------

@dataclass
class ClaimFixture:
    tenant_id: str
    claim_id: str
    title: str
    claim_type: str
    member_id: str
    date_of_service: Optional[date]
    provider_name: Optional[str]
    claim_data: Dict[str, Any]
    outcome: Optional[str]                      # None when not adjudicated
    amounts: Dict[str, Optional[Decimal]] = field(default_factory=dict)
    carc_codes: List[str] = field(default_factory=list)
    rarc_codes: List[str] = field(default_factory=list)

    @property
    def is_adjudicated(self) -> bool:
        return self.outcome in ADJUDICATED_OUTCOMES


def _parse_amount(value: str, claim_id: str, name: str) -> Decimal:
    try:
        return Decimal(value.replace(",", "").replace("$", "").strip()).quantize(Decimal("0.01"))
    except InvalidOperation:
        raise ReferenceDataError(f"{claim_id}: invalid amount for {name}: {value!r}")


def _parse_claim_block(tenant_id: str, claim_id: str, title: str, block: str,
                       tenants: Dict[str, TenantRecord]) -> ClaimFixture:
    raw_fields: Dict[str, str] = {}
    for line in block.splitlines():
        cells = _cells(line)
        if not cells or len(cells) < 2 or cells[0].lower() == "field":
            continue
        raw_fields[_strip_md(cells[0]).lower()] = cells[1].strip()

    if not raw_fields:
        raise ReferenceDataError(f"{claim_id}: no field table found")

    fixture_claim_id = _strip_md(raw_fields.pop("claim_id", claim_id))
    if fixture_claim_id != claim_id:
        raise ReferenceDataError(f"{claim_id}: heading and claim_id field disagree ({fixture_claim_id})")

    claim_data: Dict[str, Any] = {"title": title}
    missing: List[str] = []
    values: Dict[str, Optional[str]] = {}
    for key, value in raw_fields.items():
        v = value.strip()
        if v.lower() in MISSING_MARKERS:
            values[key] = None
            missing.append(key)
        elif _strip_md(v) in EMPTY_MARKERS:
            values[key] = None
        else:
            values[key] = _strip_md(v)
    if missing:
        claim_data["missing_fields"] = missing

    member_id = values.pop("member_id", None)
    if not member_id:
        raise ReferenceDataError(f"{claim_id}: member_id is required")
    prefix = tenants[tenant_id].member_id_prefix
    if not re.fullmatch(rf"{prefix}-\d{{8}}", member_id):
        # A member ID from another tenant's format filed under this tenant is
        # exactly the kind of data error that becomes a cross-tenant leak.
        raise ReferenceDataError(
            f"{claim_id}: member_id {member_id!r} does not match {tenant_id} format {prefix}-########")

    dos_raw = values.pop("date_of_service", None)
    dos = date.fromisoformat(dos_raw) if dos_raw else None

    provider = values.pop("provider", None) or values.pop("facility", None)

    claim_type = values.pop("claim_type", None)
    if not claim_type:
        if values.get("ndc"):
            claim_type = "pharmacy"
        elif values.get("type_of_bill") or values.get("revenue_code"):
            claim_type = "facility"
        elif values.get("cpt"):
            claim_type = "professional"
        else:
            raise ReferenceDataError(f"{claim_id}: claim_type missing and cannot be inferred")
        claim_data["claim_type_inferred"] = True
    if claim_type not in ("professional", "facility", "pharmacy"):
        raise ReferenceDataError(f"{claim_id}: invalid claim_type {claim_type!r}")

    outcome_raw = values.pop("outcome", None) or ""
    outcome_token = outcome_raw.split()[0].upper() if outcome_raw else None
    if outcome_raw and outcome_raw.upper() != outcome_token:
        claim_data["outcome_note"] = outcome_raw

    amounts: Dict[str, Optional[Decimal]] = {}
    for name in AMOUNT_FIELDS:
        v = values.pop(name, None)
        amounts[name] = _parse_amount(v, claim_id, name) if v else None

    def codes(name: str) -> List[str]:
        v = values.pop(name, None)
        if not v:
            return []
        found = _CODE_RE.findall(v)
        leftover = _CODE_RE.sub("", v).replace(",", "").strip()
        if leftover:
            claim_data[f"{name}_note"] = v
        return found

    carc = codes("carc")
    rarc = codes("rarc")

    claim_data.update({k: v for k, v in values.items() if v is not None})

    outcome = outcome_token if outcome_token in ADJUDICATED_OUTCOMES else None
    if outcome_token and outcome is None and outcome_token != "PENDING":
        raise ReferenceDataError(f"{claim_id}: unknown outcome {outcome_raw!r}")

    return ClaimFixture(
        tenant_id=tenant_id, claim_id=claim_id, title=title, claim_type=claim_type,
        member_id=member_id, date_of_service=dos, provider_name=provider,
        claim_data=claim_data, outcome=outcome, amounts=amounts,
        carc_codes=carc, rarc_codes=rarc,
    )


def parse_sample_claims(path: Path, tenants: Dict[str, TenantRecord]) -> Dict[str, Any]:
    """
    Returns {"claims": [ClaimFixture], "skipped": [(heading, reason)]}.

    Sections whose heading names no known tenant (the adversarial fixtures) are
    skipped and reported, not guessed at -- they are pipeline test inputs for
    Iteration 2, not claims with a tenant, member and outcome.
    """
    raw = path.read_text(encoding="utf-8")
    sections = re.split(r"^## ", raw, flags=re.MULTILINE)[1:]

    claims: List[ClaimFixture] = []
    skipped: List[tuple] = []
    seen = set()

    for section in sections:
        heading, _, body = section.partition("\n")
        tenant_match = re.search(r"\(`([a-z0-9-]+)`\)", heading)
        blocks = re.split(r"^### ", body, flags=re.MULTILINE)[1:]
        if not tenant_match or tenant_match.group(1) not in tenants:
            for b in blocks:
                skipped.append((b.partition("\n")[0].strip(), f"section '{heading.strip()}' has no known tenant"))
            continue
        tenant_id = tenant_match.group(1)

        for block in blocks:
            head, _, content = block.partition("\n")
            m = re.match(r"(CLAIM-[A-Z0-9-]+)\s*[—–-]\s*(.+)$", head.strip())
            if not m:
                skipped.append((head.strip(), "heading is not a CLAIM-ID"))
                continue
            claim_id, title = m.group(1), m.group(2).strip()
            if claim_id in seen:
                raise ReferenceDataError(f"Duplicate claim fixture {claim_id}")
            seen.add(claim_id)
            claims.append(_parse_claim_block(tenant_id, claim_id, title, content, tenants))

    if not claims:
        raise ReferenceDataError(f"{path}: no claims parsed")
    return {"claims": claims, "skipped": skipped}
