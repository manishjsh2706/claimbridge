# Weaviate Integration Guide - ClaimBridge

## Overview

This guide explains how Weaviate vector database integrates with ClaimBridge's LangGraph pipeline to implement RAG (Retrieval Augmented Generation) for intelligent claim processing.

**INTERVIEW QUESTION:** "Walk me through how your system processes a claim from submission to approval."

**ANSWER:** 
1. **VALIDATE**: Claim data checked for completeness and correctness
2. **RETRIEVE** ← **WEAVIATE** (This document): Fetch relevant policies from vector database
3. **GENERATE**: LLM uses claim + retrieved context to generate assessment
4. **QUALITY CHECK**: Validate LLM response quality
5. **PUBLISH**: Save result to database

This document focuses on step 2.

---

## Architecture Layers

### Layer 1: Database Client (weaviate_client.py)
```
Responsibility: Low-level database operations
├── Connect to Weaviate server
├── Create collections (schemas)
├── Index documents (insert with embeddings)
├── Execute queries (hybrid search, vector search)
└── Close connections
```

**Why separate from application logic?**
- Database operations are standardized and reusable
- Can test with mock client
- Can swap Weaviate for Pinecone/Milvus without changing business logic

### Layer 2: RAG Orchestrator (weaviate_integration.py)
```
Responsibility: Orchestrate RAG workflow
├── Retrieve policies (hybrid search - keyword + vector)
├── Retrieve guidelines (hybrid search)
├── Retrieve historical claims (vector search)
├── Compile context for LLM
└── Return formatted documents + context string
```

**Why separate from nodes?**
- Orchestrator handles workflow coordination
- Nodes handle business decisions
- Each has single responsibility

### Layer 3: LangGraph Nodes (nodes_with_weaviate.py)
```
Responsibility: Business logic
├── validate_claim: Data validation
├── retrieve_documents: USE orchestrator to fetch context
├── generate_assessment: LLM reasoning
├── quality_check: Response validation
└── publish_result: Database save
```

---

## Data Flow Example

```
User submits claim:
{
  "claim_number": "CLM-2024-001",
  "amount": 5000,
  "description": "Emergency room visit for chest pain"
}

↓ [VALIDATE NODE]
  Checks: claim_number exists? amount > 0? description present?
  Result: is_valid=true

↓ [RETRIEVE NODE]  
  Calls: orchestrator.retrieve_claim_context(
    claim_description="Emergency room visit for chest pain",
    company_id="hdfc-life",  ← Multi-tenant filter
    limit=5
  )
  
  ↓↓ [ORCHESTRATOR] ↓↓
     Runs 3 queries:
     
     Query 1: "Find policies similar to emergency room visit"
     Weaviate: WHERE company_id = 'hdfc-life'
     Results:
     - Emergency Room Coverage Policy (score: 0.95)
     - Inpatient Hospitalization Policy (score: 0.78)
     
     Query 2: "Find guidelines for emergency assessment"
     Weaviate: WHERE company_id = 'hdfc-life'
     Results:
     - Emergency Claim Assessment Guidelines (score: 0.92)
     
     Query 3: "Find similar past claims"
     Weaviate: WHERE company_id = 'hdfc-life'
     Results:
     - Similar ER claim from Nov 2023 (APPROVED) (score: 0.87)
  ↑↑ [ORCHESTRATOR COMPLETE] ↑↑
  
  Return: {
    retrieved_documents: [policy1, policy2, guideline1, history1],
    retrieval_context: "=== RETRIEVED CONTEXT ===\n\n--- POLICIES (2 found) ---\n..."
  }

↓ [GENERATE NODE]
  Receives: claim + retrieval_context
  Calls: claude("Given this claim and these policies, approve or reject?")
  LLM sees:
    "Emergency room visit for chest pain"
    [POLICY] Emergency Room Coverage Policy
    "Emergency room visits covered up to $10,000..."
    [POLICY] Inpatient Hospitalization Policy
    "Hospital admission covered 100% after deductible..."
    [GUIDELINE] Emergency Claim Assessment Guidelines
    "Emergency claims require expedited processing..."
    [HISTORY] Similar claim from Nov 2023
    "Similar patient, ER visit, approved..."
  
  LLM output: "Based on policies and similar precedent, APPROVE"

↓ [QUALITY CHECK NODE]
  Validates: confidence >= 0.7? Response > 50 chars? Contains decision?
  Result: quality_check_passed = true

↓ [PUBLISH NODE]
  Saves: claim + assessment + logs to PostgreSQL
  Result: final_status = APPROVED

✓ COMPLETE: Claim processed with context-aware LLM reasoning
```

