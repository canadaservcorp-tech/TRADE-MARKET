# Feasibility test: can this bot make real money with a few hundred dollars?

This is the plan for answering that question with evidence instead of hope.
It has three parts: what the data already says, the exact test to run, and
the pass/fail rules decided **before** the test starts so the result cannot
be rationalised afterwards.

## 1. What we already know (fee-aware backtest, 5 years daily)

Setup: $300 position, $1 commission per side, 5%/10% stop/target,
10 symbols x 4 strategies (MA 10/30, 20/50, 50/200, RSI-14), 70% in-sample /
30% out-of-sample split. Script: `python feasibility_backtest.py`.

| metric (out-of-sample, last ~18 months)             | result        |
|-----------------------------------------------------|---------------|
| strategy/symbol combos beating buy-and-hold          | **0 / 40**    |
| combos with a positive return                        | 17 / 40       |
| median strategy return                               | 0.0%          |
| median buy-and-hold return over the same window      | +46.5%        |
| best combo (F, MA 10/30)                             | +30% vs +38% B&H, 51 trades, -17% drawdown |
| combos that could not buy a single share at $300     | SPY, QQQ, MSFT (all 0 trades) |

Conclusion from the data: **the current strategies have no edge**. They are
trend filters that mostly keep you *out* of the market; in a rising market
that loses to holding, and in a falling market they save a little (F 2021-23:
strategy -12% to -21% vs -35% B&H) but still lose money. Fees make every
high-frequency variant (MA 10/30, RSI) worse.

## 2. The honest arithmetic at $300-$500

- Whole shares only (cash account, no fractional orders in `ibkr.py`), so the
  universe is stocks under ~$100 -> ~3-30 shares per trade.
- A winning trade at the 10% target on $300 = **+$30**; a stop-out = **-$15**;
  commissions = **-$2** per round trip. Breakeven win rate at that ratio is
  ~37%. Backtested win rates were 38-60%, i.e. around breakeven before slippage.
- MA 50/200 fires ~2 signals per year per symbol. Even if it worked, expected
  profit is on the order of **$10-40 per year** per $300. It cannot be an income
  at this scale; the only thing a few hundred dollars can buy is *information*.

So the realistic question is not "will it make money" but "does the live
result match the backtest?" If yes, the system is trustworthy and scaling
capital is a decision about risk appetite. If no, something is broken and
scaling would only lose faster.

## 3. The test protocol ("model of instruction")

### Capital & risk
| parameter              | value  | why |
|------------------------|--------|-----|
| deposit                | $300-500 CAD/USD | enough for 1 real position + commissions; small enough to lose entirely |
| `MAX_POSITION_DOLLARS` | 250    | one position at a time, leaves cash for fees/T+1 settlement |
| `STOP_LOSS_PCT`        | 0.05   | max loss per trade ~ $12.50 |
| `TAKE_PROFIT_PCT`      | 0.10   | 2:1 reward/risk |
| `MAX_TRADES_PER_DAY`   | 1      | this strategy should never need more |
| hard floor             | stop the test if account equity < 80% of deposit | pre-committed kill line |

### Strategy under test
`ma_cross` 50/200 on **F** (already configured). It is the only setting that
was not flagged overfit, and it trades rarely so fees are negligible. It is
also the one most likely to do *nothing* for months; that is acceptable — a
"no signal" streak is a valid result, not a failure.

Add one liquid, cheap second symbol later only if the first phase completes.

### Phases
| phase | duration | mode | goal |
|-------|----------|------|------|
| 0. Paper | 4 weeks | `DRY_RUN = True` via `schedule_bot.bat` | confirm daily run happens, `paper_trades.csv` fills, signals match a manual MA check |
| 1. Live micro | 3 months (or first 3 completed round-trips, whichever is later) | `DRY_RUN = False`, caps above | measure live fill vs backtest assumption, slippage, commission, ops failures |
| 2. Decision | 1 session | analysis | compare against the criteria below; decide scale / change strategy / stop |

### Daily operation (already automated)
1. PC on, TWS logged in before 10:00 local.
2. Task Scheduler runs `run_bot.bat --once` (Mon-Fri 10:00).
3. Weekly: open `bot.log` and `state.json`; note any `ERROR` lines.
4. Never edit caps mid-test. Changes reset the test clock.

