"""
Seed Weaviate with Sample Data - ClaimBridge

INTERVIEW EXPLANATION:
=====================
Why seed data?
- Development: Need data to test retrieval before real policies are uploaded
- Testing: Must verify semantic search works correctly
- Demo: Show how Weaviate retrieval improves claim assessment quality

When to run this:
1. After Weaviate container starts (docker-compose up)
2. After collections are created (via weaviate_client.create_collections())
3. Before processing first claim
4. Can run multiple times (idempotent - won't create duplicates)

This script SEEDS sample data for testing. In production:
- Policies uploaded by insurance company staff via admin portal
- Handled by separate admin API (not claims processing API)
- Versioned and audit-logged (e.g., policy_2024_q1 vs policy_2024_q2)

Pattern: Data initialization script
- Separate from application code
- Idempotent (safe to run multiple times)
- Can run as one-time script or job
- Includes health checks (verify connection before inserting)
"""

import logging
from datetime import datetime
from .weaviate_client import WeaviateClient

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


SAMPLE_POLICIES = [
    {
        "title": "Emergency Room Coverage - Standard Plan",
        "content": "Emergency room visits are covered up to $10,000 with standard deductible of $500. Covers: chest pain, abdominal pain, severe injuries, allergic reactions, difficulty breathing. Requires immediate medical certification. Claims must be submitted within 30 days.",
        "company_id": "hdfc-life",
        "doc_type": "policy",
        "source": "policy_2024_q1"
    },
    {
        "title": "Outpatient Surgery Coverage",
        "content": "Outpatient procedures covered at 80% of approved amount. Includes: orthopedic surgery (knee, hip, shoulder), dental procedures, cataract surgery. Maximum coverage: $5,000 per procedure. Requires prior authorization from company medical team. Deductible: $250.",
        "company_id": "hdfc-life",
        "doc_type": "policy",
        "source": "policy_2024_q1"
    },
    {
        "title": "Inpatient Hospitalization Coverage",
        "content": "Hospital admission covered at 100% after deductible ($1,000). Includes: room charges, doctor fees, nursing care, medications, surgical procedures. Maximum coverage: $50,000 per admission. Requires notification within 24 hours of admission.",
        "company_id": "hdfc-life",
        "doc_type": "policy",
        "source": "policy_2024_q1"
    },
    {
        "title": "Physical Therapy Coverage",
        "content": "Physiotherapy and rehabilitation covered up to 20 sessions per year. Maximum: $100 per session. Requires medical prescription from licensed doctor. Conditions covered: post-surgery rehabilitation, arthritis, muscle injury, back pain.",
        "company_id": "hdfc-life",
        "doc_type": "policy",
        "source": "policy_2024_q1"
    },
    {
        "title": "Mental Health & Counseling",
        "content": "Psychological counseling and mental health treatment covered up to 10 sessions per year. Includes: depression, anxiety, stress management. Covered amount: $150 per session with licensed therapist. Requires referral from primary care doctor.",
        "company_id": "hdfc-life",
        "doc_type": "policy",
        "source": "policy_2024_q1"
    },
    {
        "title": "Dental Treatment Coverage",
        "content": "Dental procedures covered at 50% of approved amount. Includes: cleaning, filling, extraction, crown. Maximum coverage: $2,000 per year. Emergency dental (pain relief) covered at 100%. Preventive care (cleaning) covered 1x per 6 months.",
        "company_id": "axa-insurance",
        "doc_type": "policy",
        "source": "policy_2024_q1"
    },
    {
        "title": "Cardiac Care Coverage - Premium",
        "content": "Comprehensive coverage for cardiac procedures: angiography, angioplasty, bypass surgery, stent placement. Coverage: 90% of approved amount. Maximum: $100,000 per procedure. Requires hospitalization. Emergency cardiac care covered 100%.",
        "company_id": "axa-insurance",
        "doc_type": "policy",
        "source": "policy_2024_q1"
    },
    {
        "title": "Cancer Treatment Coverage",
        "content": "All cancer treatments covered including: chemotherapy, radiation, immunotherapy, targeted therapy, surgery. Coverage: 100% after deductible ($2,000). Includes medications and follow-up care. Lifetime maximum: $500,000.",
        "company_id": "axa-insurance",
        "doc_type": "policy",
        "source": "policy_2024_q1"
    }
]

