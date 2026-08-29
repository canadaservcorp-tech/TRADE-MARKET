"""
Backtest the configured strategy on historical data.

Simulates the SAME logic as bot.py (strategy signal from strategies.py +
stop-loss + take-profit) against years of past prices, then reports how it
did versus simply buying and holding. Switch strategies via config.STRATEGY
("ma_cross" or "rsi") to compare them.

Run:  python backtest.py

HONESTY NOTE (read before trusting any number this prints):
  - Results IGNORE fees, slippage, and the bid/ask spread. Real results are worse.
  - A strategy tuned to look good on past data often fails live ("overfitting").
  - Past performance does not predict future performance.
"""

import yfinance as yf
import pandas as pd

import config
from strategies import get_signal_fn

STARTING_CASH = 1000.0
YEARS = 5
WARMUP = max(config.LONG_WINDOW, config.RSI_PERIOD) + 1


def run_backtest():
    print(f"Downloading {YEARS}y of daily data for {config.SYMBOL}...")
    df = yf.download(config.SYMBOL, period=f"{YEARS}y",
                     interval="1d", auto_adjust=True, progress=False)
    if df.empty:
        print("No data returned. Check the symbol.")
        return

    signal_fn = get_signal_fn()
    close = df["Close"].squeeze()
    closes_list = close.tolist()

    cash = STARTING_CASH
    shares = 0
    entry_price = 0.0
    trades = []
    equity_curve = []
    unaffordable_buys = 0

    for i in range(WARMUP, len(close)):
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
    strat_return = (final_value - STARTING_CASH) / STARTING_CASH
    buy_hold_return = (close.iloc[-1] - close.iloc[WARMUP]) / close.iloc[WARMUP]

    eq = pd.Series(equity_curve)
    drawdown = (eq - eq.cummax()) / eq.cummax()
    max_drawdown = drawdown.min()

    sells = [t for t in trades if t[1].startswith("SELL")]
    wins, last_buy = 0, None
    for t in trades:
        if t[1] == "BUY":
            last_buy = t[2]
        elif t[1].startswith("SELL") and last_buy is not None:
            if t[2] > last_buy:
                wins += 1
            last_buy = None
    win_rate = (wins / len(sells)) if sells else 0

    print("\n" + "=" * 50)
    print(f"BACKTEST: {config.SYMBOL}  ({YEARS} years, daily)")
    if config.STRATEGY == "ma_cross":
        print(f"Strategy: {config.SHORT_WINDOW}/{config.LONG_WINDOW} MA crossover")
    else:
        print(f"Strategy: RSI({config.RSI_PERIOD}) "
              f"{config.RSI_OVERSOLD}/{config.RSI_OVERBOUGHT}")
    print("=" * 50)
    print(f"Starting cash:        ${STARTING_CASH:,.2f}")
    print(f"Ending value:         ${final_value:,.2f}")
    print(f"Strategy return:      {strat_return:+.1%}")
    print(f"Buy-and-hold return:  {buy_hold_return:+.1%}  <-- did the bot beat this?")
    print(f"Number of trades:     {len(trades)}")
    print(f"Win rate:             {win_rate:.0%}  (of {len(sells)} completed trades)")
    print(f"Max drawdown:         {max_drawdown:.1%}")
    print("=" * 50)

    if unaffordable_buys:
        print(f"\n>> {unaffordable_buys} buy signal(s) skipped: one share of "
              f"{config.SYMBOL} costs more than MAX_POSITION_DOLLARS "
              f"(${config.MAX_POSITION_DOLLARS}). The live bot would skip the "
              "same way — pick a cheaper symbol or raise the cap.")

    if strat_return < buy_hold_return:
        print("\n>> The strategy UNDERPERFORMED buy-and-hold. Common — it added risk "
              "without adding return.")
    else:
        print("\n>> The strategy beat buy-and-hold in THIS period. Don't over-trust it: "
              "one period, no fees, overfitting risk.")

    print("\nLast 10 trades:")
    for t in trades[-10:]:
        print(f"  {t[0]}  {t[1]:20s}  {t[3]} @ ${t[2]}")


if __name__ == "__main__":
    run_backtest()
