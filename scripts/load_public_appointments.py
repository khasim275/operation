"""Load-test the public booking endpoint with unique synthetic patients.

Example: python scripts/load_public_appointments.py --base-url http://127.0.0.1:8001 --users 1000 --concurrency 1000
Run only against an isolated test database. Every request creates a test record.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import statistics
import time
from datetime import date, timedelta

import httpx


async def main() -> int:
    parser = argparse.ArgumentParser(description="Stress-test atomic public appointment booking.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--users", type=int, choices=(100, 1000), required=True)
    parser.add_argument("--concurrency", type=int, default=None)
    args = parser.parse_args()
    concurrency = min(args.concurrency or args.users, args.users)
    appointment_date = (date.today() + timedelta(days=31 + secrets.randbelow(200))).isoformat()
    time_slot = "10:00"
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=min(concurrency, 100))
    timeout = httpx.Timeout(90, connect=30)
    semaphore = asyncio.Semaphore(concurrency)
    durations: list[float] = []
    failures: list[str] = []
    results: list[dict] = []

    async with httpx.AsyncClient(base_url=args.base_url, limits=limits, timeout=timeout) as client:
        async def book(index: int) -> None:
            phone_suffix = f"{secrets.randbelow(10**10):010d}"
            payload = {
                "name": f"Load Test Patient {index:04d}",
                "phone": f"+9199{phone_suffix}",
                "appointment_date": appointment_date,
                "time_slot": time_slot,
            }
            started = time.perf_counter()
            async with semaphore:
                try:
                    response = await client.post(
                        "/api/public/appointments/book",
                        json=payload,
                        headers={"Idempotency-Key": secrets.token_urlsafe(24)},
                    )
                    durations.append(time.perf_counter() - started)
                    if response.status_code != 201:
                        failures.append(f"{response.status_code}: {response.text[:180]}")
                    else:
                        results.append(response.json())
                except Exception as exc:  # Report transport errors without hiding the rest of the run.
                    failures.append(f"{type(exc).__name__}: {exc}")

        started = time.perf_counter()
        await asyncio.gather(*(book(index) for index in range(args.users)))
        elapsed = time.perf_counter() - started

        ids = [item["opid"] for item in results]
        tokens = [item["token_no"] for item in results]
        sample_ids = ids[: min(20, len(ids))]
        verified = 0
        for opid in sample_ids:
            response = await client.get(f"/api/public/appointments/{opid}")
            if response.status_code == 200 and response.json()["opid"] == opid:
                verified += 1

    percentile_95 = sorted(durations)[min(len(durations) - 1, int(len(durations) * 0.95))] if durations else None
    report = {
        "users": args.users,
        "concurrency": concurrency,
        "successes": len(results),
        "failures": len(failures),
        "unique_opids": len(set(ids)),
        "unique_tokens": len(set(tokens)),
        "sample_lookup_checks": verified,
        "sample_lookup_total": len(sample_ids),
        "elapsed_seconds": round(elapsed, 2),
        "requests_per_second": round(len(results) / elapsed, 2) if elapsed else 0,
        "p50_seconds": round(statistics.median(durations), 3) if durations else None,
        "p95_seconds": round(percentile_95, 3) if percentile_95 is not None else None,
        "sample_errors": failures[:5],
    }
    print(json.dumps(report, indent=2))
    return int(bool(failures or len(results) != args.users or len(set(ids)) != len(ids)
                    or len(set(tokens)) != len(tokens) or verified != len(sample_ids)))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
