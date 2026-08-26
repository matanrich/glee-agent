"""Offline sanity tests for agent.py — no network."""

import agent


def make(family, player, va_type, state, fields=None, prompt="", gid="g1"):
    return {
        "game_id": gid,
        "game_family": family,
        "your_player": player,
        "phase": va_type,
        "opponent": {"type": "hidden", "name": None},
        "game_state": state,
        "valid_actions": {"type": va_type, "fields": fields or {}},
        "prompt": prompt,
    }


def run(game):
    agent._seen.clear()
    return agent.strategy(game)


def check(name, cond, extra=""):
    status = "ok" if cond else "FAIL"
    print(f"[{status}] {name} {extra}")
    return cond


results = []

# ---- bargaining: opening offer, equal deltas, known horizon
g = make("bargaining", "player_1", "offer", {
    "money_to_divide": 1000, "round": 1, "max_rounds": 10, "horizon_known": True,
    "delta_1": 0.9, "delta_2": 0.9, "history": [], "messages_allowed": True,
}, fields={"alice_gain": {}, "bob_gain": {}, "message": {}})
a = run(g)
results.append(check("bg offer sums", a["alice_gain"] + a["bob_gain"] == 1000, str(a)))
results.append(check("bg offer share 50-85%", 500 <= a["alice_gain"] <= 850, str(a["alice_gain"])))

# ---- bargaining: I'm weak (my delta low, theirs 1.0) — still demands >= 50 with clamp
g = make("bargaining", "player_2", "offer", {
    "money_to_divide": 100, "round": 1, "max_rounds": None, "horizon_known": False,
    "delta_2": 0.8, "delta_1": 1.0, "history": [], "messages_allowed": False,
}, fields={"alice_gain": {}, "bob_gain": {}})
g["game_state"].pop("max_rounds")
a = run(g)
results.append(check("bg weak offer valid", a["alice_gain"] + a["bob_gain"] == 100, str(a)))
results.append(check("bg no message when not allowed", "message" not in a, str(a)))

# ---- bargaining: last-round responder accepts anything
g = make("bargaining", "player_1", "decision", {
    "money_to_divide": 1000, "round": 10, "max_rounds": 10, "horizon_known": True,
    "delta_1": 0.9, "delta_2": 0.9, "history": [],
    "last_offer": {"player_1_gain": 50, "player_2_gain": 950, "proposer": "player_2", "round": 10},
    "current_player": "player_1",
}, fields={"decision": {}})
a = run(g)
results.append(check("bg last-round accept", a == {"decision": "accept"}, str(a)))

# ---- bargaining: early lowball gets rejected
g["game_state"].update(round=2)
a = run(g)
results.append(check("bg early lowball reject", a == {"decision": "reject"}, str(a)))

# ---- bargaining: good offer accepted early
g["game_state"]["last_offer"] = {"player_1_gain": 560, "player_2_gain": 440,
                                 "proposer": "player_2", "round": 2}
a = run(g)
results.append(check("bg 56% accept", a == {"decision": "accept"}, str(a)))

# ---- bargaining: weak side (opponent inflation-immune) proposes below half
g = make("bargaining", "player_1", "offer", {
    "money_to_divide": 10000, "round": 3, "horizon_known": False,
    "delta_1": 0.8, "delta_2": 1.0, "history": [], "messages_allowed": False,
}, fields={"alice_gain": {}, "bob_gain": {}})
a = run(g)
results.append(check("bg weak side anchors in pool range",
                     4500 <= a["alice_gain"] <= 8500 and a["alice_gain"] + a["bob_gain"] == 10000, str(a)))

# ---- bargaining: standoff breaker accepts a small offer deep in a lossy game
g = make("bargaining", "player_1", "decision", {
    "money_to_divide": 10000, "round": 12, "horizon_known": False,
    "delta_1": 0.95, "delta_2": 1.0, "current_player": "player_1", "history": [],
    "last_offer": {"player_1_gain": 1200, "player_2_gain": 8800,
                   "proposer": "player_2", "round": 22},
}, fields={"decision": {}})
g["game_state"]["round"] = 22
a = run(g)
results.append(check("bg deep-round small offer accepted", a == {"decision": "accept"}, str(a)))

# ---- negotiation: seller opening offer with prompt bounds
g = make("negotiation", "player_1", "offer", {
    "player_1_role": "seller", "player_2_role": "buyer",
    "player_1_value": 80, "round": 1, "max_rounds": 10, "horizon_known": True,
    "current_player": "player_1", "history": [], "messages_allowed": True,
}, fields={"product_price": {}, "message": {}},
   prompt="The product is worth between $100 and $200 to the buyer.")
a = run(g)
results.append(check("neg seller anchors sanely", 130 <= a["product_price"] <= 200, str(a)))

