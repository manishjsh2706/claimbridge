# ClaimBridge Weaviate Integration - Installation Guide

**Status:** Ready to integrate into your project  
**Date:** September 17, 2026  
**Your Project:** D:\GenAI\claims-assistant-essential

---

## 📋 Quick Summary

You now have **9 production-ready files** created in the cloud workspace. This guide tells you **exactly where** to place each file in your project and what changes to make.

---

## 📁 File Placement Map

### Files Already in Your Project (From Previous Work)

```
✅ src/claimbridge/weaviate/
   ├── __init__.py (created)
   ├── client.py (created - from weaviate_client.py)
   └── orchestrator.py (created - from weaviate_integration.py)

✅ src/claimbridge/scripts/
   ├── __init__.py (created)
   └── seed_weaviate.py (created)

✅ src/claimbridge/langgraph/
   ├── nodes_with_weaviate.py (created - reference implementation)
   └── state.py (existing - no changes needed)

✅ docs/
   ├── WEAVIATE_ARCHITECTURE_SUMMARY.md (created)
   ├── WEAVIATE_INTEGRATION_GUIDE.md (created)
   ├── INTERVIEW_PREPARATION_GUIDE.md (created)
   └── README_WEAVIATE_INTEGRATION.md (created)
```

---

## 🔧 Two Files That Need Your Attention

### 1. **Update: `src/claimbridge/langgraph/nodes.py`**

Your current `nodes.py` has mock data in `retrieve_documents()` (lines 96-153).

**What to do:**
- Replace the entire `retrieve_documents()` function with the real Weaviate integration
- Add these imports at the top of the file:
  ```python
  from src.claimbridge.weaviate import create_rag_orchestrator
  ```

- Add this new function before the node functions:
  ```python
  # Global RAG orchestrator (initialized on app startup)
  _rag_orchestrator = None

  def initialize_rag_orchestrator(weaviate_url: str = "http://localhost:8080"):
      """
      Initialize the RAG orchestrator on application startup.
      
      INTERVIEW POINT: Resource initialization pattern
      - Called once during app startup
      - NOT per-request (would waste resources)
      - Shares connection pool across all claims
      
      Args:
          weaviate_url: Weaviate server URL (default: localhost:8080)
      """
      global _rag_orchestrator
      _rag_orchestrator = create_rag_orchestrator(weaviate_url)
      logger.info("[INIT] RAG orchestrator initialized")
  ```

- Replace `retrieve_documents()` function (lines 96-153) with the real implementation that:
  1. Checks if orchestrator is initialized
  2. Builds claim query from normalized claim
  3. Calls `_rag_orchestrator.retrieve_claim_context()`
  4. Returns retrieved documents and context

**See:** `nodes_updated.py` in `/mnt/user-data/outputs/` for complete code

---

### 2. **Update: `src/claimbridge/main.py`**

Your current `main.py` needs to initialize the RAG orchestrator on startup.

**What to do:**
- Add this import at the top:
  ```python
  from src.claimbridge.langgraph.nodes import initialize_rag_orchestrator
  ```

- In the `@app.on_event("startup")` function, add this call:
  ```python
  logger.info("\n[STARTUP] Initializing RAG orchestrator...")
  initialize_rag_orchestrator(weaviate_url="http://weaviate:8080")
  logger.info("✓ RAG orchestrator ready")
  ```

This ensures Weaviate connection is ready BEFORE any claims are processed.

**See:** `main_updated.py` in `/mnt/user-data/outputs/` for complete code

---

## 🚀 Step-by-Step Integration

### Step 1: Copy Files (Already Done ✅)
Files are already in your project directories from previous work.

### Step 2: Update `nodes.py` 
Replace the mock `retrieve_documents()` with real Weaviate integration.

**Recommended approach:**
1. Copy the complete updated `nodes.py` from `/mnt/user-data/outputs/nodes_updated.py`
2. Replace your current `src/claimbridge/langgraph/nodes.py`
3. Verify imports are correct

**Key changes:**
- Adds `initialize_rag_orchestrator()` function
- Updates `retrieve_documents()` to use real Weaviate
- Adds safety check for orchestrator initialization
- Imports from `src.claimbridge.weaviate` package

### Step 3: Update `main.py`
Add RAG orchestrator initialization in startup handler.

**Recommended approach:**
1. Copy the complete updated `main.py` from `/mnt/user-data/outputs/main_updated.py`
2. Replace your current `src/claimbridge/main.py`
3. Verify imports are correct

