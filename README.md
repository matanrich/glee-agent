# Equilibrist — GLEE Competition agent

Agent for the [GLEE Competition](https://glee-competition.com) (NeurIPS 2026 IAB
workshop). Account: matan@xsparx.io · agent id `d29d57fd038c` · name **Equilibrist**.
Competition closes **August 29, 2026 (AoE)**.

## Run

```bash
./run.sh          # starts agent.py in the background, logs to agent.log
tail -f agent.log
```

The API key lives in `.env` (not committed anywhere). A lost key can be reset from
the dashboard card (invalidates the old one immediately).

**Keep it running until the close.** The machine must stay awake:

- Ratings above 1800 (raw) decay unless the agent plays 100 games/family per 48h.
- Top-100 agents must play ≥10 games/day per family (1 rating point per missing game).
- The run loop (`concurrency=12`, `poll_interval=3.0`) comfortably exceeds both when
  left on. Polling costs one request per cycle regardless of games in flight, and the
  60 req/min budget is the throughput cap — poll rarely, play many games at once.

Stop it with `pkill -f 'python agent.py'` — the SDK leaves the matchmaking queue on
exit, so nothing times out.

## Strategy

Scoring is **self-gain percentile within the exact game configuration**, opponent-
adjusted; no-deals and abandonments score at the bottom. So the agent is aggressive
but always closes, and is engineered never to time out or submit five invalid moves.

- **Bargaining** — backward induction over the known horizon (Rubinstein closed form
  when unbounded) using both inflation rates, with realism clamps for the actual
  pool (demand 50–85%, concede after rejections, final-round ultimatum kept below the
  spite line, accept anything on the last round, never walk away).
- **Negotiation** — anchor near the opponent-value bound parsed from the prompt,
  concede along a quadratic curve toward reservation (own valuation), never accept a
  loss, never counter on the wrong side of the standing offer; take-it-or-leave-it
  priced at the uniform-buyer optimum `(hi + V)/2`.
- **Persuasion** — seller: always recommend when the prior alone justifies buying or
  the buyer has shown they ignore messages (bought on a "no"), otherwise honest
  signaling with a defection window sized by what a lie risks (`rounds_left × p <
  1.5`); buyer: quality is iid with known `p`, so outcomes never update the prior —
  only a seller who discriminates (sometimes says no) is judged on the track record
  of their "yes", and liars get cut off.
- **Safety net** — moves filtered against `valid_actions.fields`, exact-sum splits,
  per-decision-point retry memory that degrades to ultra-safe moves, catch-all
  fallback. Pure Python, so a turn can never hit the 120 s limit.

## Test

```bash
.venv/bin/python test_agent.py   # 23 offline branch tests, no network
```

## Iterating

Up to 5 agents per account; only the best one ranks the account. To A/B a variant,
create a second agent in the dashboard and run a copy with its key. Post-game
percentiles (shown in the dashboard history) are the tuning signal: look for
configurations where the percentile is consistently low and adjust that branch.

`analyze.py` pulls every completed game's transcript through the API (cached in
`games/`, throttled to respect the rate limit the live agent shares) and prints
per-family/role deal rates, surplus shares, persuasion round-by-round detail, and
all no-deal games:

```bash
set -a; source .env; set +a; .venv/bin/python analyze.py
```
