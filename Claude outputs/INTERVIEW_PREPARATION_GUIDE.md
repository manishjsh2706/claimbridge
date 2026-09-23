# ClaimBridge: Interview Preparation Guide

**Purpose:** This is your 30-minute interview cheat sheet. Use it to quickly recall key architecture points, design decisions, and technical depth.

---

## 🎯 THE ELEVATOR PITCH (2 minutes)

"I built **ClaimBridge**, a production-grade AI claims processing system for insurance companies. It's a **multi-tenant SaaS platform** that uses **LangGraph** for workflow orchestration and **Weaviate vector database** for semantic search (RAG pattern). 

The system processes claims through 5 sequential nodes:
1. **Validate** - Check data quality
2. **Retrieve** - Query Weaviate for relevant policies (semantic search)
3. **Generate** - LLM reasons using claim + retrieved policies
4. **Quality Check** - Validate assessment quality
5. **Publish** - Save to PostgreSQL

Key achievement: **Multi-tenant isolation at 3 levels** (API, application logic, database query), preventing Company A from ever seeing Company B's data or policies."

---

## 🏗️ CORE ARCHITECTURE

### System Components

```
User API Request
    ↓
FastAPI (main_with_weaviate.py)
    ├─ Validates headers (X-Company-Id, X-Customer-Id)
    ├─ Extracts claim data from body
    ↓
LangGraph Workflow (5 nodes)
    ├─ Node 1: validate_claim
    ├─ Node 2: retrieve_documents ←→ Weaviate (via RAGOrchestrator)
    ├─ Node 3: generate_assessment ←→ LLM (Claude/OpenAI)
    ├─ Node 4: quality_check
    └─ Node 5: publish_result ←→ PostgreSQL
    ↓
HTTP Response (JSON)
```

### Why This Architecture?

| Component | Why Chosen |
|-----------|-----------|
| **LangGraph** | State machine for multi-step workflows. Each node sees full state. Sequential execution with state passing. |
| **Weaviate** | Self-hosted vector DB. Built-in multi-tenant filtering. HNSW indexing. Cost-effective. |
| **FastAPI** | Fast, type-safe, built-in validation. Auto-generates Swagger docs. Great for APIs. |
| **PostgreSQL** | Transactional. ACID guarantees. Perfect for storing claims + audit logs. |

---

## 🔐 MULTI-TENANT ISOLATION

**CRITICAL for SaaS:** Prevent data leakage between customers.

### Defense in Depth (3 Layers)

```python
# Layer 1: API Headers (TRUSTED - from authentication system)
X-Company-Id: hdfc-life          ← Comes from user's authenticated identity
X-Customer-Id: john-doe-12345    ← Comes from user's authenticated identity

# Layer 2: Application Code (VALIDATED)
company_id = verify_company_id(header)  # Raises 400 if missing/invalid
customer_id = verify_customer_id(header)

state = {
    "company_id": company_id,        # From header (TRUSTED)
    "customer_id": customer_id,      # From header (TRUSTED)
    # Note: NO company_id in request body (that's USER INPUT)
}

# Layer 3: Database Queries (FILTERED)
# Every Weaviate query includes:
results = weaviate.hybrid_search(
    query_text=...,
    where={
        "path": ["company_id"],
        "operator": "Equal",
        "valueString": company_id    # Filter to this company only
    }
)
```

### Why 3 Layers?

- **If** Layer 1 compromised: Layer 2 & 3 still protect
- **If** Layer 2 compromised: Layer 3 still protects
- **If** Layer 3 compromised: Attacker would need to change code
- Defense in depth = fail-safe architecture

---

## 📊 KEY DESIGN PATTERNS

### 1. **RAG (Retrieval Augmented Generation)**

**Problem:** LLM hallucination. "Is claim covered?" → LLM guesses instead of checking policy.

**Solution:** Retrieve actual policies before asking LLM.

```
Traditional: Claim → LLM → ??? (Did LLM read the policy correctly?)
RAG:         Claim + [Retrieved Policies] → LLM → Better answer
```

### 2. **Separation of Concerns**

