"""Equilibrist — GLEE competition agent.

Plays all three families with game-theoretic rule-based strategies:
equilibrium bargaining with realism clamps, concession-curve negotiation,
and reputation-aware persuasion. Pure Python: every move returns in
microseconds, so turn timeouts can only come from the network.

Usage:
    export GLEE_API_KEY=glee_...
    python agent.py
"""

import logging
import os
import re
import sys
import time
from pathlib import Path

from glee_sdk import CompetitionClosedError, CompetitionNotOpenError, GleeClient

try:
    import pool as pool_targets
except Exception:  # missing/corrupt quantile file must never take the agent down
    pool_targets = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(message)s",
    stream=sys.stdout,  # hosted runtimes read the container's stdout
)
log = logging.getLogger("equilibrist")

# ---------------------------------------------------------------------------
# helpers

def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _fields(game):
    f = game.get("valid_actions", {}).get("fields")
    return f if isinstance(f, dict) else None


def _finalize(action, game):
    """Drop keys the server didn't ask for and cap message length."""
    f = _fields(game)
    if f:
        action = {k: v for k, v in action.items() if k in f}
    elif game.get("game_state", {}).get("messages_allowed") is False:
        action.pop("message", None)
    msg = action.get("message")
    if isinstance(msg, str) and len(msg) > 1900:
        action["message"] = msg[:1900]
    return action


def _money_pair(mine, money):
    """Split `money` giving me `mine`, with the pair summing exactly."""
    if float(money).is_integer():
        mine = int(round(mine))
        mine = int(_clamp(mine, 0, money))
        return mine, int(money) - mine
    mine = round(_clamp(mine, 0.0, money), 2)
    return mine, round(money - mine, 2)


# Moves are deterministic, so an invalid move would repeat and burn all five
# attempts. If the same decision point comes back, escalate to a safer move.
_seen = {}

def _attempt_no(game):
    s = game.get("game_state", {})
    key = (
        game.get("game_id"),
        game.get("phase"),
        s.get("round"),
        len(s.get("history") or []),
    )
    _seen[key] = _seen.get(key, 0) + 1
    if len(_seen) > 5000:
        _seen.clear()
    return _seen[key]


# ---------------------------------------------------------------------------
# Bargaining

REJECT_FLOOR = 0.12  # realistic minimum share a final-round responder swallows

def _bg_deltas(game):
    s = game["game_state"]
    me = game["your_player"]
    d_me = s.get("delta_1" if me == "player_1" else "delta_2")
    d_opp = s.get("delta_2" if me == "player_1" else "delta_1")
    d_me = float(d_me) if d_me is not None else 0.9
    d_opp = float(d_opp) if d_opp is not None else d_me
    return d_me, d_opp


def _rubinstein(d_prop, d_resp):
    denom = 1.0 - d_prop * d_resp
    if denom < 1e-9:
        return 0.5
    return _clamp((1.0 - d_resp) / denom, 0.1, 0.9)


def _proposer_share(round_k, cur_round, i_propose_now, max_rounds, d_me, d_opp):
    """Nominal pot fraction the round-k proposer can secure.

    Backward induction under a known horizon; Rubinstein otherwise. Parity of
    (round_k - cur_round) says whether the round-k proposer is me.
    """
    k_is_me = i_propose_now == ((round_k - cur_round) % 2 == 0)
    d_prop, d_resp = (d_me, d_opp) if k_is_me else (d_opp, d_me)
    if max_rounds is None or max_rounds - round_k > 60:
        return _rubinstein(d_prop, d_resp)
    if round_k >= max_rounds:
        return 1.0 - REJECT_FLOOR
    nxt = _proposer_share(round_k + 1, cur_round, i_propose_now, max_rounds, d_me, d_opp)
    return _clamp(1.0 - d_resp * nxt, 0.1, 0.9)


def _bg_pool_targets(game):
    if pool_targets is None:
        return None
    try:
        return pool_targets.bargaining_targets(game)
    except Exception:
        return None


