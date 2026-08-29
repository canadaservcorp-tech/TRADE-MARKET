"""
Backtest the configured strategy on historical data.

Simulates the SAME logic as bot.py (strategy signal from strategies.py +
stop-loss + take-profit) against years of past prices, then reports how it
did versus simply buying and holding.

Run:
  python backtest.py                       single run (config.SYMBOL/STRATEGY)
  python backtest.py --walk-forward        also split ~70% in-sample / ~30% out-of-sample
  python backtest.py --grid                sweep config.BACKTEST_SYMBOLS x BACKTEST_MA_GRID
  python backtest.py --grid --walk-forward grid with in/out-of-sample columns

HONESTY NOTE (read before trusting any number this prints):
  - Results IGNORE fees, slippage, and the bid/ask spread. Real results are worse.
  - A strategy tuned to look good on past data often fails live ("overfitting").
    The --grid overfit warnings and the --walk-forward in/out-of-sample gap
    exist to expose exactly that.
  - Past performance does not predict future performance.
"""

import argparse

import yfinance as yf
import pandas as pd

import config
from strategies import get_signal_fn, ma_cross_signal

STARTING_CASH = 1000.0
YEARS = 5
WALK_FORWARD_SPLIT = 0.7


def download_closes(symbol):
    df = yf.download(symbol, period=f"{YEARS}y",
                     interval="1d", auto_adjust=True, progress=False)
    if df.empty:
        return None
    return df["Close"].squeeze()