```python
# Weaviate Client (weaviate_client.py)
# Responsibility: Database operations only
client.hybrid_search(...)

# RAG Orchestrator (weaviate_integration.py)
# Responsibility: Workflow coordination
orchestrator.retrieve_claim_context(...)  # Calls client 3x, combines results

# LangGraph Nodes (nodes_with_weaviate.py)
# Responsibility: Business logic
def retrieve_documents(state):
    result = orchestrator.retrieve_claim_context(...)  # Uses orchestrator
    return {...}  # Returns dict for LangGraph to merge

# FastAPI (main_with_weaviate.py)
# Responsibility: HTTP handling
@app.post("/claims/process")
async def process_claim(request, company_id, customer_id):
    result = process_claim(company_id, customer_id, ...)
```

Each layer can be tested independently. Each has one job.

### 3. **State Machine (LangGraph)**

```
Type: StateGraph
State: TypedDict (not dataclass - LangGraph requirement!)
```

Why TypedDict not dataclass?
- LangGraph needs to merge dictionaries into state
- Dataclasses don't support dict merging like TypedDict does
- TypedDict is a type hint for dicts, making it compatible

### 4. **Dependency Injection**

```python
@app.post("/claims/process")
async def process_claim(
    request: ClaimRequest,
    company_id: str = Depends(verify_company_id),  # ← Dependency injection
    customer_id: str = Depends(verify_customer_id)
):
    # FastAPI automatically:
    # 1. Calls verify_company_id() with X-Company-Id header
    # 2. Gets company_id back
    # 3. Passes it to this function
    # 4. Returns 400 if verify_* raises HTTPException
```

Why useful?
- Validation happens before your function runs
- Reusable across multiple endpoints
- Easy to test (pass different dependencies)

### 5. **Factory Pattern**

```python
def create_rag_orchestrator(url="http://localhost:8080"):
    """Factory function to create orchestrator."""
    client = WeaviateClient(url)
    return WeaviateRAGOrchestrator(client)
```

Why?
- Centralized initialization logic
- Easy to swap implementations for testing
- Production: Read URL from environment variable

---

## 🔍 MULTI-TENANT FILTERING DEEP DIVE

### Scenario: Company A processes claim, should they see Company B's policies?

**NO - Here's how we prevent it:**

```python
# User from Company A makes request
POST /claims/process
Header: X-Company-Id: hdfc-life
Header: X-Customer-Id: alice-12345
Body: {description: "Emergency room visit", ...}

# Step 1: FastAPI validates header
company_id = verify_company_id("hdfc-life")  # ✓ Valid
state["company_id"] = "hdfc-life"            # Stored in state

# Step 2: Retrieve node calls orchestrator
result = orchestrator.retrieve_claim_context(
    company_id="hdfc-life"  # ← Uses state["company_id"], NOT user input
)

# Step 3: RAGOrchestrator queries Weaviate
policies = client.hybrid_search(
    collection_name="ClaimPolicies",
    query_text="emergency room",
    company_id="hdfc-life",  # ← WHERE clause filters by company_id
    limit=5
)

# Weaviate executes:
# SELECT * FROM ClaimPolicies
# WHERE company_id = 'hdfc-life'  # ← Only HDFC's policies
# ORDER BY relevance DESC
# LIMIT 5

# Even if attacker tries to change company_id:
# POST /claims/process
# Header: X-Company-Id: hdfc-life  ← Authentication system validates this
# Body: {company_id: "axa-insurance"}  ← This is ignored! It's user input.

# Result: ✓ Company A only sees HDFC policies
#         ✓ Company A cannot see AXA policies
```

---

## 💬 COMMON INTERVIEW QUESTIONS

### Q1: "Walk us through a single claim."

**Structure your answer:**
1. **API layer**: Headers validated
2. **Validate node**: Data quality checked
3. **Retrieve node**: Policies fetched from Weaviate (key differentiation!)
4. **Generate node**: LLM reasons using policies
5. **QC node**: Response quality validated
6. **Publish node**: Result saved to DB

### Q2: "How do you prevent Company A from seeing Company B's data?"

**Answer format:**
1. **State the problem**: Multi-tenant isolation is critical
2. **Explain approach**: Defense in depth - 3 layers
3. **Give examples**: Layer 1 (headers), Layer 2 (code), Layer 3 (query)
4. **Attack scenario**: "If Company A tries to spoof company_id in request body..."
5. **Mitigation**: "Company ID comes from header, not body..."

