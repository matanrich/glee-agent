"""Official per-game rating deltas by policy era, from the platform history feed.

Usage: python analysis/history_eras.py [analysis/history.jsonl]

The feed (one JSON object per line) carries, for every game the agent played:
family, role, outcome, payoffs, completed_at, and the platform's own
rating_delta — the opponent-adjusted score movement it assigned to that game.
Eras are the code versions in production, delimited by deploy times (UTC).
"""

import json
import sys
from collections import defaultdict

DEPLOYS = [  # (era start, UTC), era label
    ("2026-08-25T00:00:00", "equilibrium"),
    ("2026-08-26T05:53:00", "quantile v1"),
    ("2026-08-26T21:24:00", "quantile + continuation"),
    ("2026-08-27T05:47:00", "final (+ empirical floors)"),
]


def era_of(ts):
    label = None
    for start, name in DEPLOYS:
        if ts >= start:
            label = name
    return label


def main(path):
    rows = [json.loads(l) for l in open(path) if l.strip()]
    cells = defaultdict(list)
    for r in rows:
        if r.get("status") not in ("completed", "no_deal"):
            continue
        era = era_of(r["completed_at"][:19])
        role = "p1" if r["your_player"] == "player_1" else "p2"
        res = r.get("result") or {}
        pay = res.get(f"{r['your_player']}_payoff")
        agreed = res.get("outcome") not in ("no_deal", "timeout")
        cells[(r["game_family"], role, era)].append((r.get("rating_delta"), pay, agreed))

    print(f"{len(rows)} games in feed\n")
    print("| family | role | era | games | agreement | mean rating Δ/game | mean payoff |")
    print("|---|---|---|---|---|---|---|")
    for fam in ("bargaining", "negotiation", "persuasion"):
        for role in ("p1", "p2"):
            for _, era in DEPLOYS:
                obs = cells.get((fam, role, era))
                if not obs:
                    continue
                deltas = [o[0] for o in obs if o[0] is not None]
                pays = [o[1] for o in obs if o[1] is not None]
                print(f"| {fam} | {role} | {era} | {len(obs)} | "
                      f"{sum(o[2] for o in obs)/len(obs):.2f} | "
                      f"{sum(deltas)/len(deltas):+.2f} | {sum(pays)/len(pays):,.0f} |")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "analysis/history.jsonl")
