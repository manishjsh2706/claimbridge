"""Pytest configuration and fixtures."""

import pytest
from fastapi.testclient import TestClient
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from claimbridge.main import app

@pytest.fixture
def client():
    """FastAPI test client."""
    return TestClient(app)

@pytest.fixture
def sample_claim():
    """Sample claim data for testing."""
    return {
        "member_id": "MEM-001",
        "claim_amount": 1500.00,
        "service_date": "2024-09-15",
        "description": "Fertility treatment consultation",
    }
