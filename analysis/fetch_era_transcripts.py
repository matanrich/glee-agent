"""Fetch transcripts for a sample of games from one policy era.

Usage: GLEE_API_KEY=... python analysis/fetch_era_transcripts.py \
           --history analysis/history.jsonl --since 2026-08-27T05:47:00 \
           --per-family 700 --ids-out analysis/final_ids.txt

Samples completed games from the platform history feed (completed_at >= since),
stratified per family and balanced across roles, and fetches each transcript
through the agent API into games/ (skipping ones already cached). The API allows
60 requests/minute; we stay under it.
"""

import argparse
import json
import os
import random
import time

import requests

API = "https://glee-competition.com/api/agent/games/"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", default="analysis/history.jsonl")
    ap.add_argument("--since", required=True)
    ap.add_argument("--until", default="9999")
    ap.add_argument("--per-family", type=int, default=700)
    ap.add_argument("--games", default="games")
    ap.add_argument("--ids-out", required=True)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    key = os.environ["GLEE_API_KEY"]
    rows = [json.loads(l) for l in open(args.history) if l.strip()]
    pool = {}
    for r in rows:
        ts = r["completed_at"][:19]
        if r.get("status") not in ("completed", "no_deal") or not (args.since <= ts < args.until):
            continue
        pool.setdefault((r["game_family"], r["your_player"]), []).append(r["game_id"])

    rng = random.Random(args.seed)
    chosen = []
    for (fam, role), ids in sorted(pool.items()):
        rng.shuffle(ids)
        chosen += ids[: args.per_family // 2]
    with open(args.ids_out, "w") as f:
        f.write("\n".join(chosen) + "\n")
    print(f"selected {len(chosen)} games across {len(pool)} (family, role) strata")

    os.makedirs(args.games, exist_ok=True)
    fetched = skipped = failed = 0
    for gid in chosen:
        path = os.path.join(args.games, f"{gid}.json")
        if os.path.exists(path):
            skipped += 1
            continue
        try:
            r = requests.get(API + gid, headers={"Authorization": f"Bearer {key}"}, timeout=30)
        except requests.RequestException:
            failed += 1
            time.sleep(5)
            continue
        if r.status_code == 429:
            time.sleep(float(r.headers.get("Retry-After", 15)))
            continue
        if not r.ok:
            failed += 1
            continue
        with open(path, "w") as f:
            f.write(r.text)
        fetched += 1
        if fetched % 100 == 0:
            print(f"fetched {fetched}", flush=True)
        time.sleep(1.1)  # ~55 requests/minute
    print(f"done: fetched {fetched}, already cached {skipped}, failed {failed}")


if __name__ == "__main__":
    main()
