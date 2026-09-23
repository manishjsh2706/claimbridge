"""
Ingest the provided tenant policies into Weaviate - ClaimBridge
===============================================================

Replaces the invented sample data (hdfc-life / axa-insurance) with the corpus
the spec actually provides: resources/policies/{tenant_id}/*.md for pacific-hmo,
coastal-ppo and summit-employer.

    python -m src.claimbridge.scripts.ingest_policies
    python -m src.claimbridge.scripts.ingest_policies --tenants pacific-hmo
    python -m src.claimbridge.scripts.ingest_policies --keep-legacy

What it does, in order:
  1. Parse every tenant's policy documents into section chunks (fails fast on a
     mis-filed document -- see knowledge/policy_parser.py)
  2. Parse the shared CARC/RARC reference (not indexed -- exact lookup)
  3. Drop the legacy sample collections (unless --keep-legacy)
  4. Create the PolicyChunk collection if missing
  5. Replace each tenant's chunks (idempotent: safe to re-run)
  6. VERIFY -- and exit non-zero if anything fails:
       a. every tenant's indexed count equals its parsed count
       b. zero cross-tenant leakage: each tenant's results contain only its own
          doc_keys (the golden "forbidden_doc_prefixes" check)
       c. relevance: for queries shaped like the golden scenarios, the section the
          golden case requires appears in the top 3

Summit Employer is ingested even though it is ONBOARDING: the onboarding
checklist explicitly calls for ingesting its corpus and running evals before
go-live. Serving it to members is blocked at the API layer by tenant status,
not by keeping its documents out of the index.
"""

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.claimbridge.knowledge import load_code_reference, load_tenant_corpora, resources_dir
from src.claimbridge.vectorstore import WeaviateClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("ingest_policies")

# Relevance probes derived from the golden cases in resources/golden/.
# (tenant, query shaped like the claim, section the golden case requires)
RELEVANCE_PROBES = [
    ("pacific-hmo", "MRI lumbar spine 72148 denied, no prior authorization on file, CO-197",
     "prior-auth.imaging"),                                    # golden ph-004-member
    ("pacific-hmo", "office visit billed above the allowed amount, charge reduced CO-45",
     "fee-schedule.allowed-amounts"),                          # golden ph-001-member
    # Includes the CO-45 definition wording ("provider may balance bill"), because
    # the pipeline enriches its retrieval query with the meaning of each code on
    # the claim -- a bare claim line rarely uses the policy's own vocabulary.
    ("coastal-ppo", "out-of-network surgeon charged above allowed amount CO-45, "
                    "provider may balance bill the member",
     "oon.balance-billing"),                                   # golden cp-001-member
    ("coastal-ppo", "cosmetic dermatology procedure 11900 not covered, CO-50",
     "exclusions.cosmetic"),                                   # golden cp-002-provider
    ("summit-employer", "immunization claim missing diagnosis, incomplete billing data CO-16",
     "billing.completeness"),                                  # golden se-001-onboarding
]

LEAKAGE_PROBE = "member appeal deadline and member services phone number"
TOP_K_RELEVANCE = 3