### Q3: "Why use Weaviate instead of [Pinecone/Milvus/Elasticsearch]?"

**Answer format:**
1. **Acknowledge tradeoff**: "Each has pros/cons"
2. **State requirement**: "We're SaaS - need data privacy"
3. **Explain choice**: "Weaviate is self-hosted (data never leaves our servers)"
4. **Technical reason**: "Multi-tenant filtering via WHERE company_id"
5. **Cost benefit**: "Free to self-host, no per-query costs"

### Q4: "What happens if Weaviate goes down?"

**Answer format:**
1. **Graceful degradation**: "Claims still process, just lower quality"
2. **Mechanism**: "retrieve_documents catches exception, returns empty context"
3. **LLM fallback**: "generate_assessment runs with empty context, lower confidence"
4. **Result**: "Claim marked as PENDING_REVIEW instead of auto-approved"
5. **Production fix**: "Add circuit breaker + cached common policies"

### Q5: "How do you handle bias in AI decisions?"

**Answer format:**
1. **Acknowledge issue**: "AI inherits bias from training data"
2. **Monitoring**: "Track approval rates by demographic groups"
3. **Audit trail**: "Log which documents influenced each decision"
4. **Manual review**: "Escalate borderline cases to humans"
5. **Policy update**: "Regular audits to remove discriminatory rules"

### Q6: "Explain the difference between hybrid and vector search."

**Keyword Search (BM25):**
- Exact term matching: "emergency room" finds "emergency room"
- Fast, precise, limited
- Good for: Finding specific policy clauses

**Vector Search:**
- Semantic similarity: "ER visit" ≈ "emergency room" (similar vectors)
- Slower, handles synonyms, more recall
- Good for: Finding similar past claims

**Hybrid:**
- Combines both via ranking fusion
- Best of both worlds
- Used for: Policies (need both exact terms and semantic understanding)

### Q7: "How does LangGraph state work?"

**Key points:**
1. **TypedDict** (not dataclass): LangGraph requirement
2. **Dictionary passing**: Each node returns dict, LangGraph merges it into state
3. **Sequential execution**: Node 1 output → Node 2 input → Node 3 input...
4. **Example**: 
   ```python
   state = {"is_valid": False, "validated": []}
   # After validate_claim returns {"is_valid": True, "validated": [...]}
   # State becomes: {"is_valid": True, "validated": [...], ...other fields...}
   ```

---

## 📈 PERFORMANCE & SCALABILITY

### Latency Breakdown (per claim)

| Component | Time |
|-----------|------|
| Validation | 10ms |
| Weaviate query (3x) | 150ms |
| LLM call | 3-5s |
| Quality check | 20ms |
| Database save | 30ms |
| **Total** | **~3.2-5.2s** |

### Bottleneck? 
LLM calls (Claude/OpenAI). Everything else is fast.

### Optimizations

1. **Caching**: Cache common policy sets (Redis)
2. **Batching**: Process 100 claims at once (reuse LLM tokens)
3. **Smaller models**: Use faster LLM for simple cases
4. **Async**: Process independent parts in parallel (Python asyncio)

---

## 🛡️ SECURITY CONSIDERATIONS

### Data Protection

| Layer | Protection |
|-------|-----------|
| **Transport** | HTTPS (TLS encryption) |
| **Authentication** | OAuth 2.0 (X-Company-Id header) |
| **Authorization** | Company_id filtering at query level |
| **Encryption** | At-rest (AES-256), in-transit (TLS) |
| **Audit logs** | All queries logged with company_id |

### Attack Vectors Considered

1. **Claim number spoofing**: Use composite claim_id (company + customer + timestamp)
2. **Company ID spoofing**: Header validation + database filtering
3. **SQL injection**: Parameterized queries (Weaviate)
4. **LLM prompt injection**: Claim text is data, not code
5. **Timing attacks**: All operations same time (no company detection by timing)

---

## 🎓 TALKING POINTS FOR DEPTH

### "Tell me about a technical challenge you solved"

**Talking point: LangGraph TypedDict issue**

Challenge: LangGraph was rejecting state as @dataclass
```python
# This failed:
class ClaimProcessingState(dataclass):
    claim_id: str
    
# Error: "argument after ** must be a mapping, not ClaimProcessingState"
```