# ---- negotiation: seller TIOLI with bounds -> (hi+V)/2
g["game_state"]["max_rounds"] = 1
a = run(g)
results.append(check("neg seller TIOLI ~140", 130 <= a["product_price"] <= 150, str(a)))

# ---- negotiation: buyer accepts profitable good deal
g = make("negotiation", "player_2", "decision", {
    "player_1_role": "seller", "player_2_role": "buyer",
    "player_2_value": 150, "round": 9, "max_rounds": 10, "horizon_known": True,
    "current_player": "player_2",
    "last_offer": {"price": 100, "from_player": "player_1", "round": 9},
    "history": [],
}, fields={"decision": {}, "product_price": {}, "message": {}})
a = run(g)
results.append(check("neg buyer accepts near-horizon profit", a["decision"] == "AcceptOffer", str(a)))

# ---- negotiation: buyer never accepts a loss; final round plain reject
g["game_state"]["last_offer"]["price"] = 180
g["game_state"]["round"] = 10
a = run(g)
results.append(check("neg buyer final-round loss -> plain reject",
                     a["decision"] == "RejectOffer" and "product_price" not in a, str(a)))

# ---- negotiation: buyer counter stays below their offer and own value
g["game_state"]["round"] = 2
g["game_state"]["last_offer"]["price"] = 140
a = run(g)
results.append(check("neg buyer counter sane",
                     a["decision"] == "RejectOffer" and 0 < a["product_price"] < 140, str(a)))

# ---- negotiation: seller rejects sub-value price mid-game with higher counter
g = make("negotiation", "player_1", "decision", {
    "player_1_role": "seller", "player_2_role": "buyer",
    "player_1_value": 100, "round": 3, "max_rounds": 10, "horizon_known": True,
    "current_player": "player_1",
    "last_offer": {"price": 60, "from_player": "player_2", "round": 3},
    "history": [],
}, fields={"decision": {}, "product_price": {}, "message": {}})
a = run(g)
results.append(check("neg seller counters above value",
                     a["decision"] == "RejectOffer" and a["product_price"] > 100, str(a)))

# ---- negotiation: unknown horizon holds a margin mid-game
g = make("negotiation", "player_2", "decision", {
    "player_1_role": "seller", "player_2_role": "buyer",
    "player_2_value": 100, "round": 15, "horizon_known": False,
    "current_player": "player_2",
    "last_offer": {"price": 97, "from_player": "player_1", "round": 15},
    "history": [],
}, fields={"decision": {}, "product_price": {}, "message": {}})
a = run(g)
results.append(check("neg unknown-horizon rejects 3% profit mid-game",
                     a["decision"] == "RejectOffer" and a["product_price"] <= 85, str(a)))

# ---- negotiation: unknown horizon takes real profit very late
g["game_state"]["round"] = 70
g["game_state"]["last_offer"]["price"] = 95
a = run(g)
results.append(check("neg unknown-horizon accepts late", a["decision"] == "AcceptOffer", str(a)))

# ---- persuasion: buyer with good prior buys
g = make("persuasion", "player_2", "buyer_decision", {
    "p": 0.8, "v": 120, "u": 0, "product_price": 80, "round": 1, "total_rounds": 20,
    "seller_message": "Great product!", "history": [],
}, fields={"decision": {}})
a = run(g)
results.append(check("pers buyer buys on good prior", a == {"decision": "yes"}, str(a)))

# ---- persuasion: known iid p — bad draws from an always-yes seller don't stop us
lows = [{"round": i, "seller_message": "Amazing!", "buyer_decision": "yes",
         "bought": True, "quality": "low"} for i in range(1, 7)]
g["game_state"]["history"] = lows
g["game_state"]["round"] = 7
a = run(g)
results.append(check("pers buyer keeps buying on positive prior EV", a == {"decision": "yes"}, str(a)))

# ---- persuasion: a discriminating seller whose 'yes' keeps coming up low gets cut off
disc = [{"round": 1, "seller_message": "Skip this one.", "buyer_decision": "no", "bought": False}] + [
    {"round": i, "seller_message": "Amazing!", "buyer_decision": "yes",
     "bought": True, "quality": "low"} for i in range(2, 7)]
g["game_state"]["history"] = disc
a = run(g)
results.append(check("pers buyer cuts off lying discriminator", a == {"decision": "no"}, str(a)))

# ---- persuasion: negative prior EV and no signal -> pass
g2 = make("persuasion", "player_2", "buyer_decision", {
    "p": 1 / 3, "v": 120, "u": 0, "product_price": 80, "round": 1, "total_rounds": 20,
    "seller_message": "Great product!", "history": [],
}, fields={"decision": {}})
a = run(g2)
results.append(check("pers buyer passes on negative prior EV", a == {"decision": "no"}, str(a)))

