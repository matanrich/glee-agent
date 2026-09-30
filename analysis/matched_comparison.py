"""Configuration-matched comparison of policy eras against the GLEE pool.

Usage: python analysis/matched_comparison.py [--pool analysis/pool.pkl]
                                              [--games games] [--out analysis/out]

Each cached transcript is assigned to a code era (see ERAS). For every
(family, role, visible-configuration) cell present in two eras, we report game
counts, agreement rate, mean payoff as a fraction of the stakes, and the mean
payoff percentile against the dataset pool, with bootstrap 95% intervals.
Aggregates are matched: a cell contributes to an era pair only when both eras
played it, weighted by the smaller of the two counts, so a shift in the
configuration mix between eras cannot masquerade as a policy effect.
"""

import argparse
import bisect
import json
import os
import pickle
import random
import time
from collections import defaultdict

# Eras are identified by explicit id lists when available (final_ids.txt), and
# otherwise by when the transcript was fetched: each fetch pulled the games just
# played, so fetch time brackets the code version that played them. Boundaries
# are local time (UTC+3) on this machine.
ERAS = [
    ("equilibrium", None, "2026-08-26 10:00"),
    ("quantile v1", "2026-08-26 10:00", "2026-08-27 04:00"),
    ("quantile + continuation", "2026-08-27 04:00", "2026-08-27 12:00"),
]
FINAL_ERA = "final (+ empirical floors)"
QS = [0.10, 0.25, 0.50, 0.75, 0.90]


def load_pool(path):
    with open(path, "rb") as f:
        pool = pickle.load(f)
    configs = defaultdict(list)
    for (fam, key, role) in pool:
        if role == "p1":
            configs[fam].append((json.loads(key), key))
    return pool, configs


def visible_config(g):
    """The configuration fields our (information-filtered) view exposes."""
    s = g["game_state"]
    fam = g["game_family"]
    me = g["your_player"]
    role = "p1" if me == "player_1" else "p2"
    known = s.get("horizon_known")
    max_rounds = s.get("max_rounds") if known else 99
    vis = {"complete_information": s.get("complete_information"),
           "messages_allowed": s.get("messages_allowed")}
    if max_rounds is not None:
        vis["max_rounds"] = max_rounds
    if fam == "bargaining":
        vis["money_to_divide"] = s.get("money_to_divide")
        for k in ("delta_1", "delta_2"):
            if s.get(k) is not None:
                vis[k] = s[k]
        stakes = float(s.get("money_to_divide") or 0)
    elif fam == "negotiation":
        vis["_sv"] = s.get("player_1_value")
        vis["_bv"] = s.get("player_2_value")
        stakes = float(s.get(f"{me}_value") or 0)
    else:
        vis = {"p": s.get("p"), "product_price": s.get("product_price"),
               "total_rounds": s.get("total_rounds"),
               "seller_message_type": s.get("seller_message_type")}
        if s.get("v") is not None and s.get("product_price"):
            vis["v"] = s["v"] / s["product_price"]
        stakes = float(s.get("product_price") or 0) * float(s.get("total_rounds") or 1)
    vis = {k: v for k, v in vis.items() if v is not None}
    return fam, role, vis, stakes


def matches(fam, vis, cfg):
    if fam == "negotiation":
        order = cfg["product_price_order"]
        for field, key in (("_sv", "seller_value"), ("_bv", "buyer_value")):
            if field in vis and abs(cfg[key] * order - vis[field]) > 1e-6:
                return False
    for k, v in vis.items():
        if k.startswith("_"):
            continue
        if k in cfg and cfg[k] != v:
            return False
    return True


def percentile(pool, fam, keys, role, payoff):
    total = below = 0
    for key in keys:
        arr = pool.get((fam, key, role))
        if not arr:
            continue
        lo, hi = bisect.bisect_left(arr, payoff), bisect.bisect_right(arr, payoff)
        below += lo + 0.5 * (hi - lo)
        total += len(arr)
    return below / total if total else None


def era_of(path, gid, final_ids):
    if gid in final_ids:
        return FINAL_ERA
    t = time.localtime(os.path.getmtime(path))
    stamp = time.strftime("%Y-%m-%d %H:%M", t)
    for name, start, end in ERAS:
        if (start is None or stamp >= start) and (end is None or stamp < end):
            return name
    return None