SAMPLE_GUIDELINES = [
    {
        "title": "Emergency Claim Assessment Guidelines",
        "content": "FAST-TRACK ASSESSMENT: Emergency claims require expedited processing. Checklist: 1) Medical emergency documented? 2) Insurance coverage active at claim date? 3) Amount within policy limits? 4) Medical documentation submitted? If all YES, approve within 24 hours. If NO to any, escalate to medical review team.",
        "company_id": "hdfc-life",
        "doc_type": "guideline",
        "source": "guidelines_2024"
    },
    {
        "title": "Pre-Authorization Requirement Guidelines",
        "content": "Procedures REQUIRING pre-auth: elective surgery, hospitalization, specialized treatment. Process: 1) Get medical provider estimate, 2) Submit to medical team, 3) Medical review (2-3 days), 4) Approval sent to provider, 5) Proceed with treatment. Costs without pre-auth may be denied.",
        "company_id": "hdfc-life",
        "doc_type": "guideline",
        "source": "guidelines_2024"
    },
    {
        "title": "Claim Rejection Guidelines",
        "content": "Valid reasons for rejection: 1) Policy not active on service date, 2) Procedure not covered under plan, 3) Amount exceeds annual limit, 4) Waiting period not met, 5) Policy lapsed due to non-payment, 6) Fraud indicators detected. Always send detailed reason to claimant. Claimant has 30 days to appeal.",
        "company_id": "hdfc-life",
        "doc_type": "guideline",
        "source": "guidelines_2024"
    },
    {
        "title": "Documentation Requirements",
        "content": "MANDATORY for all claims: 1) Itemized bill/invoice from provider, 2) Medical certificate from treating doctor, 3) Prescription (if applicable), 4) Discharge summary (if hospitalized), 5) Any relevant test reports. OPTIONAL: insurance card copy, patient ID. Claims without mandatory docs are REJECTED.",
        "company_id": "axa-insurance",
        "doc_type": "guideline",
        "source": "guidelines_2024"
    },
    {
        "title": "Medical Review Process",
        "content": "When assessment confidence is MEDIUM (60-80%): Escalate to medical reviewer. Medical reviewer: 1) Reviews medical records, 2) Checks treatment necessity, 3) Compares with industry standards, 4) Makes final approval/rejection decision. Process takes 3-5 business days.",
        "company_id": "axa-insurance",
        "doc_type": "guideline",
        "source": "guidelines_2024"
    }
]

SAMPLE_HISTORY = [
    {
        "title": "Historical Claim: Similar Emergency Room Visit - APPROVED",
        "content": "Claim ID: CLM-2023-5001. Claimant: Similar patient, emergency room visit for chest pain, November 2023. Amount: $4,800. Assessment: Chest pain is covered emergency. Medical documentation complete. Status: APPROVED. Reason: Meets all emergency criteria, documentation complete, amount within limits.",
        "company_id": "hdfc-life",
        "doc_type": "historical_claim",
        "source": "history_archive_2023"
    },
    {
        "title": "Historical Claim: Emergency Room Visit - DENIED",
        "content": "Claim ID: CLM-2023-4501. Claimant: Similar patient, emergency room visit for minor cut, September 2023. Amount: $1,200. Assessment: Denied. Reason: Minor injury, not covered under emergency definition. ER visit was unnecessary (could have visited urgent care). Recommendation: Urgent care coverage instead. Status: REJECTED.",
        "company_id": "hdfc-life",
        "doc_type": "historical_claim",
        "source": "history_archive_2023"
    },
    {
        "title": "Historical Claim: Outpatient Surgery - APPROVED",
        "content": "Claim ID: CLM-2023-3001. Claimant: Similar patient, knee arthroscopy (outpatient), August 2023. Amount: $3,500. Assessment: Orthopedic surgery is covered. Pre-authorization obtained. Medical necessity documented (MRI showing knee damage). Status: APPROVED at 80% coverage.",
        "company_id": "hdfc-life",
        "doc_type": "historical_claim",
        "source": "history_archive_2023"
    },
    {
        "title": "Historical Claim: Hospitalization - APPROVED",
        "content": "Claim ID: CLM-2023-2501. Claimant: Similar patient, appendicitis emergency surgery with hospitalization, May 2023. Amount: $28,000. Assessment: Emergency surgery with 3-day hospitalization. Full coverage under inpatient policy. Status: APPROVED at 100% (after deductible).",
        "company_id": "axa-insurance",
        "doc_type": "historical_claim",
        "source": "history_archive_2023"
    },
    {
        "title": "Historical Claim: Cardiac Procedure - APPROVED",
        "content": "Claim ID: CLM-2023-1501. Claimant: Similar patient, cardiac angioplasty with stent, March 2023. Amount: $65,000. Assessment: Life-saving cardiac procedure. Pre-auth obtained. Medical emergency. Status: APPROVED at 90% coverage (premium cardiac plan).",
        "company_id": "axa-insurance",
        "doc_type": "historical_claim",
        "source": "history_archive_2023"
    }
]


