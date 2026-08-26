"""Payoff-quantile targets from the GLEE research dataset.

The competition scores each game as a payoff percentile within its exact
configuration and role, seeded by the public GLEE dataset. This module answers:
"in this game, what payoff lands at pool percentile q?" — matched on every
config field our filtered view exposes, aggregated (weighted by pool size)
over the hidden ones.
"""

import json
import os

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pool_quantiles.json")
_DATA = None


def _load():
    global _DATA
    if _DATA is None:
        with open(_PATH) as f:
            raw = json.load(f)
        _DATA = (raw["quantiles"], raw["pools"])
    return _DATA


def _match(family, role, predicate):
    """Aggregate matching pools. Returns {q: payoff} plus a positive-outcome
    variant under key ("pos", q) — the aspiration when a deal is on the table,
    undiluted by configs where trade is impossible and everyone scores 0."""
    qs, pools = _load()
    acc = None
    acc_pos = None
    total = total_pos = 0
    for p in pools:
        if p["f"] != family or p["r"] != role:
            continue
        if not predicate(p["c"]):
            continue
        if acc is None:
            acc = [0.0] * len(p["q"])
            acc_pos = [0.0] * len(p["q"])
        for i, v in enumerate(p["q"]):
            acc[i] += v * p["n"]
        total += p["n"]
        if p.get("qp"):
            for i, v in enumerate(p["qp"]):
                acc_pos[i] += v * p["np"]
            total_pos += p["np"]
    if not total:
        return None
    out = {q: acc[i] / total for i, q in enumerate(qs)}
    if total_pos:
        for i, q in enumerate(qs):
            out[("pos", q)] = acc_pos[i] / total_pos
    return out


def bargaining_targets(game):
    s = game["game_state"]
    role = "p1" if game["your_player"] == "player_1" else "p2"
    money = s.get("money_to_divide")
    d1, d2 = s.get("delta_1"), s.get("delta_2")
    known = s.get("horizon_known")
    max_rounds = s.get("max_rounds") if known else 99  # hidden cap in the grid
    ci = s.get("complete_information")
    ma = s.get("messages_allowed")

    def pred(c):
        if c.get("money_to_divide") != money:
            return False
        if max_rounds is not None and c.get("max_rounds") != max_rounds:
            return False
        if ci is not None and c.get("complete_information") != ci:
            return False
        if ma is not None and c.get("messages_allowed") != ma:
            return False
        if d1 is not None and c.get("delta_1") != d1:
            return False
        if d2 is not None and c.get("delta_2") != d2:
            return False
        return True

    return _match("bargaining", role, pred)


def negotiation_targets(game):
    s = game["game_state"]
    me = game["your_player"]
    role = "p1" if me == "player_1" else "p2"
    my_value = s.get(f"{me}_value")
    sv, bv = s.get("player_1_value"), s.get("player_2_value")
    known = s.get("horizon_known")
    max_rounds = s.get("max_rounds") if known else 99
    ci = s.get("complete_information")
    ma = s.get("messages_allowed")

    def val_ok(c, field, absval):
        if absval is None:
            return True
        return abs(c[field] * c["product_price_order"] - absval) < 1e-6

    def pred(c):
        if max_rounds is not None and c.get("max_rounds") != max_rounds:
            return False
        if ci is not None and c.get("complete_information") != ci:
            return False
        if ma is not None and c.get("messages_allowed") != ma:
            return False
        # my own value pins the scale; opponent's only under complete info
        if not val_ok(c, "seller_value", sv):
            return False
        if not val_ok(c, "buyer_value", bv):
            return False
        if my_value is not None:
            f = "seller_value" if role == "p1" else "buyer_value"
            if not val_ok(c, f, my_value):
                return False
        return True

    return _match("negotiation", role, pred)