def simulate(close, signal_fn, warmup):
    """Run the bot's decision loop over a series of daily closes.

    `close` is a pd.Series (date-indexed). The first `warmup` bars are used
    only as signal history — trading starts after them, and buy-and-hold is
    measured over the same tradable window so the comparison is fair.
    Returns None if the series is too short to trade.
    """
    if len(close) <= warmup + 1:
        return None

    closes_list = close.tolist()
    cash = STARTING_CASH
    shares = 0
    entry_price = 0.0
    trades = []
    equity_curve = []
    unaffordable_buys = 0

    for i in range(warmup, len(close)):
        price = close.iloc[i]
        date = close.index[i].date()

        if shares > 0 and entry_price > 0:
            change = (price - entry_price) / entry_price
            if change <= -config.STOP_LOSS_PCT or change >= config.TAKE_PROFIT_PCT:
                cash += shares * price
                trades.append((date, "SELL (stop/target)", round(price, 2), shares))
                shares, entry_price = 0, 0.0
                equity_curve.append(cash)
                continue

        # Same signal function the live bot uses, on data up to this day.
        sig = signal_fn(closes_list[: i + 1])

        if sig == "buy" and shares == 0:
            qty = int(config.MAX_POSITION_DOLLARS // price)
            if qty >= 1:
                cash -= qty * price
                shares, entry_price = qty, price
                trades.append((date, "BUY", round(price, 2), qty))
            else:
                unaffordable_buys += 1
        elif sig == "sell" and shares > 0:
            cash += shares * price
            trades.append((date, "SELL (signal)", round(price, 2), shares))
            shares, entry_price = 0, 0.0

        equity_curve.append(cash + shares * price)

    final_value = cash + shares * close.iloc[-1]

    eq = pd.Series(equity_curve)
    drawdown = (eq - eq.cummax()) / eq.cummax()

    sells = [t for t in trades if t[1].startswith("SELL")]
    wins, last_buy = 0, None
    for t in trades:
        if t[1] == "BUY":
            last_buy = t[2]
        elif t[1].startswith("SELL") and last_buy is not None:
            if t[2] > last_buy:
                wins += 1
            last_buy = None

    return {
        "strat_return": (final_value - STARTING_CASH) / STARTING_CASH,
        "bh_return": (close.iloc[-1] - close.iloc[warmup]) / close.iloc[warmup],
        "n_trades": len(trades),
        "win_rate": (wins / len(sells)) if sells else 0.0,
        "max_drawdown": float(drawdown.min()),
        "unaffordable_buys": unaffordable_buys,
        "trades": trades,
        "final_value": final_value,
    }


def walk_forward(close, signal_fn, warmup):
    """Split the history ~70/30: pick settings on the first part (in-sample),
    then see if they still work on data they never saw (out-of-sample)."""
    cut = int(len(close) * WALK_FORWARD_SPLIT)
    in_sample = simulate(close.iloc[:cut], signal_fn, warmup)
    # Out-of-sample keeps `warmup` bars of lead-in history for the signal but
    # only trades on the held-out final ~30%.
    out_sample = simulate(close.iloc[max(0, cut - warmup):], signal_fn, warmup)
    return in_sample, out_sample


def fmt_pct(x):
    return f"{x:+.1%}" if x is not None else "   n/a"


def run_grid(with_walk_forward):
    rows = []
    for symbol in config.BACKTEST_SYMBOLS:
        print(f"Downloading {YEARS}y of daily data for {symbol}...")
        close = download_closes(symbol)
        if close is None:
            print(f"  No data for {symbol}; skipping.")
            continue
        for short_w, long_w in config.BACKTEST_MA_GRID:
            def signal_fn(c, s=short_w, l=long_w):
                return ma_cross_signal(c, s, l)
            warmup = long_w + 1
            res = simulate(close, signal_fn, warmup)
            if res is None:
                continue
            row = {"symbol": symbol, "short": short_w, "long": long_w, **res}
            if with_walk_forward:
                is_res, oos_res = walk_forward(close, signal_fn, warmup)
                row["is"] = is_res
                row["oos"] = oos_res
            rows.append(row)

    if not rows:
        print("No results.")
        return

    print("\n" + "=" * 100)
    print(f"GRID BACKTEST — MA crossover, {YEARS} years daily, "
          f"${config.MAX_POSITION_DOLLARS} position cap, no fees/slippage")
    print("=" * 100)
    header = (f"{'symbol':<8}{'MA':>8}  {'strategy':>9}{'buy&hold':>10}"
              f"{'trades':>8}{'win%':>7}{'maxDD':>8}  {'beats B&H?':<11}")
    if with_walk_forward:
        header += f"{'in-sample':>10}{'out-sample':>11}  {'verdict':<24}"
    print(header)
    print("-" * 100)

    for r in rows:
        beats = r["strat_return"] > r["bh_return"]
        windows = f"{r['short']}/{r['long']}"
        line = (f"{r['symbol']:<8}{windows:>8}  "
                f"{fmt_pct(r['strat_return']):>9}{fmt_pct(r['bh_return']):>10}"
                f"{r['n_trades']:>8}{r['win_rate']:>7.0%}{r['max_drawdown']:>8.1%}  "
                f"{'YES' if beats else 'no':<11}")
        if with_walk_forward:
            is_r, oos_r = r["is"], r["oos"]
            is_edge = (is_r["strat_return"] - is_r["bh_return"]) if is_r else None
            oos_edge = (oos_r["strat_return"] - oos_r["bh_return"]) if oos_r else None
            if is_edge is None or oos_edge is None:
                verdict = "too little data"
            elif is_edge > 0 and oos_edge <= 0:
                verdict = "OVERFIT (wins IS, loses OOS)"
            elif is_edge > 0 and oos_edge > 0:
                verdict = "holds up out-of-sample"
            else:
                verdict = "no edge in-sample"
            line += (f"{fmt_pct(is_edge):>10}{fmt_pct(oos_edge):>11}  {verdict:<24}")
        print(line)

    print("-" * 100)
    if with_walk_forward:
        print("in-sample / out-sample columns show the strategy's EDGE over "
              "buy-and-hold on each segment (first ~70% vs held-out last ~30%).")

    # Overfit warning: a setting that only wins on one symbol, or a symbol
    # where only one setting wins, is likely curve-fit rather than a real edge.
    for symbol in config.BACKTEST_SYMBOLS:
        sym_rows = [r for r in rows if r["symbol"] == symbol]
        winners = [r for r in sym_rows if r["strat_return"] > r["bh_return"]]
        if len(sym_rows) > 1 and len(winners) == 1:
            w = winners[0]
            print(f"\n>> OVERFIT WARNING: for {symbol}, only ONE setting "
                  f"({w['short']}/{w['long']}) beats buy-and-hold. A robust edge "
                  "usually shows up across neighbouring settings too — this one "
                  "is likely fit to this particular history.")
        skipped = sum(r["unaffordable_buys"] for r in sym_rows)
        if skipped:
            print(f"\n>> {symbol}: {skipped} buy signal(s) skipped across the grid — "
                  f"one share costs more than MAX_POSITION_DOLLARS "
                  f"(${config.MAX_POSITION_DOLLARS}). The live bot would skip the same way.")


def run_single(with_walk_forward):
    print(f"Downloading {YEARS}y of daily data for {config.SYMBOL}...")
    close = download_closes(config.SYMBOL)
    if close is None:
        print("No data returned. Check the symbol.")
        return

    signal_fn = get_signal_fn()
    warmup = max(config.LONG_WINDOW, config.RSI_PERIOD) + 1
    res = simulate(close, signal_fn, warmup)
    if res is None:
        print("Not enough data to backtest.")
        return

    print("\n" + "=" * 50)
    print(f"BACKTEST: {config.SYMBOL}  ({YEARS} years, daily)")
    if config.STRATEGY == "ma_cross":
        print(f"Strategy: {config.SHORT_WINDOW}/{config.LONG_WINDOW} MA crossover")
    else:
        print(f"Strategy: RSI({config.RSI_PERIOD}) "
              f"{config.RSI_OVERSOLD}/{config.RSI_OVERBOUGHT}")
    print("=" * 50)
    print(f"Starting cash:        ${STARTING_CASH:,.2f}")
    print(f"Ending value:         ${res['final_value']:,.2f}")
    print(f"Strategy return:      {res['strat_return']:+.1%}")
    print(f"Buy-and-hold return:  {res['bh_return']:+.1%}  <-- did the bot beat this?")
    print(f"Number of trades:     {res['n_trades']}")
    print(f"Win rate:             {res['win_rate']:.0%}")
    print(f"Max drawdown:         {res['max_drawdown']:.1%}")
    print("=" * 50)

    if res["unaffordable_buys"]:
        print(f"\n>> {res['unaffordable_buys']} buy signal(s) skipped: one share of "
              f"{config.SYMBOL} costs more than MAX_POSITION_DOLLARS "
              f"(${config.MAX_POSITION_DOLLARS}). The live bot would skip the "
              "same way — pick a cheaper symbol or raise the cap.")

    if res["strat_return"] < res["bh_return"]:
        print("\n>> The strategy UNDERPERFORMED buy-and-hold. Common — it added risk "
              "without adding return.")
    else:
        print("\n>> The strategy beat buy-and-hold in THIS period. Don't over-trust it: "
              "one period, no fees, overfitting risk.")

    if with_walk_forward:
        is_res, oos_res = walk_forward(close, signal_fn, warmup)
        print("\n" + "=" * 50)
        print(f"WALK-FORWARD: first {WALK_FORWARD_SPLIT:.0%} in-sample, "
              f"last {1 - WALK_FORWARD_SPLIT:.0%} held out")
        print("=" * 50)
        for label, r in (("In-sample ", is_res), ("Out-sample", oos_res)):
            if r is None:
                print(f"{label}: not enough data")
                continue
            edge = r["strat_return"] - r["bh_return"]
            print(f"{label}: strategy {r['strat_return']:+.1%}  "
                  f"buy&hold {r['bh_return']:+.1%}  edge {edge:+.1%}  "
                  f"({r['n_trades']} trades)")
        if is_res and oos_res:
            is_edge = is_res["strat_return"] - is_res["bh_return"]
            oos_edge = oos_res["strat_return"] - oos_res["bh_return"]
            gap = is_edge - oos_edge
            print(f"Gap (in-sample edge minus out-of-sample edge): {gap:+.1%}")
            if is_edge > 0 and oos_edge <= 0:
                print(">> OVERFIT: the edge exists only on data the settings were "
                      "picked on. Do not trust this configuration.")

    print("\nLast 10 trades:")
    for t in res["trades"][-10:]:
        print(f"  {t[0]}  {t[1]:20s}  {t[3]} @ ${t[2]}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--grid", action="store_true",
                        help="sweep BACKTEST_SYMBOLS x BACKTEST_MA_GRID from config.py")
    parser.add_argument("--walk-forward", action="store_true",
                        help="report in-sample (~70%%) vs held-out out-of-sample (~30%%)")
    args = parser.parse_args()
    if args.grid:
        run_grid(args.walk_forward)
    else:
        run_single(args.walk_forward)
