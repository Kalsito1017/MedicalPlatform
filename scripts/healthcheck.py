"""HTTP healthcheck for the Clinical AI Platform backend.

Exits 0 when the target endpoint reports healthy, 1 otherwise. Intended for
local smoke checks and container healthchecks.

Usage:
    cd backend && uv run python ../scripts/healthcheck.py --url http://localhost:8000
"""
from __future__ import annotations

import argparse
import sys

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description="Check backend health endpoints.")
    parser.add_argument("--url", default="http://localhost:8000", help="Backend base URL")
    parser.add_argument("--endpoint", default="/health/ready", choices=["/health/live", "/health/ready"])
    parser.add_argument("--timeout", type=float, default=5.0, help="Request timeout in seconds")
    args = parser.parse_args()

    target = args.url.rstrip("/") + args.endpoint
    try:
        response = httpx.get(target, timeout=args.timeout)
    except httpx.HTTPError as exc:
        print(f"UNHEALTHY {target}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if response.status_code == 200:
        print(f"HEALTHY {target}: {response.text.strip()}")
        return 0
    print(f"UNHEALTHY {target}: HTTP {response.status_code} {response.text.strip()}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
