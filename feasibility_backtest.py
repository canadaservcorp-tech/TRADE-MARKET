"""Fee-aware feasibility backtest: $300 position, $1/side commission, 5y daily.

Companion to FEASIBILITY.md. Run: python feasibility_backtest.py
"""
import warnings
warnings.filterwarnings("ignore")
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd, yfinance as yf
from strategies import ma_cross_signal, rsi

POS = 300.0
FEE = 1.0
SYMS = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "F", "SOFI", "PLTR", "AMD", "XLE"]
GRID = [(10, 30), (20, 50), (50, 200)]
STOP, TP = 0.05, 0.10

def rsi_sig(closes, period=14, lo=30, hi=70):
    if len(closes) < period + 2: return None
    r = rsi(closes, period)
    p, c = r.iloc[-2], r.iloc[-1]
    if p < lo <= c: return "buy"
    if p > hi >= c: return "sell"
    return None

def sim(close, sig_fn, warmup, stop=STOP, tp=TP):
    cash, sh, entry, trades, wins, eq = POS, 0, 0.0, 0, 0, []
    cl = close.tolist()
    for i in range(warmup, len(cl)):
        p = cl[i]
        if sh:
            ch = (p - entry) / entry
            if ch <= -stop or ch >= tp:
                cash += sh * p - FEE; wins += p > entry; sh = 0; trades += 1
                eq.append(cash); continue
        s = sig_fn(cl[: i + 1])
        if s == "buy" and not sh:
            q = int((cash - FEE) // p)
            if q: cash -= q * p + FEE; sh, entry = q, p; trades += 1
        elif s == "sell" and sh:
            cash += sh * p - FEE; wins += p > entry; sh = 0; trades += 1
        eq.append(cash + sh * p)
    fv = cash + sh * cl[-1]
    e = pd.Series(eq); dd = ((e - e.cummax()) / e.cummax()).min()
    bh = cl[-1] / cl[warmup] - 1
    return fv / POS - 1, bh, trades, dd

rows = []
for sym in SYMS:
    df = yf.download(sym, period="5y", interval="1d", auto_adjust=True, progress=False)
    close = df["Close"].squeeze()
    cut = int(len(close) * 0.7)
    strategies = {f"MA{a}/{b}": (lambda c, a=a, b=b: ma_cross_signal(c, a, b), b) for a, b in GRID}
    strategies["RSI14"] = (rsi_sig, 20)
    for name, (fn, warm) in strategies.items():
        ins = sim(close.iloc[:cut], fn, warm)
        oos = sim(close.iloc[cut - warm:], fn, warm)
        rows.append((sym, name, ins[0], ins[1], oos[0], oos[1], ins[2] + oos[2], oos[3]))

pd.set_option("display.width", 200)
out = pd.DataFrame(rows, columns=["sym", "strat", "IS_ret", "IS_bh", "OOS_ret", "OOS_bh", "trades", "OOS_dd"])
out["OOS_edge"] = out.OOS_ret - out.OOS_bh
print(out.round(3).sort_values("OOS_edge", ascending=False).to_string(index=False))
print("\nOOS strategies beating buy&hold:", (out.OOS_edge > 0).sum(), "/", len(out))
print("OOS strategies with positive return:", (out.OOS_ret > 0).sum(), "/", len(out))
print("Median OOS return:", round(out.OOS_ret.median(), 3), " median OOS B&H:", round(out.OOS_bh.median(), 3))
