"""
A stand-in ClaimBridge for the MCP tool tests.

The point of these tests is the MCP layer -- what the tools return, what they
refuse, what arguments they do and do not take. Whether ClaimBridge itself is
correct is settled by the other suites, so here it is replaced by the smallest
server that speaks the right shapes. No database, no Weaviate, no OpenAI.
"""

import importlib.util
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

PACIFIC_KEY = "cbk_test_pacific"
TENANT_WIDE_KEY = "cbk_test_global"
PORT = 8931

CLAIM = {
    "tenant_id": "pacific-hmo", "claim_id": "CLAIM-PH-004", "claim_type": "professional",
    "member_id": "PH-10015678", "date_of_service": "2025-11-05",
    "provider_name": "Northwest MRI Center", "source": "fixture", "intake_status": "COMPLETE",
    "validation_issues": [], "claim_data": {"cpt": "72148", "icd10": "M54.5"},
    "adjudication": {"outcome": "DENY", "carc_codes": ["CO-197"], "billed_amount": "1850.00"},
    "recommendation": {"recommendation": "DENY", "rationale": "No prior auth on file",
                       "rules_version": "rules-2026-09-22.1"},
    "communications": [{"id": 41, "audience": "member", "status": "PENDING_REVIEW"}],
}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)
        key = self.headers.get("X-Api-Key")
        if key not in (PACIFIC_KEY, TENANT_WIDE_KEY):
            return self._send(401, {"detail": "Missing or invalid X-Api-Key"})

        if url.path == "/v1/whoami":
            if key == TENANT_WIDE_KEY:
                return self._send(200, {"principal_id": "admin", "role": "admin",
                                        "tenant_id": None, "permissions": ["claims:read"]})
            return self._send(200, {"principal_id": "mcp-demo", "role": "auditor",
                                    "tenant_id": "pacific-hmo",
                                    "permissions": ["audit:read", "claims:read", "review:read"]})

        parts = url.path.strip("/").split("/")
        if len(parts) >= 3 and parts[0] == "v1" and parts[1] == "tenants":
            tenant, rest = parts[2], parts[3:]
            if tenant != "pacific-hmo":
                return self._send(403, {"detail": f"This key is not valid for tenant '{tenant}'"})
            if rest[:1] == ["claims"]:
                if rest[1] != CLAIM["claim_id"]:
                    return self._send(404, {"detail": f"Claim '{rest[1]}' not found"})
                return self._send(200, CLAIM)
            if rest[:1] == ["policy-search"]:
                return self._send(200, {
                    "tenant_id": tenant, "query": query["q"][0], "degraded": False, "note": None,
                    "sections": [{"doc_key": "pacific-hmo-plan-summary",
                                  "document_title": "Pacific HMO - Plan Summary",
                                  "section_path": "prior-auth.imaging", "section_title": "Imaging",
                                  "content": "MRI requires prior authorization.",
                                  "effective_date": "2025-01-01", "corpus_version": "abc",
                                  "score": 0.9550727}]})
            if rest[:1] == ["codes"]:
                codes = query.get("code", [])
                return self._send(200, {
                    "known": [{"code": c, "kind": "CARC", "title": "Precertification absent",
                               "member_friendly_name": "Prior authorization required",
                               "fields": {"typical_meaning": "needed prior approval"}}
                              for c in codes if c.startswith("CO-")],
                    "unknown": [c for c in codes if not c.startswith("CO-")]})
            if rest[:1] == ["review-queue"]:
                return self._send(200, [{"id": 41, "claim_id": "CLAIM-PH-004", "audience": "member",
                                         "status": "PENDING_REVIEW",
                                         "created_at": "2026-09-24T13:29:23",
                                         "needs_human_review": True}])
        return self._send(404, {"detail": "no such route"})


@pytest.fixture(scope="session")
def fake_api():
    server = HTTPServer(("127.0.0.1", PORT), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{PORT}"
    server.shutdown()


@pytest.fixture()
def mcp_server(fake_api, monkeypatch):
    """
    Load server.py the way its Docker image does -- as a standalone file, not
    as part of the claimbridge package. The image copies only this one file, so
    importing it any other way here would test something that cannot happen.
    """
    pytest.importorskip("mcp", reason="MCP SDK lives in requirements-mcp.txt, not requirements.txt")
    monkeypatch.setenv("CLAIMBRIDGE_API_URL", fake_api)
    monkeypatch.setenv("CLAIMBRIDGE_API_KEY", PACIFIC_KEY)
    path = Path(__file__).resolve().parents[2] / "src" / "claimbridge" / "mcp" / "server.py"
    spec = importlib.util.spec_from_file_location("claimbridge_mcp_server", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