def bargaining_strategy(game):
    s = game["game_state"]
    va = game["valid_actions"]
    me = game["your_player"]
    money = float(s["money_to_divide"])
    r = int(s.get("round") or 1)
    max_rounds = s.get("max_rounds") if s.get("horizon_known") else None
    d_me, d_opp = _bg_deltas(game)
    my_offer_key = "alice_gain" if me == "player_1" else "bob_gain"
    opp_offer_key = "bob_gain" if me == "player_1" else "alice_gain"
    targets = _bg_pool_targets(game)
    disc = d_me ** (r - 1)

    if va["type"] == "offer" and targets:
        # Aim where the reference pool pays: p90 payoff first, conceding one
        # quantile per rejection of my own offers. Payoffs are discounted, so
        # the nominal demand that still HITS the target grows as rounds pass —
        # which is exactly the pressure to settle early.
        rejections = sum(
            1 for h in (s.get("history") or [])
            if h.get("proposer") == me and (h.get("decision") or "") == "reject"
        )
        ladder = [0.90, 0.75, 0.60, 0.50]
        tgt = targets[ladder[min(rejections, 3)]]
        share = (tgt / disc) / money if money else 0.5
        # discounting inflates late-round nominal demands past anything the
        # pool's opponents ever accept — cap near the pool's top settlements
        share = min(share, (targets[0.90] / money) + 0.08 if money else 0.8, 0.80)
        if max_rounds is not None and r == max_rounds:
            share = 1.0 - REJECT_FLOOR - 0.03
        share = _clamp(share, 0.45, 0.85)
        mine, theirs = _money_pair(share * money, money)
        pct = round(100 * mine / money)
        return _finalize({
            my_offer_key: mine,
            opp_offer_key: theirs,
            "message": (
                f"Proposing {pct}/{100 - pct}. Every round of delay shrinks the pie "
                "for both of us - locking this in now beats a smaller split later."
            ),
        }, game)

    if va["type"] == "decision" and targets:
        offer = s.get("last_offer") or {}
        my_now = float(offer.get(f"{me}_gain") or 0.0) * disc
        if max_rounds is not None and r >= max_rounds:
            return {"decision": "accept"}
        if r <= 2:
            lvl = 0.75
        elif r <= 4:
            lvl = 0.60
        elif r <= 6:
            lvl = 0.50
        elif r <= 9:
            lvl = 0.40
        elif r <= 14:
            lvl = 0.25
        else:
            lvl = 0.10
        if my_now >= targets[lvl] and my_now > 0:
            return {"decision": "accept"}
        if r > 20 and my_now > 0:
            return {"decision": "accept"}  # deep in the hidden-cap zone
        return {"decision": "reject"}

    if va["type"] == "offer":
        share = _proposer_share(r, r, True, max_rounds, d_me, d_opp)
        # cushion for acceptance probability: larger after rejections, and
        # larger when my inflation is worse (a rejected round costs me more)
        rejections = sum(
            1 for h in (s.get("history") or [])
            if h.get("proposer") == me and (h.get("decision") or "") == "reject"
        )
        share -= 0.03 + 0.04 * min(rejections, 3) + 0.1 * (1.0 - d_me)
        if max_rounds is None and r > 6:
            share = min(share, 0.55)
        if max_rounds is not None and r == max_rounds:
            share = 1.0 - REJECT_FLOOR - 0.03  # ultimatum, but below the spite line
        # when the equilibrium genuinely sits below half, insisting on 50%
        # just feeds the standoff — allow conceding into the weak-side range
        floor = 0.5 if _rubinstein(d_me, d_opp) >= 0.45 else 0.35
        share = _clamp(share, floor, 0.85)
        mine, theirs = _money_pair(share * money, money)
        pct = round(100 * mine / money)
        return {
            my_offer_key: mine,
            opp_offer_key: theirs,
            "message": (
                f"Proposing {pct}/{100 - pct}. Every round of delay shrinks the pie "
                "for both of us - locking this in now beats a smaller split later."
            ),
        }

    # decision phase: I'm the receiver
    offer = s.get("last_offer") or {}
    my_gain = float(offer.get(f"{me}_gain") or 0.0)
    if max_rounds is not None and r >= max_rounds:
        return {"decision": "accept"}  # rejecting the last offer means 0
    cont_share = _proposer_share(r + 1, r + 1, True, max_rounds, d_me, d_opp)
    continuation = d_me * cont_share * money
    threshold = min(0.95 * continuation, 0.62 * money)
    if max_rounds is None and r > 8:
        threshold = min(threshold, 0.40 * money)
    # Standoff breaker: thresholds are nominal but payoffs are discounted, so
    # dragging a game out is strictly worse than a small early deal. Applies
    # only when I don't hold the final-round ultimatum myself.
    opp_has_last_word = max_rounds is None or (max_rounds - r) % 2 == 0
    if opp_has_last_word:
        cap = money * max(0.5 * (0.85 ** max(r - 2, 0)), 0.02 if r < 25 else 0.0)
        threshold = min(threshold, cap)
    if my_gain >= threshold or my_gain >= 0.55 * money:
        return {"decision": "accept"}
    return {"decision": "reject"}


