"""
Index verification with EXPLAIN ANALYZE - ClaimBridge
=====================================================

    docker compose exec claimbridge python -m src.claimbridge.scripts.index_check --rows 10000
    docker compose exec claimbridge python -m src.claimbridge.scripts.index_check --cleanup

An index only matters at volume: with 50 rows Postgres reads the whole table
because that is genuinely faster. So this loads a THROWAWAY tenant
("loadtest-plan") with synthetic claims, communications and recommendations,
runs ANALYZE, and then EXPLAIN (ANALYZE) on the real Iteration 2 queries:

    review queue          communications (tenant_id, status, created_at)
    latest recommendation recommendations (tenant_id, claim_id, created_at)
    claims by member      claims (tenant_id, member_id)

Each query PASSES only if the plan uses an index scan for that table.

NOT covered: audit_events. Its rows cannot be deleted (append-only trigger),
so load-testing it would leave permanent synthetic rows in the audit trail.
That is the trade-off the trigger buys, and it is the right one.

The data lives under one tenant id and `--cleanup` removes exactly that
tenant (claims cascade to their children). No other tenant is touched.
"""

import argparse
import sys
from typing import List, Tuple

from sqlalchemy import text

from src.claimbridge.db import session_scope

TENANT = "loadtest-plan"

QUERIES: List[Tuple[str, str, str, dict]] = [
    ("review queue", "communications",
     """SELECT id, claim_id, audience, status, created_at FROM communications
        WHERE tenant_id = :t AND status = 'PENDING_REVIEW'
        ORDER BY created_at, id LIMIT 50""", {}),
    ("latest recommendation for one claim", "recommendations",
     """SELECT id, recommendation FROM recommendations
        WHERE tenant_id = :t AND claim_id = :c
        ORDER BY created_at DESC, id DESC LIMIT 1""", {"c": "LOAD-000500"}),
    ("claims by member", "claims",
     """SELECT claim_id, date_of_service FROM claims
        WHERE tenant_id = :t AND member_id = :m""", {"m": "LT-00000500"}),
]


def seed(rows: int) -> None:
    with session_scope() as s:
        s.execute(text("""
            INSERT INTO tenants (tenant_id, display_name, plan_type, status, appeal_window_days,
                                 appeal_window_basis, member_services_phone, member_id_prefix)
            VALUES (:t, 'Load Test Plan', 'synthetic', 'LIVE', 30, 'from EOB date', '1-800-000-0000', 'LT')
            ON CONFLICT (tenant_id) DO NOTHING"""), {"t": TENANT})
        s.execute(text("""
            INSERT INTO claims (tenant_id, claim_id, claim_type, member_id, date_of_service,
                                provider_name, claim_data, source, intake_status)
            SELECT :t, 'LOAD-' || lpad(g::text, 6, '0'), 'professional',
                   'LT-' || lpad(g::text, 8, '0'), DATE '2025-01-01' + (g % 300),
                   'Load Test Clinic', '{}'::jsonb, 'loadtest', 'VALIDATED'
            FROM generate_series(1, :n) g
            ON CONFLICT DO NOTHING"""), {"t": TENANT, "n": rows})
        s.execute(text("""
            INSERT INTO communications (tenant_id, claim_id, audience, status, content, citations,
                                        correlation_id, created_by, approved_by, approved_at, created_at)
            SELECT :t, 'LOAD-' || lpad(g::text, 6, '0'), 'member',
                   CASE WHEN g %% 10 = 0 THEN 'PENDING_REVIEW' ELSE 'DRAFT' END,
                   '{}'::jsonb, '[]'::jsonb, 'loadtest', 'loadtest', NULL, NULL,
                   now() - (g || ' seconds')::interval
            FROM generate_series(1, :n) g
            ON CONFLICT DO NOTHING""".replace("%%", "%")), {"t": TENANT, "n": rows})
        s.execute(text("""
            INSERT INTO recommendations (tenant_id, claim_id, recommendation, rationale, rules_version,
                                         input_hash, created_by, correlation_id, created_at)
            SELECT :t, 'LOAD-' || lpad(g::text, 6, '0'), 'APPROVE', 'load test', 'loadtest',
                   md5(g::text), 'loadtest', 'loadtest', now() - (g || ' seconds')::interval
            FROM generate_series(1, :n) g"""), {"t": TENANT, "n": rows})
        for t in ("claims", "communications", "recommendations"):
            s.execute(text(f"ANALYZE {t}"))
    print(f"seeded {rows} claims, {rows} communications, {rows} recommendations under tenant '{TENANT}'")


def counts() -> dict:
    with session_scope() as s:
        return {t: s.execute(text(f"SELECT count(*) FROM {t} WHERE tenant_id = :t"), {"t": TENANT}).scalar()
                for t in ("claims", "communications", "recommendations")}


def explain() -> int:
    failures = []
    with session_scope() as s:
        for label, table, sql, params in QUERIES:
            plan = "\n".join(r[0] for r in s.execute(text("EXPLAIN (ANALYZE, BUFFERS) " + sql),
                                                     {"t": TENANT, **params}))
            used_index = "Index Scan" in plan or "Index Only Scan" in plan or "Bitmap Index Scan" in plan
            seq_scan = f"Seq Scan on {table}" in plan
            ok = used_index and not seq_scan
            print("\n" + "=" * 78 + f"\n{'PASS' if ok else 'FAIL'}  {label}  ({table})\n" + "=" * 78)
            print(plan)
            if not ok:
                failures.append(label)
    print("\n" + "=" * 78)
    print(f"{len(QUERIES) - len(failures)}/{len(QUERIES)} queries use an index" +
          (f"  FAILED: {failures}" if failures else ""))
    print("audit_events is not load-tested on purpose: its rows cannot be deleted (append-only trigger).")
    return 1 if failures else 0


def cleanup() -> int:
    before = counts()
    with session_scope() as s:
        # claims cascade to communications, recommendations and adjudications
        s.execute(text("DELETE FROM claims WHERE tenant_id = :t"), {"t": TENANT})
        s.execute(text("DELETE FROM tenants WHERE tenant_id = :t"), {"t": TENANT})
    after = counts()
    print(f"removed tenant '{TENANT}': {before} -> {after}")
    return 0 if not any(after.values()) else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Verify Iteration 2 indexes with EXPLAIN ANALYZE")
    p.add_argument("--rows", type=int, default=10000)
    p.add_argument("--cleanup", action="store_true", help="delete the load-test tenant and its rows")
    args = p.parse_args(argv)
    if args.cleanup:
        return cleanup()
    seed(args.rows)
    return explain()


if __name__ == "__main__":
    sys.exit(main())