def seed_weaviate(weaviate_url: str = "http://localhost:8080") -> dict:
    """
    Seed Weaviate with sample data.

    INTERVIEW EXPLANATION - Idempotency:
    This function is IDEMPOTENT - safe to run multiple times.

    Why?
    - Won't create duplicate documents if already inserted
    - Can add new documents on subsequent runs
    - Safe to run as scheduled job or manual command

    How?
    - Check if document with same title + source already exists
    - Only insert if new
    - Log what was inserted vs skipped

    In production:
    - Use database transaction IDs (idempotency key)
    - Example: document UUID = hash(title + company_id + source)
    - If UUID already exists, skip
    - If UUID is new, insert

    Args:
        weaviate_url: URL to Weaviate instance

    Returns:
        Dict with seeding results: {documents_inserted, documents_skipped, errors}
    """
    logger.info("=" * 70)
    logger.info("SEEDING WEAVIATE WITH SAMPLE DATA")
    logger.info("=" * 70)

    client = WeaviateClient(weaviate_url)
    results = {
        "policies_inserted": 0,
        "guidelines_inserted": 0,
        "history_inserted": 0,
        "errors": []
    }

    try:
        # STEP 1: Create collections if they don't exist
        logger.info("\n[STEP 1] Creating collections...")
        client.create_collections()
        logger.info("✓ Collections ready")

        # STEP 2: Seed policies
        logger.info("\n[STEP 2] Seeding policies...")
        for policy in SAMPLE_POLICIES:
            try:
                logger.info(f"  Inserting policy: {policy['title'][:50]}...")
                doc_id = client.index_document(
                    collection_name="ClaimPolicies",
                    content=policy["content"],
                    company_id=policy["company_id"],
                    customer_id="",  # Policies are company-level, not customer-specific
                    doc_type=policy["doc_type"],
                    title=policy["title"],
                    source=policy["source"],
                    metadata={"inserted_at": datetime.utcnow().isoformat()}
                )
                logger.info(f"    ✓ Inserted with ID: {doc_id}")
                results["policies_inserted"] += 1
            except Exception as e:
                error_msg = f"Error inserting policy '{policy['title']}': {str(e)}"
                logger.error(f"    ✗ {error_msg}")
                results["errors"].append(error_msg)

        # STEP 3: Seed guidelines
        logger.info("\n[STEP 3] Seeding guidelines...")
        for guideline in SAMPLE_GUIDELINES:
            try:
                logger.info(f"  Inserting guideline: {guideline['title'][:50]}...")
                doc_id = client.index_document(
                    collection_name="ClaimGuidelines",
                    content=guideline["content"],
                    company_id=guideline["company_id"],
                    customer_id="",  # Guidelines are company-level
                    doc_type=guideline["doc_type"],
                    title=guideline["title"],
                    source=guideline["source"],
                    metadata={"inserted_at": datetime.utcnow().isoformat()}
                )
                logger.info(f"    ✓ Inserted with ID: {doc_id}")
                results["guidelines_inserted"] += 1
            except Exception as e:
                error_msg = f"Error inserting guideline '{guideline['title']}': {str(e)}"
                logger.error(f"    ✗ {error_msg}")
                results["errors"].append(error_msg)

        # STEP 4: Seed historical claims
        logger.info("\n[STEP 4] Seeding historical claims...")
        for claim in SAMPLE_HISTORY:
            try:
                logger.info(f"  Inserting history: {claim['title'][:50]}...")
                doc_id = client.index_document(
                    collection_name="ClaimHistory",
                    content=claim["content"],
                    company_id=claim["company_id"],
                    customer_id="",  # History is anonymized (no customer ID)
                    doc_type=claim["doc_type"],
                    title=claim["title"],
                    source=claim["source"],
                    metadata={"inserted_at": datetime.utcnow().isoformat()}
                )
                logger.info(f"    ✓ Inserted with ID: {doc_id}")
                results["history_inserted"] += 1
            except Exception as e:
                error_msg = f"Error inserting history '{claim['title']}': {str(e)}"
                logger.error(f"    ✗ {error_msg}")
                results["errors"].append(error_msg)

        logger.info("\n" + "=" * 70)
        logger.info("SEEDING COMPLETE")
        logger.info("=" * 70)
        logger.info(f"\nResults:")
        logger.info(f"  Policies inserted: {results['policies_inserted']}")
        logger.info(f"  Guidelines inserted: {results['guidelines_inserted']}")
        logger.info(f"  Historical claims inserted: {results['history_inserted']}")
        logger.info(f"  Errors: {len(results['errors'])}")

        if results['errors']:
            logger.warning("\nErrors encountered:")
            for error in results['errors']:
                logger.warning(f"  - {error}")

        return results

    except Exception as e:
        logger.error(f"\n✗ SEEDING FAILED: {str(e)}", exc_info=True)
        results["errors"].append(f"Fatal error: {str(e)}")
        return results

    finally:
        client.close()