# ---------------------------------------------------------------------------
# Negotiation

_BETWEEN = re.compile(
    r"between\s+\$?\s*([\d,]+(?:\.\d+)?)\s+and\s+\$?\s*([\d,]+(?:\.\d+)?)", re.I
)

def _prompt_bounds(prompt):
    best = None
    for a, b in _BETWEEN.findall(prompt or ""):
        lo = float(a.replace(",", ""))
        hi = float(b.replace(",", ""))
        if hi > lo and (best is None or hi > best[1]):
            best = (lo, hi)
    return best


def _neg_price(x, my_value):
    if float(my_value).is_integer():
        return int(round(x))
    return round(x, 2)


def _neg_pool_targets(game):
    if pool_targets is None:
        return None
    try:
        return pool_targets.negotiation_targets(game)
    except Exception:
        return None


def negotiation_strategy(game):
    s = game["game_state"]
    va = game["valid_actions"]
    me = game["your_player"]
    role = s[f"{me}_role"]
    V = float(s[f"{me}_value"])
    r = int(s.get("round") or 1)
    max_rounds = s.get("max_rounds") if s.get("horizon_known") else None
    bounds = _prompt_bounds(game.get("prompt"))
    targets = _neg_pool_targets(game)

    if targets:
        horizon = max_rounds if max_rounds is not None else 99
        prog = _clamp((r - 1) / max(horizon - 1, 1), 0.0, 1.0)
        if prog < 0.2:
            lvl, offer_lvl = 0.75, 0.90
        elif prog < 0.45:
            lvl, offer_lvl = 0.60, 0.75
        elif prog < 0.7:
            lvl, offer_lvl = 0.50, 0.60
        elif prog < 0.9:
            lvl, offer_lvl = 0.40, 0.50
        else:
            lvl, offer_lvl = 0.25, 0.40

        def aspiration(q):
            # positive-outcome quantiles: what a DEAL pays, undiluted by
            # impossible-trade configs where the whole pool scores 0
            return max(targets.get(("pos", q), targets[q]), 0.0)

        def price_for(profit):
            return V + profit if role == "seller" else V - profit

        if va["type"] == "offer":
            price = price_for(aspiration(offer_lvl))
            if bounds:
                lo, hi = bounds
                if role == "seller":
                    # never open below the uniform-buyer optimum
                    price = _clamp(max(price, (hi + V) / 2.0), lo, hi * 0.98)
                else:
                    price = _clamp(min(price, (lo + V) / 2.0), lo * 1.02, hi)
            return {
                "product_price": _neg_price(price, V),
                "message": (
                    "Serious offer - priced so we both come out ahead. "
                    "I can move a little, but this is the right neighborhood."
                ),
            }

        offer_price = float((s.get("last_offer") or {}).get("price") or 0.0)
        profit_now = (offer_price - V) if role == "seller" else (V - offer_price)
        last_round = max_rounds is not None and r >= max_rounds
        if last_round:
            if profit_now > 0:
                return {"decision": "AcceptOffer"}
            return {"decision": "RejectOffer"}
        thr = aspiration(lvl)
        if bounds:
            surplus = (bounds[1] - V) if role == "seller" else (V - bounds[0])
            thr = max(thr, (1.0 - prog) * 0.25 * max(surplus, 0.0))
        if profit_now > 0 and profit_now >= thr:
            return {"decision": "AcceptOffer"}
        if r > 60 and profit_now > 0:
            return {"decision": "AcceptOffer"}  # hidden-cap insurance
        counter = price_for(aspiration(offer_lvl))
        if role == "seller":
            counter = max(counter, offer_price * 1.02, V * 1.01)
        else:
            counter = max(min(counter, offer_price * 0.98, V * 0.99), 0.0)
        return {
            "decision": "RejectOffer",
            "product_price": _neg_price(counter, V),
            "message": "Close, but not there yet - here's a number that works for me.",
        }

    if role == "seller":
        hi = bounds[1] if bounds else V * 2.5
        anchor = max(hi * 0.95, V * 1.3)
        reservation = V * 1.02 if V > 0 else V + 0.02 * max(hi - V, 1.0)
    else:
        lo = bounds[0] if bounds else V * 0.3
        anchor = min(lo * 1.05, V * 0.55)
        reservation = V * 0.98

    horizon = max_rounds if max_rounds is not None else 10

    if max_rounds is None:
        # No deadline pressure: hold a profit margin instead of conceding to
        # reservation, relaxing it only late as hidden-round-cap insurance.
        margin = V * (1.15 if role == "seller" else 0.85)
        late = _clamp((r - 30) / 30.0, 0.0, 1.0)
        reservation = margin + (reservation - margin) * late

    def target(round_k):
        t = _clamp((round_k - 1) / max(horizon - 1, 1), 0.0, 1.0)
        return reservation + (anchor - reservation) * (1.0 - t) ** 2

    def profitable(price):
        return (price - V) if role == "seller" else (V - price)

    if va["type"] == "offer":
        price = target(r)
        if max_rounds == 1 and role == "seller" and bounds and bounds[1] > V:
            # take-it-or-leave-it vs a uniform[lo,hi] buyer value: (hi+V)/2
            price = _clamp((bounds[1] + V) / 2.0, bounds[0], bounds[1] * 0.98)
        return {
            "product_price": _neg_price(price, V),
            "message": (
                "Serious offer - priced so we both come out ahead. "
                "I can move a little, but this is the right neighborhood."
            ),
        }

    # decision phase
    offer_price = float((s.get("last_offer") or {}).get("price") or 0.0)
    last_round = max_rounds is not None and r >= max_rounds
    if last_round:
        if profitable(offer_price) > 0:
            return {"decision": "AcceptOffer"}
        return {"decision": "RejectOffer"}  # no counter exists on the final round

    accept_bar = target(r + 1)
    good_enough = (
        offer_price >= accept_bar if role == "seller" else offer_price <= accept_bar
    )
    if profitable(offer_price) > 0 and good_enough:
        return {"decision": "AcceptOffer"}
    if max_rounds is None and r > 60 and profitable(offer_price) > 0.02 * max(V, 1.0):
        return {"decision": "AcceptOffer"}  # hidden cap looms; take any real profit

    counter = target(r)
    # never counter on the wrong side of their standing offer
    if role == "seller":
        counter = max(counter, offer_price * 1.02, reservation)
    else:
        counter = min(counter, offer_price * 0.98, reservation)
        counter = max(counter, 0.0)
    return {
        "decision": "RejectOffer",
        "product_price": _neg_price(counter, V),
        "message": "Close, but not there yet - here's a number that works for me.",
    }


