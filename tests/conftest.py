"""
Pytest configuration and fixtures.

NOTHING USES THE FIXTURES BELOW. They are left over from tests/unit/test_api.py,
which was removed, and are kept only in case a future API test wants them.

WHY THE IMPORTS ARE INSIDE THE FIXTURES
pytest loads every conftest.py from the rootdir down, so this file is imported
even by `pytest tests/mcp`. With `from fastapi.testclient import TestClient` at
module level, that collection failed outright in the MCP job, whose environment
deliberately has no FastAPI -- the MCP server reaches ClaimBridge over HTTP and
has no business importing the application. Deferring the imports keeps this file
harmless to anyone who is not actually asking for a FastAPI client.
"""

import sys
from pathlib import Path

import pytest

# Imports in the test modules are written as `from src.claimbridge...`, which
# needs the repository root on the path; `src` is also added so `claimbridge...`
# resolves for anything that spells it that way.
_ROOT = Path(__file__).resolve().parent.parent
for _path in (_ROOT, _ROOT / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))


@pytest.fixture
def client():
    """FastAPI test client. Requires FastAPI, so it is imported on use."""
    from fastapi.testclient import TestClient

    from claimbridge.main import app
    return TestClient(app)


@pytest.fixture
def sample_claim():
    return {
        "member_id": "MEM-001",
        "claim_amount": 1500.00,
        "service_date": "2024-09-15",
        "description": "Fertility treatment consultation",
    }
