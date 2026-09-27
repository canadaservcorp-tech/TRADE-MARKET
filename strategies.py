"""Trading signals. Selected via config.STRATEGY ("ma_cross" or "rsi").

Both strategies take a list of daily closes (oldest first) and return
'buy', 'sell', or None. The backtester reuses these same functions so a
backtest exercises exactly the logic the bot trades with.
"""

import logging

import pandas as pd

import config

log = logging.getLogger("strategies")


def ma_cross_signal(closes, short_window=None, long_window=None):
    """Moving-average crossover: fast MA crossing the slow MA.

    Windows default to config; the backtest grid passes explicit ones.
    """
    short_window = short_window or config.SHORT_WINDOW
    long_window = long_window or config.LONG_WINDOW
    if len(closes) < long_window + 1:
        log.warning("Not enough price history yet.")
        return None
    s = pd.Series(closes)
    short = s.rolling(short_window).mean()
    long = s.rolling(long_window).mean()
    ps, pl = short.iloc[-2], long.iloc[-2]
    cs, cl = short.iloc[-1], long.iloc[-1]
    if ps <= pl and cs > cl:
        return "buy"
    if ps >= pl and cs < cl:
        return "sell"
    return None


def ma_snapshot(closes):
    """Current (short MA, long MA) values, or None if not enough history."""
    if len(closes) < config.LONG_WINDOW:
        return None
    s = pd.Series(closes)
    return (s.rolling(config.SHORT_WINDOW).mean().iloc[-1],
            s.rolling(config.LONG_WINDOW).mean().iloc[-1])


def rsi(closes, period):
    """Wilder's RSI over a series of closes."""
    s = pd.Series(closes)
    delta = s.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, pd.NA)
    return (100 - 100 / (1 + rs)).fillna(100)


def rsi_signal(closes):
    """RSI mean-reversion: buy leaving oversold, sell leaving overbought."""
    if len(closes) < config.RSI_PERIOD + 2:
        log.warning("Not enough price history yet.")
        return None
    r = rsi(closes, config.RSI_PERIOD)
    prev, cur = r.iloc[-2], r.iloc[-1]
    if prev < config.RSI_OVERSOLD <= cur:
        return "buy"
    if prev > config.RSI_OVERBOUGHT >= cur:
        return "sell"
    return None


STRATEGIES = {"ma_cross": ma_cross_signal, "rsi": rsi_signal}


def get_signal_fn():
    try:
        return STRATEGIES[config.STRATEGY]
    except KeyError:
        raise SystemExit(
            f"config.STRATEGY must be one of {sorted(STRATEGIES)}, "
            f"got {config.STRATEGY!r}"
        )


def momentum_score(closes, lookbacks=(3, 6, 12), days_per_month=21):
    """Average of the trailing 3/6/12-month returns (cross-sectional
    momentum). Returns None if there is not enough history."""
    need = max(lookbacks) * days_per_month + 1
    if len(closes) < need:
        return None
    last = closes[-1]
    return sum(last / closes[-1 - k * days_per_month] - 1 for k in lookbacks) / len(lookbacks)


def rotation_targets(closes_by_symbol, top_n, lookbacks=(3, 6, 12), days_per_month=21):
    """Rank symbols by momentum_score; return the top_n with a positive
    score (an empty list means: sit in cash)."""
    scores = {}
    for sym, closes in closes_by_symbol.items():
        sc = momentum_score(closes, lookbacks, days_per_month)
        if sc is None:
            log.warning("Not enough history for %s; excluded this month.", sym)
        elif sc > 0:
            scores[sym] = sc
    return sorted(scores, key=scores.get, reverse=True)[:top_n]
