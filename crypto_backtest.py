"""Fee-aware crypto feasibility backtest: $250 position, IBKR/Paxos fees, BTC & ETH.

IBKR crypto commission: 0.18% of trade value, min $1.75 per order (tiers ignored).
Fractional quantities allowed, so the whole $250 is deployed each entry.
Run: python crypto_backtest.py
"""
import warnings
warnings.filterwarnings("ignore")
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd, yfinance as yf
from strategies import ma_cross_signal, rsi

POS = 250.0
FEE_PCT, FEE_MIN = 0.0018, 1.75
SYMS = ["BTC-USD", "ETH-USD"]
GRID = [(10, 30), (20, 50), (50, 200)]
STOP, TP = 0.08, 0.20


def fee(value):
    return max(FEE_MIN, value * FEE_PCT)


def rsi_sig(closes, period=14, lo=30, hi=70):
    if len(closes) < period + 2: return None
    r = rsi(closes, period)
    p, c = r.iloc[-2], r.iloc[-1]
    if p < lo <= c: return "buy"
    if p > hi >= c: return "sell"
    return None


def donchian_sig(closes, n=20):
    """Breakout: buy on a new n-bar high, sell on a new n-bar low."""
    if len(closes) < n + 1: return None
    win = closes[-n - 1:-1]
    if closes[-1] > max(win): return "buy"
    if closes[-1] < min(win): return "sell"
    return None


def sim(close, sig_fn, warmup, stop=STOP, tp=TP):
    cash, qty, entry, trades, eq = POS, 0.0, 0.0, 0, []
    cl = close.tolist()
    for i in range(warmup, len(cl)):
        p = cl[i]
        if qty:
            ch = (p - entry) / entry
            if ch <= -stop or ch >= tp:
                cash += qty * p - fee(qty * p); qty = 0.0; trades += 1
                eq.append(cash); continue
        s = sig_fn(cl[: i + 1])
        if s == "buy" and not qty:
            f = fee(cash); qty = (cash - f) / p; cash = 0.0; entry = p; trades += 1
        elif s == "sell" and qty:
            cash += qty * p - fee(qty * p); qty = 0.0; trades += 1
        eq.append(cash + qty * p)
    fv = cash + qty * cl[-1]
    e = pd.Series(eq); dd = ((e - e.cummax()) / e.cummax()).min()
    bh = cl[-1] / cl[warmup] - 1
    return fv / POS - 1, bh, trades, dd


def run(label, period, interval, resample=None):
    rows = []
    for sym in SYMS:
        df = yf.download(sym, period=period, interval=interval, auto_adjust=True, progress=False)
        close = df["Close"].squeeze().dropna()
        if resample: close = close.resample(resample).last().dropna()
        cut = int(len(close) * 0.7)
        strategies = {f"MA{a}/{b}": (lambda c, a=a, b=b: ma_cross_signal(c, a, b), b) for a, b in GRID}
        strategies["RSI14"] = (rsi_sig, 20)
        strategies["DON20"] = (donchian_sig, 21)
        strategies["DON55"] = (lambda c: donchian_sig(c, 55), 56)
        for name, (fn, warm) in strategies.items():
            ins = sim(close.iloc[:cut], fn, warm)
            oos = sim(close.iloc[cut - warm:], fn, warm)
            rows.append((sym, name, ins[0], ins[1], oos[0], oos[1], ins[2] + oos[2], oos[3]))
    out = pd.DataFrame(rows, columns=["sym", "strat", "IS_ret", "IS_bh", "OOS_ret", "OOS_bh", "trades", "OOS_dd"])
    out["OOS_edge"] = out.OOS_ret - out.OOS_bh
    print(f"\n=== {label} ===")
    print(out.round(3).sort_values("OOS_edge", ascending=False).to_string(index=False))
    print("OOS beating buy&hold:", (out.OOS_edge > 0).sum(), "/", len(out),
          "| positive OOS:", (out.OOS_ret > 0).sum(), "/", len(out),
          "| median OOS ret:", round(out.OOS_ret.median(), 3), "median OOS B&H:", round(out.OOS_bh.median(), 3))
    return out


pd.set_option("display.width", 200)
run("Daily bars, 5y", "5y", "1d")
run("4-hour bars, 2y", "730d", "1h", resample="4h")