---

## Setup Instructions

### Step 1: Verify Docker Services Running

```bash
docker-compose ps
```

Should show:
```
CONTAINER           STATUS
postgres            healthy
weaviate            healthy
claimbridge         healthy
```

### Step 2: Create Weaviate Collections

```bash
# Inside Docker container
docker-compose exec claimbridge python -c "
from src.claimbridge.weaviate_client import WeaviateClient
client = WeaviateClient()
client.create_collections()
client.close()
"
```

**What this does:**
- Creates 3 collections: ClaimPolicies, ClaimGuidelines, ClaimHistory
- Sets up schema with properties: content, company_id, customer_id, doc_type, title, source, metadata_, created_at, updated_at
- Configures vectorizer: text2vec-openai (1536-dimensional vectors)
- Configures indexing: HNSW with cosine distance

### Step 3: Seed Sample Data

```bash
# Run seeding script
docker-compose exec claimbridge python -m src.claimbridge.scripts.seed_weaviate
```

**What this does:**
- Inserts 8 sample policies (HDFC Life + AXA Insurance)
- Inserts 5 sample guidelines
- Inserts 5 sample historical claims
- Verifies all data is searchable

**Output:**
```
SEEDING WEAVIATE WITH SAMPLE DATA
==================================================

[STEP 1] Creating collections...
✓ Collections ready

[STEP 2] Seeding policies...
  Inserting policy: Emergency Room Coverage - Standard Plan...
    ✓ Inserted with ID: 12345abc...
  [More policies...]

[STEP 3] Seeding guidelines...
  [Guidelines...]

[STEP 4] Seeding historical claims...
  [Historical claims...]

Results:
  Policies inserted: 8
  Guidelines inserted: 5
  Historical claims inserted: 5
  Errors: 0

VERIFYING SEEDED DATA
==================================================

[VERIFY] Searching HDFC Life policies...
  ✓ Found 5 HDFC policies

[VERIFY] Searching guidelines...
  ✓ Found 5 guidelines

[VERIFY] Searching historical claims...
  ✓ Found 5 historical claims

✓ VERIFICATION PASSED - All data is searchable
```

### Step 4: Initialize RAG Orchestrator in FastAPI

Add to `main.py`:

```python
from src.claimbridge.langgraph.nodes_with_weaviate import initialize_rag_orchestrator

@app.on_event("startup")
async def startup_event():
    """Initialize resources on app startup."""
    initialize_rag_orchestrator()
    logger.info("✓ RAG orchestrator initialized")

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on app shutdown."""
    logger.info("✓ Shutting down")
```

### Step 5: Update Workflow to Use Real Retrieval

In `workflow.py`, change imports:

```python
# OLD:
from .nodes import retrieve_documents

# NEW:
from .nodes_with_weaviate import retrieve_documents
```

Then rebuild Docker:

```bash
docker-compose down
docker-compose up --build -d
```

---

## Testing the Integration

### Test 1: Direct Orchestrator Test

```bash
docker-compose exec claimbridge python -c "
from src.claimbridge.weaviate_integration import create_rag_orchestrator

orchestrator = create_rag_orchestrator()
result = orchestrator.retrieve_claim_context(
    claim_description='Emergency room visit for chest pain',
    company_id='hdfc-life',
    customer_id='john-doe-12345'
)

print(f'Documents found: {len(result[\"retrieved_documents\"])}')
print(f'Retrieval context length: {len(result[\"retrieval_context\"])} chars')
orchestrator.close()
"
```

Expected output:
```
Documents found: 15
Retrieval context length: 3248 chars
```

### Test 2: Full Claim Processing via API

```bash
curl -X POST http://localhost:8000/claims/process \
  -H "X-Company-Id: hdfc-life" \
  -H "X-Customer-Id: john-doe-12345" \
  -H "Content-Type: application/json" \
  -d '{
    "claim_number": "CLM-2024-001",
    "policy_number": "POL-123456",
    "amount": 5000,
    "service_date": "2024-01-15",
    "description": "Emergency room visit for chest pain"
  }'
```