# ---- persuasion: discriminating honest seller earns trust on marginal prior
hon = [
    {"round": 1, "seller_message": "Skip this one.", "buyer_decision": "no", "bought": False},
    {"round": 2, "seller_message": "This one is great.", "buyer_decision": "yes", "bought": True, "quality": "high"},
    {"round": 3, "seller_message": "This one is great.", "buyer_decision": "yes", "bought": True, "quality": "high"},
]
g = make("persuasion", "player_2", "buyer_decision", {
    "p": 0.5, "v": 120, "u": 0, "product_price": 70, "round": 4, "total_rounds": 20,
    "seller_message": "This one is great.", "history": hon,
}, fields={"decision": {}})
a = run(g)
results.append(check("pers buyer trusts honest discriminator", a == {"decision": "yes"}, str(a)))

# ---- persuasion: seller honest on low quality mid-game when prior is bad
g = make("persuasion", "player_1", "seller_recommendation", {
    "p": 0.3, "v": 100, "u": 0, "product_price": 60, "round": 5, "total_rounds": 20,
    "current_quality": "low", "history": [],
}, fields={"decision": {}})
a = run(g)
results.append(check("pers seller honest no", a == {"decision": "no"}, str(a)))

# ---- persuasion: seller defects on the last round
g["game_state"]["round"] = 20
a = run(g)
results.append(check("pers seller endgame defect", a == {"decision": "yes"}, str(a)))

# ---- persuasion: low p widens the defection window (p=0.3 -> last ~4 rounds)
g["game_state"]["round"] = 17
a = run(g)
results.append(check("pers seller defects early on low p", a == {"decision": "yes"}, str(a)))

# ---- persuasion: buyer who bought on a 'no' ignores messages -> always recommend
g["game_state"]["round"] = 5
g["game_state"]["history"] = [
    {"round": 1, "seller_message": "no", "buyer_decision": "yes", "bought": True, "quality": "low"},
]
a = run(g)
results.append(check("pers seller recs to message-blind buyer", a == {"decision": "yes"}, str(a)))

# ---- persuasion: seller always recommends when prior already sells
g["game_state"].update(round=5, p=0.8, product_price=60, v=100)
a = run(g)
results.append(check("pers seller rec when naive-buy", a == {"decision": "yes"}, str(a)))

# ---- persuasion: text-mode seller message
g = make("persuasion", "player_1", "seller_message", {
    "p": 0.3, "v": 100, "u": 0, "product_price": 60, "round": 2, "total_rounds": 20,
    "current_quality": "high", "history": [],
}, fields={"message": {}})
a = run(g)
results.append(check("pers seller text message", isinstance(a.get("message"), str) and len(a["message"]) < 1900, str(a)[:60]))

# ---- safety: garbage game falls back without raising
a = agent.strategy({"game_id": "x", "game_family": "bargaining",
                    "your_player": "player_1", "phase": "?", "game_state": {},
                    "valid_actions": {"type": "decision", "fields": {"decision": {}}}})
results.append(check("fallback on garbage", a == {"decision": "accept"}, str(a)))

# ---- safety: repeated decision point switches to fallback
agent._seen.clear()
g = make("bargaining", "player_1", "offer", {
    "money_to_divide": 1000, "round": 1, "max_rounds": 10, "horizon_known": True,
    "delta_1": 0.9, "delta_2": 0.9, "history": [],
}, fields={"alice_gain": {}, "bob_gain": {}})
agent.strategy(g)
a = agent.strategy(g)
results.append(check("repeat -> safe 50/50", a == {"alice_gain": 500, "bob_gain": 500}, str(a)))

# ---- fractional money sums exactly
agent._seen.clear()
g["game_state"]["money_to_divide"] = 100.5
a = agent.strategy(g)
results.append(check("fractional sum exact", round(a["alice_gain"] + a["bob_gain"], 2) == 100.5, str(a)))


# ---- bargaining: heavy inflation must not create a bar nothing clears (Alice, hidden d2)
g = make("bargaining", "player_1", "decision", {
    "money_to_divide": 10000, "round": 3, "max_rounds": 12, "horizon_known": True,
    "delta_1": 0.8, "current_player": "player_1", "history": [],
    "last_offer": {"player_1_gain": 4200, "player_2_gain": 5800,
                   "proposer": "player_2", "round": 3},
}, fields={"decision": {}})
a = run(g)
results.append(check("bg inflated-away pie accepts continuation-beating offer",
                     a == {"decision": "accept"}, str(a)))

print()
failed = results.count(False)
print(f"{len(results) - failed}/{len(results)} passed")
raise SystemExit(1 if failed else 0)
