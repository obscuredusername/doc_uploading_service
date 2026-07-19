"""
Bootstrap a tenant row.

Usage:
    python scripts/create_tenant.py --name "case-assessment-backend" \\
        --broker-url "redis://localhost:6379/2"

    # With a fixed api_key (useful in CI / dev where you want a known value):
    python scripts/create_tenant.py --name dev --broker-url redis://r/0 \\
        --api-key dev-key-please-rotate

Prints the resulting row as JSON. The api_key is the value to send as
`Authorization: Bearer <api_key>` on every request.
"""
import argparse
import json
import secrets
import sys

from sqlalchemy import select

from app.db.session import SyncSessionLocal
from app.models.tenant import Tenant


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Create a tenant.")
    p.add_argument("--name", required=True, help="Tenant display name")
    p.add_argument(
        "--broker-url",
        required=True,
        help="Celery broker URL the microservice will publish notifications to",
    )
    p.add_argument(
        "--api-key",
        default=None,
        help="Optional fixed api_key (default: 32-byte hex secret).",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    api_key = args.api_key or secrets.token_hex(32)

    with SyncSessionLocal() as db:
        existing = db.execute(select(Tenant).where(Tenant.name == args.name)).scalar_one_or_none()
        if existing is not None:
            print(
                f"ERROR: tenant with name {args.name!r} already exists (id={existing.id}).",
                file=sys.stderr,
            )
            return 1

        tenant = Tenant(name=args.name, api_key=api_key, broker_url=args.broker_url)
        db.add(tenant)
        db.commit()
        db.refresh(tenant)

        print(
            json.dumps(
                {
                    "id": str(tenant.id),
                    "name": tenant.name,
                    "api_key": tenant.api_key,
                    "broker_url": tenant.broker_url,
                    "created_at": tenant.created_at.isoformat(),
                },
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