Expected response:
```json
{
  "claim_id": "hdfc-life-john-doe-12345-CLM-2024-001-1695000000",
  "company_id": "hdfc-life",
  "customer_id": "john-doe-12345",
  "final_status": "APPROVED",
  "generated_response": "Based on claim details and relevant policy documents...",
  "confidence_score": 0.92,
  "validation_errors": [],
  "quality_issues": [],
  "processing_log_id": 1,
  "node_execution_log": [
    "[2024-01-15T10:30:45] VALIDATE: PASSED",
    "[2024-01-15T10:30:46] RETRIEVE: 15 documents",
    "[2024-01-15T10:30:50] GENERATE: confidence=0.92",
    "[2024-01-15T10:30:51] QUALITY_CHECK: PASSED",
    "[2024-01-15T10:30:52] PUBLISH: APPROVED"
  ]
}
```

### Test 3: Multi-Tenant Isolation

```bash
# AXA Insurance submits claim with same claim_number
curl -X POST http://localhost:8000/claims/process \
  -H "X-Company-Id: axa-insurance" \
  -H "X-Customer-Id: alice-jones-11111" \
  -H "Content-Type: application/json" \
  -d '{
    "claim_number": "CLM-2024-001",
    "policy_number": "POL-999001",
    "amount": 7500,
    "service_date": "2024-02-10",
    "description": "Hospital admission for cardiac evaluation"
  }'
```

**Verification:**
- HDFC claim and AXA claim both use `CLM-2024-001`
- But they're stored separately in database (composite key: company_id + customer_id + claim_number)
- HDFC retrieval only sees HDFC policies
- AXA retrieval only sees AXA policies
- No data leakage between companies

---

## Interview Q&A

### Q1: "Why does Weaviate return similarity scores? How do you use them?"

**A:** Similarity scores (0-1) indicate relevance:
- 0.95 = Highly relevant (LLM should prioritize this)
- 0.75 = Moderately relevant (LLM uses with caution)
- 0.5 = Low relevance (LLM might ignore)

Usage:
1. **Ranking**: Display high-score documents first
2. **Confidence**: If all scores < 0.6, confidence in assessment is lower
3. **Filtering**: Can reject documents below 0.5 threshold

```python
# Example: Filter by score
high_confidence = [doc for doc in docs if doc['similarity_score'] > 0.7]
```

### Q2: "What if document doesn't exist in Weaviate?"

**A:** Three scenarios:

1. **New claim type never seen**: 
   - Weaviate returns low-similarity documents
   - LLM generates assessment based on generic guidelines
   - Confidence score is lower (e.g., 0.6 vs 0.92)
   - May be escalated to manual review

2. **Weaviate is down**:
   - retrieve_documents catches exception
   - Returns empty documents + error message
   - generate_assessment still runs (graceful degradation)
   - Result is lower quality but doesn't fail completely

3. **Company has no policies uploaded**:
   - Query returns 0 results
   - LLM uses only claim text + generic guidelines
   - Assessment quality is lower
   - Should upload policies first

### Q3: "How do you prevent search poisoning?"

**A:** Multi-tenant isolation at three levels:

1. **Code level**: Node checks `state['company_id']` exists
2. **Orchestrator level**: Passes company_id to every Weaviate query
3. **Database level**: Weaviate WHERE clause filters by company_id

```python
# If attacker tries:
wrong_query = rag_orchestrator.retrieve_claim_context(
    company_id="axa-insurance",  # Attacker sets this
    # but they can't change company_id in state
    # because it comes from X-Company-Id header (validated by API gateway)
)

# Weaviate query includes:
# WHERE company_id = state['company_id']  # From header, trusted
# NOT where company_id = request.body['company_id']  # From body, untrusted
```

### Q4: "Why hybrid search instead of just vector search?"

**A:** Comparison:

```
Query: "Emergency room visit"

VECTOR SEARCH ONLY:
- Finds semantically similar: "ER visit", "urgent care", "emergency department"
- Miss: "Emergency Room Policy" (exact keyword match)
- Good for: Finding similar past claims
- Bad for: Finding specific policies

HYBRID SEARCH (keyword + vector):
- Finds exact matches: "Emergency room" in policy text
- Finds semantic matches: "ER visit" → vector search
- Combines both via ranking fusion
- Good for: Finding both exact + semantic matches
```

For **policies** (need specific terms): Hybrid search
For **historical claims** (need semantic similarity): Vector search only

### Q5: "What's the maximum documents you can retrieve?"

**A:** Currently limited to:
- 5 policies (limit=5 in orchestrator)
- 5 guidelines (limit=5)
- 5 historical claims (limit=5)
- **Total context**: ~3KB formatted text

