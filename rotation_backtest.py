"""Fee-aware ETF momentum-rotation backtest (candidate strategy for bot2.py).

Each month-end, rank a universe of ETFs by the average of their 3/6/12-month
returns and hold the top N (equal dollars). An ETF is only eligible if its
momentum score is positive (otherwise that slot sits in cash). Whole shares,
$1 commission per order. Reported at two capital levels so the fee drag
at small size is explicit. Walk-forward: first 60% in-sample, last 40% out.

Run: python rotation_backtest.py            (bot 2 universe: asset classes)
     python rotation_backtest.py sectors    (bot 1 universe: US sectors)
"""
import warnings
warnings.filterwarnings("ignore")
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd, yfinance as yf
from strategies import rotation_targets

# Cheap share classes (all < $100) so whole shares fit a $250 sleeve:
# US large / US growth / US small / intl dev / EM / long Treasuries / gold / energy / REIT
UNIVERSES = {
    "assets": (["SCHX", "SCHG", "SCHA", "SCHF", "SCHE", "SPTL", "IAU", "XLE", "SCHH"],
               "SCHX", "2011-01-01"),   # SCHH (youngest) starts 2011-01
    # SPDR sectors (1998-): bot 1's universe, disjoint from bot 2's so the two
    # bots never hold the same ticker (they'd misread each other's shares).
    "sectors": (["XLB", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY"],
                "SPY", "1999-01-01"),
}
UNIVERSE, BENCH, START = UNIVERSES[sys.argv[1] if len(sys.argv) > 1 else "assets"]
LOOKBACKS = (3, 6, 12)   # months
FEE = 1.0


def month_closes():
    df = yf.download(UNIVERSE + [BENCH], start=START, interval="1d", auto_adjust=True, progress=False)["Close"]
    return df.dropna(), df.dropna().resample("ME").last()


def rotate(monthly, capital, top_n, lookbacks=LOOKBACKS):
    """Returns (final equity, trade count, max drawdown, equity series).
    Uses strategies.rotation_targets (the bot's own ranking) on monthly bars."""
    cash, hold, trades, eq = capital, {}, 0, []
    for i in range(max(lookbacks), len(monthly) - 1):
        px = monthly.iloc[i]
        hist = {s: monthly[s].iloc[: i + 1].tolist() for s in UNIVERSE}
        target = rotation_targets(hist, top_n, lookbacks, days_per_month=1)
        # sell what is no longer wanted
        for sym in [s for s in hold if s not in target]:
            cash += hold.pop(sym) * px[sym] - FEE; trades += 1
        # buy new names with an equal slice of free cash
        new = [s for s in target if s not in hold]
        for j, sym in enumerate(new):
            slice_ = cash / (len(new) - j)
            q = int((slice_ - FEE) // px[sym])
            if q > 0:
                cash -= q * px[sym] + FEE; hold[sym] = q; trades += 1
        eq.append(cash + sum(q * px[s] for s, q in hold.items()))
    e = pd.Series(eq, index=monthly.index[max(lookbacks):len(monthly) - 1])
    dd = ((e - e.cummax()) / e.cummax()).min()
    return e.iloc[-1], trades, dd, e


def cagr(e):
    yrs = (e.index[-1] - e.index[0]).days / 365.25
    return (e.iloc[-1] / e.iloc[0]) ** (1 / yrs) - 1


def report(label, monthly):
    bh = monthly[BENCH]
    bh_ret = bh.iloc[-1] / bh.iloc[max(LOOKBACKS)] - 1
    bh_e = bh.iloc[max(LOOKBACKS):]
    bh_dd = ((bh_e - bh_e.cummax()) / bh_e.cummax()).min()
    yrs = (monthly.index[-1] - monthly.index[max(LOOKBACKS)]).days / 365.25
    print(f"\n=== {label}: {monthly.index[max(LOOKBACKS)].date()} -> {monthly.index[-1].date()} ({yrs:.1f}y) ===")
    print(f"  {BENCH} buy&hold: {bh_ret:+.1%} total, {(1 + bh_ret) ** (1 / yrs) - 1:+.1%}/yr, maxDD {bh_dd:.1%}")
    rows = []
    for capital in (250, 2000):
        for top_n in (1, 2, 3):
            fv, tr, dd, e = rotate(monthly, capital, top_n)
            rows.append((capital, top_n, fv / capital - 1, cagr(e), dd, tr, tr * FEE / capital))
    out = pd.DataFrame(rows, columns=["capital", "top_n", "total_ret", "CAGR", "maxDD", "trades", "fees/capital"])
    print(out.to_string(index=False, formatters={
        "total_ret": "{:+.1%}".format, "CAGR": "{:+.1%}".format, "maxDD": "{:.1%}".format,
        "fees/capital": "{:.1%}".format}))


if __name__ == "__main__":
    _, monthly = month_closes()
    cut = int(len(monthly) * 0.6)
    report("FULL", monthly)
    report("IN-SAMPLE (first 60%)", monthly.iloc[:cut])
    report("OUT-OF-SAMPLE (last 40%)", monthly.iloc[cut - max(LOOKBACKS):])
