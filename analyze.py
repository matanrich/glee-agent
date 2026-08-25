"""Fetch completed games (cached in games/) and summarize performance."""

import json
import os
import re
import sys
import time

import requests

API = "https://glee-competition.com/api/agent/games/"
KEY = os.environ["GLEE_API_KEY"]
os.makedirs("games", exist_ok=True)

ids = set()
with open("agent.log") as f:
    for line in f:
        m = re.search(r"Game ([0-9a-f-]{36})", line)
        if m:
            ids.add(m.group(1))

for gid in sorted(ids):
    path = f"games/{gid}.json"
    if os.path.exists(path):
        continue
    r = requests.get(API + gid, headers={"Authorization": f"Bearer {KEY}"}, timeout=30)
    if r.status_code == 429:
        time.sleep(float(r.headers.get("Retry-After", 10)))
        continue
    r.raise_for_status()
    g = r.json()
    if g.get("status") == "active":
        continue  # don't cache unfinished games
    with open(path, "w") as f:
        f.write(r.text)
    time.sleep(2)  # stay far under the rate limit shared with the live agent

games = []
for fn in os.listdir("games"):
    with open(f"games/{fn}") as f:
        g = json.load(f)
    if g.get("status") != "active":
        games.append(g)

print(f"{len(games)} completed games\n")

rows = {}
for g in games:
    me = g["your_player"]
    fam = g["game_family"]
    s = g["game_state"]
    res = g.get("result") or {}
    my_pay = res.get(f"{me}_payoff")
    opp_pay = res.get(f"{'player_2' if me == 'player_1' else 'player_1'}_payoff")
    if fam == "negotiation":
        role = s.get(f"{me}_role", "?")
    elif fam == "persuasion":
        role = "seller" if me == "player_1" else "buyer"
    else:
        role = "alice" if me == "player_1" else "bob"
    rows.setdefault((fam, role), []).append((g, my_pay, opp_pay))

for (fam, role), items in sorted(rows.items()):
    n = len(items)
    deals = sum(1 for g, *_ in items if (g.get("result") or {}).get("outcome") != "no_deal")
    tot_share = []
    for g, mine, opp in items:
        if mine is None or opp is None:
            continue
        tot = (mine or 0) + (opp or 0)
        if tot > 0:
            tot_share.append(mine / tot)
    avg_share = sum(tot_share) / len(tot_share) if tot_share else float("nan")
    print(f"{fam:12s} {role:7s} n={n:3d} deals={deals:3d} my-share-of-surplus={avg_share:.2f}")

print("\n--- persuasion seller detail ---")
for g, mine, opp in rows.get(("persuasion", "seller"), []):
    s = g["game_state"]
    hist = s.get("history") or []
    T = s.get("total_rounds")
    p, v, price = s.get("p"), s.get("v"), s.get("product_price")
    sales = sum(1 for h in hist if h.get("bought"))
    lies_sold = sum(1 for h in hist if h.get("bought") and h.get("quality") == "low")
    highs = sum(1 for h in hist if h.get("quality") == "high")
    print(f"T={T:3} p={p} v={v} u={s.get('u')} price={price} "
          f"highs={highs:3} sales={sales:3} lowsold={lies_sold:2} payoff={mine}")

print("\n--- persuasion buyer detail ---")
for g, mine, opp in rows.get(("persuasion", "buyer"), []):
    s = g["game_state"]
    hist = s.get("history") or []
    T = s.get("total_rounds")
    bought = sum(1 for h in hist if h.get("bought"))
    good_missed = 0  # buyer only sees quality when bought; skip
    print(f"T={T:3} p={s.get('p')} v={s.get('v')} price={s.get('product_price')} "
          f"bought={bought:3} payoff={mine}")

print("\n--- no-deal games ---")
for (fam, role), items in sorted(rows.items()):
    for g, mine, opp in items:
        if (g.get("result") or {}).get("outcome") == "no_deal":
            s = g["game_state"]
            extra = ""
            if fam == "negotiation":
                extra = f"my_value={s.get(f'{g['your_player']}_value')} last={s.get('last_offer')}"
            if fam == "bargaining":
                extra = (f"money={s.get('money_to_divide')} d1={s.get('delta_1')} "
                         f"d2={s.get('delta_2')} rounds={s.get('round')} hist_len={len(s.get('history') or [])}")
            print(f"{fam} {role} {g['game_id'][:8]} {extra[:180]}")