### Metrics to record (weekly, in a spreadsheet)
- days the scheduled run actually executed / trading days (target >= 95%)
- each fill: date, side, qty, expected price (log) vs actual fill (TWS), commission
- account equity
- any manual intervention (and why)

### Pass / fail criteria (decided now)
| outcome after phase 1 | verdict | next step |
|-----------------------|---------|-----------|
| >= 95% scheduled runs executed, every fill within 0.5% of logged price, no unexplained order, equity >= 80% of deposit | **system trustworthy** | strategy is still unproven; move to strategy research (sec. 4) before adding capital |
| any order that violated a cap, or a fill the log cannot explain | **system failure** | fix in DRY_RUN, restart phase 0 |
| equity < 80% of deposit | **stop** | do not add money; strategy or execution is wrong |
| zero signals for 3 months | **inconclusive but fine** | extend, or add a second symbol |

Note the first row: even a perfect phase 1 does **not** prove the strategy
makes money — three trades is far too few. It proves the plumbing. Profit
claims need >= 30 trades or >= 1 full market cycle.

## 4. What would actually have to change for profit to be plausible
1. **A strategy with an out-of-sample edge after fees.** Candidates worth
   backtesting next (all still simple, all runnable by this bot with small
   changes): monthly momentum rotation across 3-5 ETFs; trend-following with
   volatility-scaled position size; buying the 200-day MA dip inside an
   uptrend. Each must be tested the same way as section 1 and beat
   buy-and-hold out-of-sample before it touches real money.
2. **More capital per position** ($1-2k) so a $1 commission is < 0.1% and
   whole-share rounding stops mattering.
3. **Diversification** across several uncorrelated symbols rather than one.
4. **Realistic expectations**: a good retail trend-following system targets
   roughly 5-15%/year with 10-25% drawdowns. On $500 that is $25-75/year.

## 4b. Second bot: crypto (ETH on IBKR/PAXOS) — the "double game"

Rationale: a second bot on an asset that is only loosely correlated with a
US stock diversifies the equity curve. It does **not** create an edge —
two zero-edge bots are still zero edge. Doubling size after a loss
(martingale) is *not* what this does and is not recommended.

`crypto_backtest.py`: $250 ticket, IBKR crypto fee (0.18%, min $1.75),
fractional size, 8% stop / 20% target, BTC & ETH, MA 10/30, 20/50, 50/200,
RSI-14, Donchian 20/55, 70/30 walk-forward.

| bars | combos beating B&H OOS | positive OOS | median OOS ret | median OOS B&H |
|---|---|---|---|---|
| daily, 5y | 2 / 12 | 5 / 12 | -4.5% | +20.5% |
| 4-hour, 2y | 1 / 12 | 5 / 12 | -3.1% | +29.7% |

Only **ETH MA 20/50 daily** beat buy-and-hold in *both* halves
(in-sample +6% vs -52% B&H; out-of-sample +50% vs +41% B&H, 36 trades,
-23% max drawdown). Faster settings (MA 10/30, Donchian 20) were destroyed
by fees and whipsaws (-20% to -50% OOS). That is the setting in
`config.py` (`CRYPTO_*`), with the same caveat as the stock bot: one
surviving combo out of 24 is weak evidence and may be luck.

How it runs: `crypto_bot.py` is a separate process (own log, state file,
kill switch `KILL_SWITCH_CRYPTO`, IBKR client id 2). `CRYPTO_ENABLED`
is **False** by default; flip it only after (1) confirming crypto trading
permission is enabled on the IBKR account and (2) paper-running with
`DRY_RUN = True`. Schedule with `schedule_crypto_bot.bat` (daily, 10:05,
weekends included). Apply the same phases and pass/fail criteria as
section 3, with the crypto-specific floor: stop if the crypto sleeve
drops below 70% of its ticket (wider than stocks because a single 8%
stop is a normal day in crypto).

## 5. Effort estimate
- Phase 0/1 are calendar time, not work: ~5 minutes/week of log review.
- Strategy research (sec. 4.1): 1-2 Devin sessions to implement and backtest
  the three candidates with fees and walk-forward, then a decision.