# ---------------------------------------------------------------------------
# Persuasion

_NEG_MARKERS = (
    "skip", "pass on", "avoid", "not worth", "isn't worth", "don't buy",
    "do not buy", "not recommend", "wouldn't", "low quality", "low-quality",
    "not good", "not great", "poor", "save your money", "can't recommend",
    "cannot recommend", "not this one", "sit this one out",
)

def _is_positive(msg):
    if msg is None:
        return True
    m = str(msg).strip().lower()
    if m in ("no", "n", "false"):
        return False
    if m in ("yes", "y", "true"):
        return True
    return not any(marker in m for marker in _NEG_MARKERS)


def persuasion_buyer(game):
    s = game["game_state"]
    p = float(s.get("p") or 0.5)
    v = float(s.get("v") or 0.0)
    u = float(s.get("u") or 0.0)
    c = float(s.get("product_price") or 0.0)
    hist = s.get("history") or []

    signal_pos = _is_positive(s.get("seller_message"))
    discriminating = any(not _is_positive(h.get("seller_message")) for h in hist)

    # Quality is drawn iid with KNOWN p, so outcomes are evidence about the
    # seller's signaling policy, never about p. A seller who always recommends
    # sends zero information: the posterior is exactly p, and bad draws must
    # not scare us out of a positive-EV stream.
    if not discriminating:
        p_hat = p
    else:
        highs = lows = 0
        for h in hist:
            if not h.get("bought") or h.get("quality") is None:
                continue
            if _is_positive(h.get("seller_message")) != signal_pos:
                continue
            if h["quality"] == "high":
                highs += 1
            else:
                lows += 1
        # truthful discrimination is the common case; data can overturn it
        prior = min(0.9, p + 0.4 * (1.0 - p)) if signal_pos else p * 0.25
        w = 2.0
        p_hat = (w * prior + highs) / (w + highs + lows)
    ev = p_hat * v + (1.0 - p_hat) * u
    return {"decision": "yes" if ev > c else "no"}


