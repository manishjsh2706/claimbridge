"""
Submit a sample-claims fixture to the running API - ClaimBridge demo
====================================================================

    docker compose exec claimbridge python -m src.claimbridge.scripts.submit_fixture CLAIM-PH-002
    ... CLAIM-PH-004 --show-payload         print the JSON that is sent
    ... CLAIM-PH-004 --key demo-ph-004-1    choose the Idempotency-Key (re-run = replay)
    ... CLAIM-PH-004 --drafts               then run the draft pipeline (member + provider -> review)

Authentication: the script runs inside the API container with database
access, so it issues (or rotates) a submitter key for principal
"cli:<actor>", scoped to the fixture's tenant, and uses it for this run.

The payload is built from resources/sample-claims.md (intake/fixtures.py), so
the demo submits exactly the provided fixture. Prints the HTTP status and the
API response.
"""

import argparse
import json
import os
import sys

import httpx

from src.claimbridge.auth import issue_key
from src.claimbridge.db import session_scope
from src.claimbridge.intake import fixture_payload, load_fixture


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Submit a fixture claim to the ClaimBridge API")
    p.add_argument("claim_id")
    p.add_argument("--key", help="Idempotency-Key (default: demo-<claim_id>)")
    p.add_argument("--actor", default="adjuster-demo")
    p.add_argument("--show-payload", action="store_true")
    p.add_argument("--drafts", action="store_true", help="after submitting, generate member + provider drafts")
    p.add_argument("--base-url", default=os.getenv("CLAIMBRIDGE_API_URL", "http://localhost:8000"))
    args = p.parse_args(argv)

    try:
        fixture = load_fixture(args.claim_id)
    except KeyError as e:
        print(e, file=sys.stderr)
        return 2
    payload = fixture_payload(args.claim_id)
    if args.show_payload:
        print("PAYLOAD\n" + json.dumps(payload, indent=2))

    with session_scope() as s:
        api_key = issue_key(s, f"cli:{args.actor}", "submitter", fixture.tenant_id)
    auth = {"X-Api-Key": api_key}

    key = args.key or f"demo-{args.claim_id.lower()}"
    url = f"{args.base_url}/v1/tenants/{fixture.tenant_id}/claims"
    resp = httpx.post(url, json=payload, timeout=180.0,
                      headers={"Idempotency-Key": key, **auth})
    print(f"POST {url}  ->  HTTP {resp.status_code}")
    try:
        print(json.dumps(resp.json(), indent=2))
    except ValueError:
        print(resp.text)
    if resp.status_code >= 400:
        return 1
    if args.drafts:
        url = f"{args.base_url}/v1/tenants/{fixture.tenant_id}/claims/{args.claim_id}/drafts"
        d = httpx.post(url, timeout=300.0, headers=auth)
        print(f"\nPOST {url}  ->  HTTP {d.status_code}")
        if d.status_code >= 400:
            print(d.text)
            return 1
        body = d.json()
        print(f"recommendation : {body['recommendation']['recommendation']}")
        for audience in ("member", "provider"):
            if audience in body["skipped"]:
                print(f"{audience:9}: skipped - {body['skipped'][audience]}")
            elif audience in body["routing"]:
                r = body["routing"][audience]
                v = body[audience]["validation"]
                print(f"{audience:9}: communication {r['communication_id']}  status {r['status']}  "
                      f"mode {v['generation_mode']}  guards {'passed' if v['passed'] else 'FAILED'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
