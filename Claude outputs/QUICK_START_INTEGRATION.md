# ClaimBridge Weaviate Integration - Quick Start (5 Minutes)

**Your Project:** `D:\GenAI\claims-assistant-essential`  
**Status:** All files ready. Just 2 files need updating in your project.

---

## ⚡ The Essential Update

Your project **already has** 7 files in the right places (from previous work):
- ✅ `src/claimbridge/weaviate/` (client.py + orchestrator.py)
- ✅ `src/claimbridge/scripts/seed_weaviate.py`
- ✅ Documentation files in `docs/`

**Only 2 files need updates:**

### 1. Update `src/claimbridge/langgraph/nodes.py`

**What:** Replace mock data with real Weaviate

**How:** Copy `nodes_updated.py` content into your `nodes.py`

**Key changes:**
- Add import: `from src.claimbridge.weaviate import create_rag_orchestrator`
- Add function: `initialize_rag_orchestrator()` 
- Update `retrieve_documents()` to call real Weaviate instead of returning mock

**Why:** So retrieve_documents node fetches REAL policies from Weaviate instead of hardcoded fake data.

---

### 2. Update `src/claimbridge/main.py`

**What:** Initialize RAG orchestrator on app startup

**How:** 
1. Add import: `from src.claimbridge.langgraph.nodes import initialize_rag_orchestrator`
2. In `@app.on_event("startup")`, add:
   ```python
   logger.info("\n[STARTUP] Initializing RAG orchestrator...")
   initialize_rag_orchestrator(weaviate_url="http://weaviate:8080")
   logger.info("✓ RAG orchestrator ready")
   ```

**Why:** Weaviate connection must be ready BEFORE first claim is processed.

---

## 🚀 Full Integration Flow (If Starting From Scratch)

```bash
# 1. Copy files to your project
# nodes_updated.py → src/claimbridge/langgraph/nodes.py
# (main_updated.py reference for main.py changes)

# 2. Update docker-compose.yml to add Weaviate service
# (See INTEGRATION_INSTRUCTIONS.md for full yaml)

# 3. Start services
docker-compose up -d

# 4. Seed Weaviate with sample data
docker-compose exec claimbridge python -m src.claimbridge.scripts.seed_weaviate

# 5. Test
curl http://localhost:8000/health

# 6. Process a claim
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

---

## 📋 What Changed in `nodes.py`

### Before (Mock Data):
```python
def retrieve_documents(state: ClaimProcessingState) -> dict:
    # Returns hardcoded mock documents
    mock_documents = [
        {"id": "doc-1", "type": "policy", "title": "Standard Health Coverage Policy", ...},
        {"id": "doc-2", "type": "guideline", "title": "Claim Assessment Guidelines", ...}
    ]
    return {"retrieved_documents": mock_documents, ...}
```

### After (Real Weaviate):
```python
def retrieve_documents(state: ClaimProcessingState) -> dict:
    if _rag_orchestrator is None:
        return {"error": "RAG orchestrator not initialized", ...}
    
    # Call real orchestrator (which queries Weaviate)
    retrieval_result = _rag_orchestrator.retrieve_claim_context(
        claim_description=claim_query,
        company_id=state.company_id,
        customer_id=state.customer_id
    )
    
    return {
        "retrieved_documents": retrieval_result.get("retrieved_documents", []),
        "retrieval_context": retrieval_result.get("retrieval_context", ""),
        ...
    }
```

**Key difference:** Real policies, guidelines, and historical claims are now fetched from Weaviate!

---

## 📋 What Changed in `main.py`

### Before:
```python
@app.on_event("startup")
async def startup_event():
    logger.info("ClaimBridge starting...")
    # No Weaviate initialization
```

### After:
```python
@app.on_event("startup")
async def startup_event():
    logger.info("ClaimBridge startup...")
    # Initialize RAG orchestrator (connects to Weaviate)
    initialize_rag_orchestrator(weaviate_url="http://weaviate:8080")