def boot_ci(values, n=2000, seed=0):
    if len(values) < 2:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    means = sorted(sum(rng.choices(values, k=len(values))) / len(values) for _ in range(n))
    return means[int(0.025 * n)], means[int(0.975 * n)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default="analysis/pool.pkl")
    ap.add_argument("--games", default="games")
    ap.add_argument("--final-ids", default="analysis/final_ids.txt")
    ap.add_argument("--out", default="analysis/out")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    pool, configs = load_pool(args.pool)
    final_ids = set()
    if os.path.exists(args.final_ids):
        with open(args.final_ids) as f:
            final_ids = {ln.strip() for ln in f if len(ln.strip()) == 36}

    # cells[(fam, role, cfg_label)][era] -> list of (pct, payoff_frac, agreed)
    cells = defaultdict(lambda: defaultdict(list))
    match_cache = {}
    for fn in os.listdir(args.games):
        path = os.path.join(args.games, fn)
        with open(path) as f:
            g = json.load(f)
        if g.get("status") == "active" or not g.get("result"):
            continue
        era = era_of(path, g["game_id"], final_ids)
        if era is None:
            continue
        fam, role, vis, stakes = visible_config(g)
        me = g["your_player"]
        payoff = g["result"].get(f"{me}_payoff")
        if payoff is None:
            continue
        label = json.dumps(vis, sort_keys=True)
        if (fam, label) not in match_cache:
            match_cache[(fam, label)] = [k for c, k in configs[fam] if matches(fam, vis, c)]
        pct = percentile(pool, fam, match_cache[(fam, label)], role, payoff)
        if pct is None:
            continue
        agreed = g["result"].get("outcome") != "no_deal"
        frac = payoff / stakes if stakes else float("nan")
        cells[(fam, role, label)][era].append((pct, frac, agreed))

    eras = [e[0] for e in ERAS] + [FINAL_ERA]
    rows = []
    for (fam, role, label), by_era in cells.items():
        for era, obs in by_era.items():
            pcts = [o[0] for o in obs]
            lo, hi = boot_ci(pcts)
            rows.append({
                "family": fam, "role": role, "config": label, "era": era,
                "n": len(obs), "agreement_rate": sum(o[2] for o in obs) / len(obs),
                "mean_payoff_frac": sum(o[1] for o in obs) / len(obs),
                "mean_pct": sum(pcts) / len(pcts), "pct_ci_lo": lo, "pct_ci_hi": hi,
            })
    with open(os.path.join(args.out, "cells.json"), "w") as f:
        json.dump(rows, f, indent=1)

    # matched aggregate: baseline era vs each later era, per (family, role)
    base = eras[0]
    lines = ["| family | role | era | matched cells | games (base/era) | agree (base/era) | payoff frac (base/era) | pct base | pct era | Δpct [95% CI] |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    summary = []
    for fam in ("bargaining", "negotiation", "persuasion"):
        for role in ("p1", "p2"):
            for era in eras[1:]:
                b_all, e_all, w_sum, k = [], [], 0, 0
                nb = ne = 0
                for (f, r, label), by_era in cells.items():
                    if f != fam or r != role or base not in by_era or era not in by_era:
                        continue
                    b, e = by_era[base], by_era[era]
                    w = min(len(b), len(e))
                    k += 1
                    w_sum += w
                    nb += len(b)
                    ne += len(e)
                    b_all.append((w, b))
                    e_all.append((w, e))
                if not k:
                    continue

                def wmean(groups, idx):
                    return sum(w * sum(o[idx] for o in obs) / len(obs) for w, obs in groups) / w_sum

                def boot_delta(n=2000, seed=1):
                    rng = random.Random(seed)
                    out = []
                    for _ in range(n):
                        d = 0.0
                        for (w, b), (_, e) in zip(b_all, e_all):
                            mb = sum(rng.choice(b)[0] for _ in range(len(b))) / len(b)
                            me_ = sum(rng.choice(e)[0] for _ in range(len(e))) / len(e)
                            d += w * (me_ - mb)
                        out.append(d / w_sum)
                    out.sort()
                    return out[int(0.025 * n)], out[int(0.975 * n)]

                pb, pe = wmean(b_all, 0), wmean(e_all, 0)
                lo, hi = boot_delta()
                summary.append({"family": fam, "role": role, "era": era, "cells": k,
                                "n_base": nb, "n_era": ne, "pct_base": pb, "pct_era": pe,
                                "delta": pe - pb, "ci": [lo, hi]})
                lines.append(
                    f"| {fam} | {role} | {era} | {k} | {nb}/{ne} | "
                    f"{wmean(b_all, 2):.2f}/{wmean(e_all, 2):.2f} | "
                    f"{wmean(b_all, 1):.2f}/{wmean(e_all, 1):.2f} | "
                    f"{pb:.3f} | {pe:.3f} | {pe - pb:+.3f} [{lo:+.3f}, {hi:+.3f}] |")
    with open(os.path.join(args.out, "matched_summary.json"), "w") as f:
        json.dump(summary, f, indent=1)
    md = "\n".join(lines)
    with open(os.path.join(args.out, "matched_summary.md"), "w") as f:
        f.write(md + "\n")
    print(md)
    present = sorted({e for c in cells.values() for e in c})
    print("\neras present:", present)


if __name__ == "__main__":
    main()
