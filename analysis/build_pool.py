"""Build per-configuration payoff pools from the public GLEE dataset.

Usage: python analysis/build_pool.py <path to GLEE repo clone> [out.pkl]

Walks Data/llm_vs_llm/{bargaining,negotiation,persuasion}, recomputes both
players' payoffs from each game log under the family's rules, and writes
{(family, config_json, role): sorted payoffs} — the reference distribution the
competition's percentile scoring is seeded from.
"""

import csv
import json
import os
import pickle
import sys


def bargaining_payoffs(rows, ga):
    d1, d2 = ga.get("delta_1", 1.0), ga.get("delta_2", 1.0)
    last = None
    for r in rows:
        if r["player"] in ("Alice", "Bob") and r.get("alice_gain"):
            last = r
        dec = (r.get("decision") or "").lower()
        if dec == "accept" and last is not None:
            rnd = int(float(r["round"]))
            return (float(last["alice_gain"]) * d1 ** (rnd - 1),
                    float(last["bob_gain"]) * d2 ** (rnd - 1))
        if dec == "walkaway":
            return 0.0, 0.0
    return 0.0, 0.0


def negotiation_payoffs(rows, ga):
    order = ga["product_price_order"]
    sv, bv = ga["seller_value"] * order, ga["buyer_value"] * order
    last_price = None
    for r in rows:
        if r.get("product_price"):
            last_price = float(r["product_price"])
        dec = (r.get("decision") or "").lower()
        if dec == "acceptoffer" and last_price is not None:
            return last_price - sv, bv - last_price
        if dec == "walkaway":
            return 0.0, 0.0
    return 0.0, 0.0


def persuasion_payoffs(rows, ga):
    price = ga["product_price"]
    seller = buyer = 0.0
    worth = None
    for r in rows:
        if r["player"] == "Nature":
            worth = float(r["product_worth"])
        elif r["player"] == "Bob" and (r.get("decision") or "").lower() == "yes":
            seller += price
            buyer += (worth or 0.0) - price
    return seller, buyer


HANDLERS = {
    "bargaining": bargaining_payoffs,
    "negotiation": negotiation_payoffs,
    "persuasion": persuasion_payoffs,
}


def main(repo, out):
    root = os.path.join(repo, "Data", "llm_vs_llm")
    pool = {}
    n = bad = 0
    for family, handler in HANDLERS.items():
        for dirpath, _, filenames in os.walk(os.path.join(root, family)):
            if "config.json" not in filenames or "game.csv" not in filenames:
                continue
            try:
                with open(os.path.join(dirpath, "config.json")) as f:
                    ga = json.load(f)["game_args"]
                with open(os.path.join(dirpath, "game.csv"), newline="") as f:
                    rows = list(csv.DictReader(f))
                p1, p2 = handler(rows, ga)
            except Exception:
                bad += 1
                continue
            key = json.dumps(ga, sort_keys=True)
            pool.setdefault((family, key, "p1"), []).append(p1)
            pool.setdefault((family, key, "p2"), []).append(p2)
            n += 1
    for v in pool.values():
        v.sort()
    with open(out, "wb") as f:
        pickle.dump(pool, f)
    configs = {fam: len({k for (f, k, _) in pool if f == fam}) for fam in HANDLERS}
    print(f"{n} games, {bad} unreadable, configs per family: {configs} -> {out}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "analysis/pool.pkl")