```

**Key difference:** Weaviate connection is established BEFORE app starts accepting requests.

---

## ✅ Verification (30 seconds)

After updating the 2 files:

```bash
# 1. Check imports work
python -c "from src.claimbridge.weaviate import create_rag_orchestrator; print('✓ Imports OK')"

# 2. Check Docker is up
docker-compose ps

# 3. Check Weaviate is running
curl http://localhost:8080/v1/meta

# 4. Check app starts
docker-compose up -d claimbridge
sleep 2
curl http://localhost:8000/health

# 5. Test a claim
curl -X POST http://localhost:8000/claims/process \
  -H "X-Company-Id: hdfc-life" \
  -H "X-Customer-Id: test" \
  -H "Content-Type: application/json" \
  -d '{"claim_number": "TEST", "policy_number": "POL-123", "amount": 100, "service_date": "2024-01-15", "description": "Test claim processing workflow"}'
```

---

## 🎯 Architecture Summary

### Before (Your Current System)
```
Claim → Validate → [MOCK DATA] → Generate → QC → Publish
```

### After (With Weaviate Integration)
```
Claim → Validate → [REAL POLICIES FROM WEAVIATE] → Generate → QC → Publish
                       ↓
                   (company_id filter)
                   (semantic search)
                   (hybrid search)
```

**Key advantage:** LLM reasons from actual policies, not guesses.

---

## 🔐 Multi-Tenant Isolation Verified

After integration, 3-layer isolation is active:

**Layer 1: API** (headers validated)
```python
@Depends(verify_company_id)  # X-Company-Id header must be present
```

**Layer 2: Application** (company_id from state, not request body)
```python
retrieval_result = _rag_orchestrator.retrieve_claim_context(
    company_id=state.company_id,  # From HEADER, not user input
)
```

**Layer 3: Database** (WHERE clause in Weaviate query)
```python
results = client.hybrid_search(
    ...,
    company_id=company_id  # WHERE company_id = 'hdfc-life'
)
```

**Result:** Company A can never see Company B's data, even if they try to spoof the company_id in the request body (because it's ignored and uses the header value instead).

---

## 📚 Where to Get More Details

- **Complete setup guide:** `INTEGRATION_INSTRUCTIONS.md` (read if you hit any issues)
- **Architecture deep-dive:** `WEAVIATE_ARCHITECTURE_SUMMARY.md` (read for interviews)
- **Interview prep:** `INTERVIEW_PREPARATION_GUIDE.md` (read before job interviews)
- **Reference implementation:** `nodes_with_weaviate.py` and `main_with_weaviate.py` (in outputs folder)

---

## 🎓 Interview Talking Points

After this integration is done, you can confidently say:

> "I implemented a production-grade Weaviate vector database integration for multi-tenant claims processing. The system retrieves relevant policies before sending claims to the LLM (RAG pattern). Multi-tenant isolation is enforced at 3 layers: API header validation, application state filtering, and database WHERE clauses. Each company's claims are stored separately with company_id isolation, preventing any data leakage."

---

## ⏱️ Time to Integration

- Reading this: 5 min ✓
- Copying files: 2 min
- Updating Docker: 2 min
- Starting services: 1 min
- Running seed script: 1 min
- Testing: 2 min
- **Total: ~15 minutes**

---

## 🚨 If Something Goes Wrong

**"ModuleNotFoundError: No module named 'src.claimbridge.weaviate'"**
→ Check `src/claimbridge/weaviate/__init__.py` exists and exports classes

**"Connection refused: weaviate:8080"**
→ Check Weaviate container is running: `docker-compose ps`

**"RAG orchestrator not initialized"**
→ Check `initialize_rag_orchestrator()` is called in main.py startup event

**"No documents found"**
→ Run seed script: `docker-compose exec claimbridge python -m src.claimbridge.scripts.seed_weaviate`

See `INTEGRATION_INSTRUCTIONS.md` troubleshooting section for more.

---

**Next step:** Copy `nodes_updated.py` to your `nodes.py` file and follow Step 2 of INTEGRATION_INSTRUCTIONS.md.

You're almost there! 🎉