**Why this limit?**
- LLM context window: 8K tokens (let's use 6K for safety)
- Per document: ~200 tokens
- 15 docs × 200 tokens = 3000 tokens (leaves 3K for claim + response)

**If you need more:**
```python
# Increase limit
result = orchestrator.retrieve_claim_context(
    ...
    limit=10  # More documents
)

# Or: Rank by score, include only top-N
high_score_docs = [d for d in docs if d['similarity_score'] > 0.8]
```

### Q6: "How do you handle embedded bias in historical claims?"

**A:** Historical data can embed past discrimination:

Example: "Claim from female patient → similar to approved female claim → approved"
This perpetuates gender bias.

Mitigations:
1. **Audit logs**: Log which documents influenced decision
2. **Bias monitoring**: Check approval rates by demographic
3. **Human review**: Manual review for edge cases
4. **Policy review**: Regular update of guidelines to remove bias
5. **Document filtering**: Exclude problematic historical claims

```python
# Example: Exclude claims before 2020 (old bias)
recent_history = [
    h for h in history 
    if h.get('source') != 'history_archive_pre2020'
]
```

---

## Troubleshooting

### Problem: "No documents found for any query"

```bash
# Check 1: Are collections created?
curl http://localhost:8080/v1/meta

# Check 2: Is Weaviate healthy?
docker-compose logs weaviate | grep -i "error"

# Check 3: Seed data?
docker-compose exec claimbridge python -m src.claimbridge.scripts.seed_weaviate

# Check 4: Can you search manually?
docker-compose exec claimbridge python -c "
from src.claimbridge.weaviate_client import WeaviateClient
c = WeaviateClient()
results = c.hybrid_search('ClaimPolicies', 'emergency', 'hdfc-life')
print(f'Found {len(results)} results')
c.close()
"
```

### Problem: "Weaviate timeout errors"

```bash
# Check Docker logs
docker-compose logs weaviate

# Weaviate might need more time to start
# Increase health check timeout in docker-compose.yml:
# timeout: 20s  # was 10s

# Or restart with more memory
docker-compose down
docker-compose up -d --build
```

### Problem: "Company A can see Company B's documents"

```bash
# This should NEVER happen if multi-tenant isolation is implemented
# Immediate security incident - check:

# 1. Is company_id being passed from header?
# 2. Is retrieve_documents using correct company_id?
# 3. Is Weaviate WHERE clause including company_id filter?

# Debug:
# Add logging to orchestrator:
logger.info(f"Query: company_id={company_id}")  # Should match X-Company-Id header
```

---

## Performance Optimization

### Caching Retrieved Documents

```python
# Cache common policy sets
from functools import lru_cache

@lru_cache(maxsize=100)
def get_company_policies(company_id: str):
    """Cache policies by company (avoids repeated Weaviate queries)."""
    # Query Weaviate once, cache result
    # Hit rate ~70% for common companies
    # TTL: 1 hour (policies change daily, not real-time)
```

### Batch Processing

```python
# Process multiple claims at once
def process_claims_batch(claims, company_id):
    """Process N claims using same orchestrator (connection pooling)."""
    for claim in claims:
        # Reuse same orchestrator = same Weaviate connection
        result = orchestrator.retrieve_claim_context(...)
```

### Pre-warm Embeddings Cache

```bash
# On startup, pre-query common claim types
common_types = [
    "emergency room visit",
    "hospitalization",
    "surgery",
    "physical therapy"
]

for claim_type in common_types:
    orchestrator.retrieve_claim_context(claim_type, "hdfc-life")
# Result: Weaviate caches embeddings for common queries
```

---

## Next Steps

1. **LLM Integration**: Replace mock generate_assessment with real Claude API call
2. **Resilience**: Add retries, circuit breaker, fallbacks
3. **Audit Logging**: Log all Weaviate queries for compliance
4. **Performance**: Add caching, batch processing
5. **Monitoring**: Track retrieval quality metrics
6. **RBAC**: Implement role-based access to different policy sets

---

## Code Files Reference

| File | Purpose |
|------|---------|
| `weaviate_client.py` | Low-level Weaviate operations |
| `weaviate_integration.py` | RAG orchestrator |
| `nodes_with_weaviate.py` | LangGraph nodes with real retrieval |
| `seed_weaviate.py` | Sample data initialization |
| `WEAVIATE_INTEGRATION_GUIDE.md` | This file |

---

**Last Updated:** 2024-01-15  
**Version:** 1.0  
**Author:** ClaimBridge Architecture Team