Root cause: LangGraph uses dict merging internally. Dataclasses don't support this.

Solution: Changed to TypedDict
```python
class ClaimProcessingState(TypedDict, total=False):
    claim_id: str
```

Learning: Understand your framework's assumptions. TypedDict is just a type hint, so it allows dict operations.

### "Tell me about multi-tenancy design"

**Talking point: Defense in depth**

I implemented 3-layer isolation:
1. **API layer**: Header validation (X-Company-Id)
2. **Application logic**: State contains company_id
3. **Database queries**: WHERE company_id = X

Why 3 layers? If one layer is compromised, others still protect. 

Example of failure: If company_id came from request body (user input), attacker could spoof it. But because it comes from headers (authenticated system), it's trusted.

### "Tell me about system design decisions"

**Talking point: RAG pattern**

Traditional: Claim → Hardcoded rules → Approve/Reject

Problem: Rules explosion. New edge case = new rule.

Design: Claim + Retrieved Policies → LLM → Approve/Reject

Benefits:
1. **Flexibility**: Update policies without code changes
2. **Reasoning**: LLM can extrapolate to novel situations
3. **Audit trail**: Clear which policies influenced decision

---

## 📚 TECHNICAL VOCABULARY

**Know these terms cold:**

- **RAG**: Retrieval Augmented Generation (retrieve data → enhance LLM)
- **Vector embedding**: Converting text to numerical vector (1536 dimensions)
- **Semantic search**: Finding similar meaning (not keyword match)
- **HNSW**: Hierarchical Navigable Small World (fast vector search index)
- **Hybrid search**: Combining keyword search + vector search
- **LangGraph**: State machine for multi-step LLM workflows
- **Multi-tenancy**: Single system serving multiple customers
- **Tenant isolation**: Preventing cross-customer data leakage
- **Defense in depth**: Multiple security layers (fail-safe)
- **Idempotency**: Same operation twice = same result (safe retries)
- **TypedDict**: Python type hint for dictionaries
- **State merging**: Combining node outputs into state (LangGraph)

---

## ✅ PRE-INTERVIEW CHECKLIST

- [ ] Can you explain the 5-node pipeline from memory?
- [ ] Can you draw the architecture on a whiteboard?
- [ ] Can you explain multi-tenant isolation in 2 minutes?
- [ ] Can you list 3 reasons why Weaviate (not Pinecone)?
- [ ] Can you explain hybrid search vs vector search?
- [ ] Can you explain TypedDict vs dataclass?
- [ ] Can you give one real attack scenario + how you prevent it?
- [ ] Can you explain LangGraph state flow?
- [ ] Can you list one technical challenge + how you solved it?
- [ ] Can you explain performance bottlenecks?

---

## 🎬 PRACTICE SCRIPT

**Use this as your starting point:**

"I built ClaimBridge, a multi-tenant SaaS claims processing system. Let me walk you through how it works.

When a user submits a claim via API, they provide two headers: X-Company-Id and X-Customer-Id. These come from our authentication system, so they're trusted. The claim data itself is untrusted user input.

The claim flows through a LangGraph workflow with 5 nodes:

**First**, the Validate node checks data quality - is the amount positive? Is the date formatted correctly?

**Second**, the Retrieve node is where things get interesting. This is where we use Weaviate, a vector database. We query Weaviate for relevant policies, guidelines, and historical claims. The key thing is that every query includes a WHERE filter for company_id, preventing Company A from seeing Company B's policies.

**Third**, the Generate node sends the claim AND the retrieved policies to an LLM like Claude. Instead of the LLM hallucinating whether claims are covered, it has the actual policy text to reference.

**Fourth**, the Quality Check node validates the LLM's output - is the confidence high enough? Is the response detailed enough?

**Fifth**, the Publish node saves everything to PostgreSQL with company_id and customer_id, creating the database composite key.

The key achievement here is multi-tenant isolation at 3 levels - the API, application logic, and database query level. If any one is compromised, the others still protect.

Is there anything specific you'd like me to dive deeper on?"

---

**Last Updated:** 2024-01-15  
**Time to Review:** 30 minutes  
**Confidence Level:** High (Interview-Ready) ✓