def persuasion_seller(game):
    s = game["game_state"]
    va = game["valid_actions"]
    p = float(s.get("p") or 0.5)
    c = float(s.get("product_price") or 0.0)
    v = s.get("v")
    u = float(s.get("u") or 0.0)
    r = int(s.get("round") or 1)
    total = int(s.get("total_rounds") or 20)
    quality = s.get("current_quality")

    hist = s.get("history") or []
    # A buyer who bought after a negative signal ignores messages entirely —
    # honest discrimination against them just forfeits the low-quality sales.
    buys_regardless = any(
        h.get("bought") and not _is_positive(h.get("seller_message")) for h in hist
    )

    if v is not None:
        naive_buy = p * float(v) + (1.0 - p) * u > c
    else:
        naive_buy = p >= 0.75
    # A lie risks the expected remaining high-quality sales (rounds_left * p),
    # so the defection window widens as p falls.
    rounds_left = max(total - r, 0)
    endgame = rounds_left * p < 1.5

    recommend = naive_buy or buys_regardless or quality == "high" or endgame

    if va["type"] == "seller_recommendation":
        return {"decision": "yes" if recommend else "no"}

    # Verifiable track record: the buyer can check these numbers against the
    # quality of the rounds they actually bought.
    bought_on_pos = [
        h for h in hist
        if h.get("bought") and _is_positive(h.get("seller_message"))
    ]
    good = sum(1 for h in bought_on_pos if h.get("quality") == "high")
    flagged = sum(1 for h in hist if not _is_positive(h.get("seller_message")))
    if bought_on_pos:
        record = (
            f"Check my record: of the {len(bought_on_pos)} you bought on my word, "
            f"{good} were high quality"
            + (f", and I flagged {flagged} weak ones for you to skip" if flagged else "")
            + ". "
        )
    elif flagged:
        record = (
            f"I've already flagged {flagged} weak product(s) instead of pushing "
            "them on you - I only vouch for what's real. "
        )
    else:
        record = ""

    if recommend:
        msg = (
            f"Round {r}: I vouch for this one - it's a genuinely strong unit. "
            + record
            + "When I say buy, I mean it. Take it."
        )
    else:
        msg = (
            "Honestly? Skip this one. "
            + record
            + "I flag the weak units so that when I tell you a product is good, "
            "you know it's true."
        )
    return {"message": msg}


