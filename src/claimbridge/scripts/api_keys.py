"""
Manage API keys - ClaimBridge
=============================

    docker compose exec claimbridge python -m src.claimbridge.scripts.api_keys create reviewer-demo --role reviewer --tenant pacific-hmo
    docker compose exec claimbridge python -m src.claimbridge.scripts.api_keys list
    docker compose exec claimbridge python -m src.claimbridge.scripts.api_keys revoke reviewer-demo

`create` prints the key ONCE; only its hash is stored. Running `create` again
for the same principal rotates the key (the old one stops working).
"""

import argparse
import sys

from src.claimbridge.auth import ROLE_PERMISSIONS, issue_key, revoke
from src.claimbridge.db import session_scope
from src.claimbridge.models import ApiPrincipal, Tenant


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Manage ClaimBridge API keys")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create", help="create a principal or rotate its key")
    c.add_argument("principal_id")
    c.add_argument("--role", required=True, choices=sorted(ROLE_PERMISSIONS))
    c.add_argument("--tenant", help="restrict the key to one tenant (omit for platform-wide)")
    sub.add_parser("list")
    r = sub.add_parser("revoke")
    r.add_argument("principal_id")
    args = p.parse_args(argv)

    with session_scope() as s:
        if args.cmd == "create":
            if args.tenant and s.get(Tenant, args.tenant) is None:
                print(f"Unknown tenant {args.tenant}", file=sys.stderr)
                return 2
            key = issue_key(s, args.principal_id, args.role, args.tenant)
            print(f"principal : {args.principal_id}\nrole      : {args.role}\n"
                  f"tenant    : {args.tenant or '* (all tenants)'}\nAPI key   : {key}\n"
                  f"(shown once - store it now; only its hash is kept)")
        elif args.cmd == "list":
            for row in s.query(ApiPrincipal).order_by(ApiPrincipal.principal_id):
                print(f"{row.principal_id:28} {row.role:10} {row.tenant_id or '*':16} "
                      f"{'active' if row.active else 'REVOKED'}")
        else:
            print("revoked" if revoke(s, args.principal_id) else "no such principal")
    return 0


if __name__ == "__main__":
    sys.exit(main())
