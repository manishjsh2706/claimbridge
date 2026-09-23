"""
Seed tenants, claims and adjudications from the spec resources - ClaimBridge
============================================================================

    python -m src.claimbridge.scripts.seed_reference_data

Reads:
    resources/tenant-catalog.md    -> tenants
    resources/sample-claims.md     -> claims + adjudications
    resources/policies/            -> tenant policy_corpus_version (hash only;
                                      the text itself is indexed by
                                      ingest_policies into Weaviate)

Idempotent upsert: re-running updates rows in place and never duplicates.
Nothing is deleted -- a fixture removed from sample-claims.md stays in the
database until someone removes it deliberately.

Everything is parsed and validated BEFORE the first write, and all writes happen
in one transaction, so a bad fixture leaves the database exactly as it was.
"""

import logging
import sys

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from src.claimbridge.db import session_scope
from src.claimbridge.knowledge import load_tenant_corpora, resources_dir
from src.claimbridge.knowledge.reference_data import parse_sample_claims, parse_tenant_catalog
from src.claimbridge.models import Adjudication, Claim, Tenant

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("seed_reference_data")


def main() -> int:
    res = resources_dir()
    if not res.is_dir():
        logger.error(f"{res} not found -- is resources/ mounted into the container?")
        return 1

    # ---- parse + validate everything first ----------------------------------
    tenants = parse_tenant_catalog(res / "tenant-catalog.md")
    parsed = parse_sample_claims(res / "sample-claims.md", tenants)
    claims = parsed["claims"]
    corpora = load_tenant_corpora(res / "policies", res.parent, list(tenants))

    logger.info(f"Parsed {len(tenants)} tenants, {len(claims)} claims "
                f"({sum(c.is_adjudicated for c in claims)} adjudicated)")
    for heading, reason in parsed["skipped"]:
        logger.info(f"Skipped fixture '{heading}': {reason}")

    # ---- write in one transaction --------------------------------------------
    with session_scope() as session:
        for t in tenants.values():
            values = dict(
                tenant_id=t.tenant_id, display_name=t.display_name, plan_type=t.plan_type,
                status=t.status, appeal_window_days=t.appeal_window_days,
                appeal_window_basis=t.appeal_window_basis,
                member_services_phone=t.member_services_phone,
                member_id_prefix=t.member_id_prefix,
                policy_corpus_version=corpora[t.tenant_id].corpus_version if t.tenant_id in corpora else None,
            )
            # branding_tone and allow_auto_publish_approve are NOT in the catalog.
            # They keep their schema defaults ('plain', false) on insert and are
            # never overwritten here, so an operator's later setting survives a
            # re-seed.
            stmt = insert(Tenant).values(**values)
            stmt = stmt.on_conflict_do_update(
                index_elements=[Tenant.tenant_id],
                set_={**{k: v for k, v in values.items() if k != "tenant_id"}, "updated_at": func.now()},
            )
            session.execute(stmt)

        for c in claims:
            claim_values = dict(
                tenant_id=c.tenant_id, claim_id=c.claim_id, claim_type=c.claim_type,
                member_id=c.member_id, date_of_service=c.date_of_service,
                provider_name=c.provider_name, claim_data=c.claim_data, source="fixture",
            )
            # A claim that has since been submitted through the API
            # (source='api') belongs to that submission now: a re-seed must
            # never overwrite it.
            stmt = insert(Claim).values(**claim_values).on_conflict_do_update(
                constraint="pk_claims",
                set_={k: v for k, v in claim_values.items() if k not in ("tenant_id", "claim_id")},
                where=(Claim.source == "fixture"),
            )
            session.execute(stmt)

            if c.is_adjudicated:
                adj_values = dict(
                    tenant_id=c.tenant_id, claim_id=c.claim_id, outcome=c.outcome,
                    carc_codes=c.carc_codes, rarc_codes=c.rarc_codes, source="fixture",
                    **c.amounts,
                )
                stmt = insert(Adjudication).values(**adj_values).on_conflict_do_update(
                    constraint="uq_adjudications_claim",
                    set_={k: v for k, v in adj_values.items() if k not in ("tenant_id", "claim_id")},
                    where=(Adjudication.source == "fixture"),
                )
                session.execute(stmt)

    # ---- verify ----------------------------------------------------------------
    failures = []
    with session_scope() as session:
        print("\n" + "=" * 72 + "\nVERIFY - database contents\n" + "=" * 72)
        for t in tenants.values():
            row = session.get(Tenant, t.tenant_id)
            ok = (row is not None and row.appeal_window_days == t.appeal_window_days
                  and row.member_services_phone == t.member_services_phone and row.status == t.status)
            print(f"  {'PASS' if ok else 'FAIL'}  tenant {t.tenant_id:16} {row.status if row else '-':10} "
                  f"appeal={row.appeal_window_days if row else '-'}d  phone={row.member_services_phone if row else '-'}  "
                  f"corpus={row.policy_corpus_version if row else '-'}")
            if not ok:
                failures.append(f"tenant:{t.tenant_id}")

        for c in claims:
            claim = session.get(Claim, (c.tenant_id, c.claim_id))
            adj = session.execute(select(Adjudication).where(
                Adjudication.tenant_id == c.tenant_id, Adjudication.claim_id == c.claim_id)).scalar_one_or_none()
            ok = claim is not None and (adj is not None) == c.is_adjudicated
            if ok and adj is not None:
                ok = adj.outcome == c.outcome and adj.patient_responsibility == c.amounts["patient_responsibility"]
            detail = (f"{adj.outcome:8} owes={adj.patient_responsibility} carc={adj.carc_codes}"
                      if adj else "not adjudicated")
            print(f"  {'PASS' if ok else 'FAIL'}  claim  {c.tenant_id:16} {c.claim_id:17} {detail}")
            if not ok:
                failures.append(f"claim:{c.claim_id}")

    print("=" * 72)
    if failures:
        print(f"SEED COMPLETE WITH {len(failures)} FAILED CHECK(S): {failures}")
        return 1
    print(f"SEED COMPLETE - {len(tenants)} tenants, {len(claims)} claims, "
          f"{sum(c.is_adjudicated for c in claims)} adjudications, all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