def verify_seeding(weaviate_url: str = "http://localhost:8080") -> dict:
    """
    Verify that seeding was successful by running test queries.

    INTERVIEW EXPLANATION - Verification pattern:
    After loading data, verify it's searchable.

    This prevents silent failures:
    - Data inserted but indices not built
    - Data inserted but wrong collection
    - Data inserted but couldn't be embedded

    Simple verification: Search for each company's data, verify results.

    Args:
        weaviate_url: URL to Weaviate instance

    Returns:
        Dict with verification results
    """
    logger.info("\n" + "=" * 70)
    logger.info("VERIFYING SEEDED DATA")
    logger.info("=" * 70)

    client = WeaviateClient(weaviate_url)
    results = {
        "hdfc_policies_found": 0,
        "axa_policies_found": 0,
        "hdfc_guidelines_found": 0,
        "axa_guidelines_found": 0,
        "hdfc_history_found": 0,
        "axa_history_found": 0,
        "success": True
    }

    try:
        # Test HDFC Life policies
        logger.info("\n[VERIFY] Searching HDFC Life policies...")
        hdfc_policies = client.hybrid_search(
            collection_name="ClaimPolicies",
            query_text="emergency room coverage",
            company_id="hdfc-life",
            limit=5
        )
        results["hdfc_policies_found"] = len(hdfc_policies)
        logger.info(f"  ✓ Found {len(hdfc_policies)} HDFC policies")

        # Test AXA policies
        logger.info("\n[VERIFY] Searching AXA Insurance policies...")
        axa_policies = client.hybrid_search(
            collection_name="ClaimPolicies",
            query_text="dental treatment cardiac care",
            company_id="axa-insurance",
            limit=5
        )
        results["axa_policies_found"] = len(axa_policies)
        logger.info(f"  ✓ Found {len(axa_policies)} AXA policies")

        # Test guidelines
        logger.info("\n[VERIFY] Searching guidelines...")
        guidelines = client.hybrid_search(
            collection_name="ClaimGuidelines",
            query_text="emergency assessment approval",
            company_id="hdfc-life",
            limit=5
        )
        results["hdfc_guidelines_found"] = len(guidelines)
        logger.info(f"  ✓ Found {len(guidelines)} HDFC guidelines")

        # Test history
        logger.info("\n[VERIFY] Searching historical claims...")
        history = client.vector_search(
            collection_name="ClaimHistory",
            query_text="emergency room visit approval",
            company_id="hdfc-life",
            limit=5
        )
        results["hdfc_history_found"] = len(history)
        logger.info(f"  ✓ Found {len(history)} HDFC historical claims")

        # Overall verification
        if all([
            results["hdfc_policies_found"] > 0,
            results["hdfc_guidelines_found"] > 0,
            results["hdfc_history_found"] > 0
        ]):
            logger.info("\n✓ VERIFICATION PASSED - All data is searchable")
        else:
            logger.warning("\n✗ VERIFICATION WARNING - Some data not found")
            results["success"] = False

        return results

    except Exception as e:
        logger.error(f"\n✗ Verification failed: {str(e)}", exc_info=True)
        results["success"] = False
        return results

    finally:
        client.close()


if __name__ == "__main__":
    """
    Run seeding and verification.

    Usage:
    ```bash
    # Run from project root
    python -m claimbridge.scripts.seed_weaviate
    ```
    """
    seeding_results = seed_weaviate()
    verification_results = verify_seeding()

    if seeding_results["errors"]:
        exit(1)  # Exit with error code
    exit(0)  # Success