**Key changes:**
- Imports `initialize_rag_orchestrator` from nodes
- Calls initialization in `@app.on_event("startup")`
- Uses Weaviate URL based on environment (Docker: `http://weaviate:8080`)

### Step 4: Verify Imports

After updating files, verify these imports exist:

**In `nodes.py`:**
```python
from src.claimbridge.weaviate import create_rag_orchestrator
```

**In `main.py`:**
```python
from src.claimbridge.langgraph.nodes import initialize_rag_orchestrator
```

**In `src/claimbridge/weaviate/__init__.py`:**
```python
from .client import WeaviateClient
from .orchestrator import WeaviateRAGOrchestrator, create_rag_orchestrator
```

### Step 5: Update `requirements.txt`

Ensure these packages are included:
```
weaviate-client>=4.0.0
langchain>=0.0.300
langgraph>=0.0.1
fastapi>=0.100.0
pydantic>=2.0.0
```

### Step 6: Docker Setup (if not already done)

Update `docker-compose.yml` to include Weaviate:

```yaml
version: '3.8'
services:
  weaviate:
    image: semitechnologies/weaviate:latest
    ports:
      - "8080:8080"
    environment:
      QUERY_DEFAULTS_LIMIT: 25
      DEFAULT_VECTORIZER_MODULE: text2vec-openai
      MODELS: "text-embedding-3-small"

  claimbridge:
    build: .
    ports:
      - "8000:8000"
    depends_on:
      - weaviate
    environment:
      WEAVIATE_URL: "http://weaviate:8080"
```

### Step 7: Initialize Weaviate Collections

Before running the app for the first time, seed the database:

```bash
# Start Weaviate
docker-compose up -d weaviate

# Run seed script
docker-compose exec claimbridge python -m src.claimbridge.scripts.seed_weaviate

# Verify seeding
curl http://localhost:8080/v1/meta
```

### Step 8: Start the Application

```bash
# Start everything
docker-compose up -d

# Check health
curl http://localhost:8000/health

# Try a test claim
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

## ✅ Verification Checklist

After integration, verify:

- [ ] `src/claimbridge/weaviate/` exists with `client.py` and `orchestrator.py`
- [ ] `src/claimbridge/scripts/` exists with `seed_weaviate.py`
- [ ] `nodes.py` has `initialize_rag_orchestrator()` function
- [ ] `nodes.py` has `retrieve_documents()` calling real Weaviate (not mock data)
- [ ] `main.py` calls `initialize_rag_orchestrator()` in startup event
- [ ] `weaviate/__init__.py` exports `create_rag_orchestrator`
- [ ] Weaviate container is running (`docker-compose ps`)
- [ ] Collections are created and seeded
- [ ] Health check endpoint works (`/health`)
- [ ] Single claim processing works (`/claims/process`)
- [ ] Logs show all 5 nodes executing
- [ ] Multi-tenant isolation verified (Company A claims separate from Company B)

---

## 📂 Complete File Structure After Integration

```
D:\GenAI\claims-assistant-essential\
├── src/claimbridge/
│   ├── weaviate/
│   │   ├── __init__.py
│   │   ├── client.py (408 lines)
│   │   └── orchestrator.py (380 lines)
│   ├── scripts/
│   │   ├── __init__.py
│   │   └── seed_weaviate.py (380 lines)
│   ├── langgraph/
│   │   ├── nodes.py (UPDATED - with real Weaviate)
│   │   ├── nodes_with_weaviate.py (reference)
│   │   ├── state.py (no changes)
│   │   └── workflow.py (no changes)
│   ├── main.py (UPDATED - with startup initialization)
│   ├── main_with_weaviate.py (reference)
│   └── ...other modules...
├── docs/
│   ├── README_WEAVIATE_INTEGRATION.md
│   ├── WEAVIATE_ARCHITECTURE_SUMMARY.md
│   ├── WEAVIATE_INTEGRATION_GUIDE.md
│   ├── INTERVIEW_PREPARATION_GUIDE.md
│   └── ...other docs...
├── docker-compose.yml (UPDATED with Weaviate)
├── requirements.txt (UPDATED with weaviate-client)
└── ...other project files...
```

---

## 🎯 What Each File Does

### Weaviate Package (`src/claimbridge/weaviate/`)

- **client.py**: Low-level Weaviate operations (connect, query, index)
- **orchestrator.py**: High-level RAG workflow (retrieve policies + guidelines + history)
- **__init__.py**: Exports `create_rag_orchestrator()` factory function

### Scripts Package (`src/claimbridge/scripts/`)

- **seed_weaviate.py**: Initializes Weaviate with sample policies, guidelines, and historical claims for 2 companies

### Updated Nodes (`langgraph/nodes.py`)

- **initialize_rag_orchestrator()**: Called once on app startup
- **retrieve_documents()**: NOW calls real Weaviate instead of returning mock data
- Other nodes (validate, generate, quality_check, publish): Unchanged

### Updated Main (`main.py`)

- **@app.on_event("startup")**: NOW calls `initialize_rag_orchestrator()`
- All endpoints remain the same
- dependency injection for company_id and customer_id validation

---

## 🔐 Security Verification

After integration, verify multi-tenant isolation:

```bash
# Process claim for Company A
curl -X POST http://localhost:8000/claims/process \
  -H "X-Company-Id: hdfc-life" \
  -H "X-Customer-Id: alice" \
  -H "Content-Type: application/json" \
  -d '{
    "claim_number": "CLM-001",
    "policy_number": "POL-001",
    "amount": 1000,
    "service_date": "2024-01-15",
    "description": "Test claim for company A"
  }'