def _search(client, query, tenant_id, limit, failures, label):
    """search_policies raises on an outage; report it as a failed check, not a traceback."""
    try:
        return client.search_policies(query, tenant_id=tenant_id, limit=limit)
    except Exception as e:
        print(f"  FAIL  {tenant_id:16} search failed: {e}")
        failures.append(label)
        return []


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest tenant policies into Weaviate")
    parser.add_argument("--tenants", nargs="*", help="Only these tenant IDs (default: all)")
    parser.add_argument("--keep-legacy", action="store_true",
                        help="Do not drop the old sample-data collections")
    args = parser.parse_args()

    res = resources_dir()
    project_root = res.parent
    logger.info(f"Resources directory: {res}")
    if not res.is_dir():
        logger.error(
            f"{res} not found. Inside Docker, resources/ must be mounted -- "
            "check the claimbridge volumes in docker-compose.yml."
        )
        return 1

    # 1-2. Parse everything BEFORE touching the database, so a malformed
    # document aborts the run without leaving the index half-updated.
    corpora = load_tenant_corpora(res / "policies", project_root, args.tenants)
    codes = load_code_reference(res / "carc-rarc-reference.md")
    logger.info(f"Parsed CARC/RARC reference: {len(codes)} codes {codes.codes()}")
    for tid, c in corpora.items():
        logger.info(f"Parsed {tid}: {len(c.chunks)} sections from {len(c.files)} files "
                    f"(corpus_version={c.corpus_version})")

    if not corpora:
        logger.error("No tenant corpora found")
        return 1

    failures = []
    ingested_at = datetime.now(timezone.utc).isoformat()

    with WeaviateClient() as client:
        # 3. Legacy data
        if args.keep_legacy:
            logger.info("Keeping legacy collections (--keep-legacy)")
        else:
            dropped = client.drop_legacy_collections()
            logger.info(f"Dropped legacy collections: {dropped or 'none present'}")

        # 4-5. Schema + data
        client.create_policy_collection()
        for tid, corpus in corpora.items():
            objects = [ch.to_properties(corpus.corpus_version, ingested_at) for ch in corpus.chunks]
            client.replace_tenant_policies(tid, objects)

        # 6a. Counts
        print("\n" + "=" * 72 + "\nVERIFY 1/3 - indexed counts\n" + "=" * 72)
        for tid, corpus in corpora.items():
            n = client.count_tenant_policies(tid)
            ok = n == len(corpus.chunks)
            print(f"  {'PASS' if ok else 'FAIL'}  {tid:16} indexed={n}  expected={len(corpus.chunks)}")
            if not ok:
                failures.append(f"count:{tid}")

        # 6b. Leakage
        print("\n" + "=" * 72 + "\nVERIFY 2/3 - cross-tenant leakage (must be 0)\n" + "=" * 72)
        for tid in corpora:
            results = _search(client, LEAKAGE_PROBE, tid, 10, failures, f"leakage:{tid}")
            foreign = [r["doc_key"] for r in results
                       if r["tenant_id"] != tid or not r["doc_key"].startswith(tid)]
            ok = bool(results) and not foreign
            print(f"  {'PASS' if ok else 'FAIL'}  {tid:16} results={len(results)}  foreign={len(foreign)}"
                  + (f"  {foreign}" if foreign else ""))
            if not ok:
                failures.append(f"leakage:{tid}")

        # 6c. Relevance
        print("\n" + "=" * 72 + f"\nVERIFY 3/3 - golden sections in top {TOP_K_RELEVANCE}\n" + "=" * 72)
        for tid, query, expected in RELEVANCE_PROBES:
            if tid not in corpora:
                continue
            results = _search(client, query, tid, 5, failures, f"relevance:{tid}:{expected}")
            top = [r["section_path"] for r in results[:TOP_K_RELEVANCE]]
            ok = expected in top
            rank = top.index(expected) + 1 if ok else None
            print(f"  {'PASS' if ok else 'FAIL'}  {tid:16} expect {expected:30} "
                  f"{'rank ' + str(rank) if ok else 'NOT IN TOP ' + str(TOP_K_RELEVANCE)}")
            print(f"        query: {query}")
            for i, r in enumerate(results[:TOP_K_RELEVANCE], 1):
                print(f"          {i}. {r['doc_key']:32} {r['section_path']:32} score={r['similarity_score']:.3f}")
            if not ok:
                failures.append(f"relevance:{tid}:{expected}")

    print("\n" + "=" * 72)
    if failures:
        print(f"INGEST COMPLETE WITH {len(failures)} FAILED CHECK(S): {failures}")
        print("=" * 72)
        return 1
    total = sum(len(c.chunks) for c in corpora.values())
    print(f"INGEST COMPLETE - {total} sections across {len(corpora)} tenants, all checks passed")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
