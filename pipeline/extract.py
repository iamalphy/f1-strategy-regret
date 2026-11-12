#!/usr/bin/env python3
"""
Extract 2024 race sessions from the OpenF1 API into a local JSON cache.

OpenF1 is free and needs no authentication, but it enforces a rate limit of
3 requests/second, so every call goes through a throttle and every response
is cached on disk. Re-running the pipeline therefore costs nothing and works
offline once the cache is warm.

Usage:
    python pipeline/extract.py            # all 2024 races
    python pipeline/extract.py --force    # ignore cache
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request

BASE = "https://api.openf1.org/v1"
YEAR = 2024
# Stay comfortably under the documented limit of 3 requests/second.
MIN_INTERVAL_S = 0.55
MAX_RETRIES = 8

ENDPOINTS = ("drivers", "laps", "stints", "pit", "position", "race_control", "weather")

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"

_last_call = 0.0


def _throttle() -> None:
    global _last_call
    elapsed = time.monotonic() - _last_call
    if elapsed < MIN_INTERVAL_S:
        time.sleep(MIN_INTERVAL_S - elapsed)
    _last_call = time.monotonic()


def get(path: str):
    """GET an OpenF1 path, throttled, with backoff on rate limiting."""
    url = f"{BASE}/{path}"
    for attempt in range(1, MAX_RETRIES + 1):
        _throttle()
        req = urllib.request.Request(url, headers={"User-Agent": "f1-strategy-regret/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 503):
                wait = 2 ** attempt
                print(f"    rate limited (HTTP {exc.code}), retrying in {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            # OpenF1 returns 404 with {"detail": "No results found."} for empty slices
            if exc.code == 404:
                return []
            raise
        except (urllib.error.URLError, TimeoutError) as exc:
            wait = 2 ** attempt
            print(f"    network error ({exc}), retrying in {wait}s", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"giving up on {url} after {MAX_RETRIES} attempts")


def race_sessions(year: int = YEAR) -> list[dict]:
    sessions = get(f"sessions?year={year}&session_name=Race")
    # Grands Prix only; sprint sessions are excluded by the design.
    sessions = [s for s in sessions if s.get("session_name") == "Race"]
    sessions.sort(key=lambda s: s["date_start"])
    return sessions


def extract_session(session: dict, force: bool = False) -> None:
    key = session["session_key"]
    out_dir = RAW / str(key)
    out_dir.mkdir(parents=True, exist_ok=True)

    meta_path = out_dir / "session.json"
    if force or not meta_path.exists():
        meta_path.write_text(json.dumps(session, indent=2), encoding="utf-8")

    label = f"{session.get('country_name','?')} ({session.get('location','?')})"
    print(f"[{key}] {label}")

    for endpoint in ENDPOINTS:
        path = out_dir / f"{endpoint}.json"
        if path.exists() and not force:
            print(f"    {endpoint}: cached")
            continue
        rows = get(f"{endpoint}?session_key={key}")
        path.write_text(json.dumps(rows), encoding="utf-8")
        print(f"    {endpoint}: {len(rows)} records")


def main() -> int:
    parser = argparse.ArgumentParser(description="Cache OpenF1 race data")
    parser.add_argument("--force", action="store_true", help="ignore the cache and refetch")
    parser.add_argument("--year", type=int, default=YEAR)
    args = parser.parse_args()

    sessions = race_sessions(args.year)
    print(f"found {len(sessions)} race sessions for {args.year}")

    manifest = []
    for session in sessions:
        extract_session(session, force=args.force)
        manifest.append(session)

    (RAW / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\ncached {len(manifest)} sessions under {RAW}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