# Process claim for Company B (different company, similar claim)
curl -X POST http://localhost:8000/claims/process \
  -H "X-Company-Id: axa-insurance" \
  -H "X-Customer-Id: bob" \
  -H "Content-Type: application/json" \
  -d '{
    "claim_number": "CLM-001",
    "policy_number": "POL-001",
    "amount": 1000,
    "service_date": "2024-01-15",
    "description": "Test claim for company B"
  }'

# RESULT: Both claims are stored SEPARATELY
# - Company A cannot see Company B's claim
# - Company A cannot see Company B's policies
# - Weaviate queries are filtered by company_id
```

---

## 📚 Reference Files in Cloud Workspace

All these files are available in `/mnt/user-data/outputs/`:

1. **nodes_updated.py** - Complete updated nodes.py (copy this to your project)
2. **main_updated.py** - Complete updated main.py (copy this to your project)
3. **nodes_with_weaviate.py** - Reference implementation (shows how integration works)
4. **main_with_weaviate.py** - Reference implementation (complete FastAPI example)
5. **WEAVIATE_ARCHITECTURE_SUMMARY.md** - Deep technical architecture
6. **WEAVIATE_INTEGRATION_GUIDE.md** - Setup and troubleshooting
7. **INTERVIEW_PREPARATION_GUIDE.md** - Interview cheat sheet
8. **README_WEAVIATE_INTEGRATION.md** - Package overview

---

## 🎓 Key Architectural Points

### RAG Pattern
- **Before RAG**: Claim → LLM → Guess whether covered
- **After RAG**: Claim + [Retrieved Policies] → LLM → Informed decision

### Multi-Tenant Isolation (3 Layers)
1. **API Layer**: `verify_company_id()` validates headers
2. **Application Logic**: `company_id` from state (not request body)
3. **Database Queries**: `WHERE company_id = 'X'` in every Weaviate query

### LangGraph Workflow
```
Validate → Retrieve (WEAVIATE!) → Generate → QualityCheck → Publish
```

The key difference: **RETRIEVE node now gets real policies from Weaviate**, not mock data.

---

## 🚨 Common Issues & Fixes

### Issue: "RAG orchestrator not initialized"
**Fix:** Make sure `initialize_rag_orchestrator()` is called in `main.py` startup event

### Issue: "No documents found"
**Fix:** Run seed script to populate Weaviate with sample data

### Issue: "Weaviate connection refused"
**Fix:** Check Weaviate is running (`docker ps`) and accessible at configured URL

### Issue: "Company A seeing Company B's data"
**Fix:** Verify `WHERE company_id = X` filter is in every Weaviate query

### Issue: "Import error for weaviate module"
**Fix:** Verify `src/claimbridge/weaviate/__init__.py` exports the right classes

---

## ✨ Next Steps

1. **Copy the 2 updated files** (`nodes_updated.py` → `nodes.py`, `main_updated.py` → `main.py`)
2. **Verify imports** work correctly
3. **Update docker-compose.yml** to include Weaviate service
4. **Update requirements.txt** with weaviate-client
5. **Start services** (`docker-compose up`)
6. **Seed data** (run seed_weaviate.py)
7. **Test** with sample claims
8. **Verify multi-tenant** isolation

---

**Ready to integrate?** Start with Step 2 above.

Need help? Check the troubleshooting section or review the reference implementations (`nodes_with_weaviate.py` and `main_with_weaviate.py`).

---

**Created:** September 17, 2026  
**For:** Manish Joshi  
**Project:** ClaimBridge Weaviate Integration
