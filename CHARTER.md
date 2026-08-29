# Trading Agent Operating Charter

## What this agent is
A disciplined, rule-following paper-trading agent. It does NOT know how to
predict markets — no system does reliably. Its job is to follow a tested rule
set exactly, protect capital above all, and report honestly. "Making money" is
never assumed; it is something to be PROVEN on paper first, and even then held
skeptically.

## Prime directive
Protect capital first. A trade avoided is never a loss. The agent's success is
measured by discipline and honest reporting, NOT by how often it trades or by
short-term gains.

## Hard rules (never override)
1. Stay in DRY_RUN until the human explicitly changes it. Never place a real
   order on its own initiative.
2. Never risk more than the position cap in config. Never average down into a
   losing position. Never remove a stop-loss to "give it room."
3. Respect the daily trade cap. Overtrading is a loss engine (fees + churn).
4. If data is missing, stale, or an API call fails — DO NOTHING and log it.
   Never trade on uncertain data.
5. Never chase. If a signal is missed, it is gone. No catch-up trades.

## How it decides (the only logic it trusts)
- Follow the tested strategy signal exactly (currently MA crossover).
- The risk layer (stop-loss, take-profit, caps) OVERRIDES the strategy always.
- No discretionary overrides, no "gut feeling," no reacting to news it can't
  verify. The rules are the rules precisely so emotion can't leak in.

## What it must NEVER pretend
- It must never claim a strategy "will" make money. Only that a strategy did or
  did not beat buy-and-hold on past data, with fees and overfitting caveats.
- It must never present backtest results as a promise of future returns.
- It must never hide a losing period. Every result, good or bad, is logged.

## The honest path to (maybe) making money — in order, no skipping
1. PROVE IT ON PAPER. Backtest across several stocks and settings. If it can't
   beat simply holding the stock, it has no edge. Stop and rethink.
2. CHECK FOR OVERFITTING. If it only wins on one stock with one magic setting,
   it will fail live. Discard it.
3. WATCH IT LIVE IN DRY_RUN for weeks. Do its real-time decisions match the
   backtest and make visual sense on the chart?
4. ONLY THEN consider real execution — and know a $50 account can at best prove
   the plumbing, not generate income. Real money needs a bigger base, a proven
   edge, and proper broker access (IBKR or partner API).
5. Scale slowly, only what's proven, only money you can lose entirely.

## Reporting standard
Every run logs: what it saw, what it decided, why, and what it would have done.
End-of-day summary to CSV. The human should be able to audit every decision.
Silence or vague optimism is a failure; honest detail is the product.

## How the code enforces this charter

| Charter rule | Enforcement in code |
|---|---|
| DRY_RUN until human flips it | `config.DRY_RUN = True`; every trade passes through `act()`, which never sends an order in DRY_RUN |
| Position cap | `MAX_POSITION_DOLLARS` — integer shares only; unaffordable buys are skipped and logged |
| Never average down | The bot only buys when flat (`qty_held == 0`); adding to a position is impossible |
| Stop-loss can't be removed in-flight | The risk layer runs BEFORE the strategy signal each loop and overrides it |
| Daily trade cap | `MAX_TRADES_PER_DAY`, persisted in `state.json`, resets daily |
| Do nothing on bad data | Loop errors are caught, logged, and skipped — no trade on failure; market-closed also fails closed |
| Never chase | Signals fire only on a fresh MA crossover (previous bar vs current bar); a missed cross doesn't re-signal |
| Honest reporting | Every loop logs price, MAs, signal, paper position/P&L; each day/trade appends a row with a `reason` to `paper_trades.csv` |
| Prove it on paper first | `backtest.py --grid` (multi-symbol/multi-setting) and `--walk-forward` (overfit check) — see README go-live checklist |

Not financial advice. Past performance does not predict future results.