def persuasion_strategy(game):
    t = game["valid_actions"]["type"]
    if t == "buyer_decision":
        return persuasion_buyer(game)
    return persuasion_seller(game)


# ---------------------------------------------------------------------------
# dispatcher + safety net

STRATEGIES = {
    "bargaining": bargaining_strategy,
    "negotiation": negotiation_strategy,
    "persuasion": persuasion_strategy,
}


def _safe_fallback(game):
    """Simplest move that should always validate."""
    va = game.get("valid_actions", {})
    s = game.get("game_state", {})
    t = va.get("type")
    family = game.get("game_family")
    if t == "offer" and family == "bargaining":
        money = float(s.get("money_to_divide") or 0)
        a, b = _money_pair(money / 2, money)
        return {"alice_gain": a, "bob_gain": b}
    if t == "offer":
        me = game.get("your_player", "player_1")
        v = float(s.get(f"{me}_value") or 100)
        role = s.get(f"{me}_role", "seller")
        return {"product_price": _neg_price(v * (1.2 if role == "seller" else 0.9), v)}
    if t == "decision" and family == "negotiation":
        me = game.get("your_player", "player_1")
        v = float(s.get(f"{me}_value") or 0)
        price = float((s.get("last_offer") or {}).get("price") or 0)
        ok = price >= v if s.get(f"{me}_role") == "seller" else price <= v
        return {"decision": "AcceptOffer" if ok else "RejectOffer"}
    if t == "decision":
        return {"decision": "accept"}
    if t == "seller_recommendation":
        return {"decision": "yes"}
    if t == "buyer_decision":
        p = float(s.get("p") or 0.5)
        v = float(s.get("v") or 0.0)
        u = float(s.get("u") or 0.0)
        c = float(s.get("product_price") or 0.0)
        return {"decision": "yes" if p * v + (1 - p) * u > c else "no"}
    f = _fields(game)
    if f and "decision" in f:
        return {"decision": "accept"}
    return {}


def strategy(game):
    try:
        attempt = _attempt_no(game)
        if attempt >= 2:
            log.warning(
                "repeat decision point (attempt %s) in %s — using fallback",
                attempt, game.get("game_id"),
            )
            return _finalize(_safe_fallback(game), game)
        action = STRATEGIES[game["game_family"]](game)
        return _finalize(action, game)
    except Exception:
        log.exception("strategy error in game %s — using fallback", game.get("game_id"))
        try:
            return _finalize(_safe_fallback(game), game)
        except Exception:
            return {"decision": "accept"}


if __name__ == "__main__":
    api_key = os.environ.get("GLEE_API_KEY", "")
    if not api_key:
        raise SystemExit("Set GLEE_API_KEY")
    client = GleeClient(api_key=api_key, timeout=60)
    log.info("stats: %s", client.stats())
    # On xhostd, a worker with no HTTP surface signals deploy readiness by
    # creating this file — only after setup (key validated) has succeeded.
    ready = os.environ.get("XHOST_READY_FILE")
    if ready:
        Path(ready).touch()
    # Supervisor: a transient network error must not kill the run loop —
    # pending games would time out server-side and score at the 5th percentile.
    backoff = 5
    while True:
        started = time.monotonic()
        try:
            # One pending-games poll costs one request regardless of how many
            # games are in flight, and the 60 req/min budget is the real
            # throughput cap — so poll rarely, play many games at once.
            client.run(strategy, concurrency=12, poll_interval=3.0)
            break  # run() returned on its own (e.g. drained after close)
        except CompetitionClosedError:
            log.info("competition closed — stopping")
            break
        except CompetitionNotOpenError as e:
            log.info("competition not open (opens %s); retrying in 10m",
                     e.competition_open_at)
            time.sleep(600)
        except KeyboardInterrupt:
            break
        except Exception:
            if time.monotonic() - started > 60:
                backoff = 5  # it ran fine for a while; this is a fresh failure
            log.exception("run loop crashed — restarting in %ss", backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 120)
