"""Trading signals. Selected via config.STRATEGY ("ma_cross" or "rsi").

Both strategies take a list of daily closes (oldest first) and return
'buy', 'sell', or None. The backtester reuses these same functions so a
backtest exercises exactly the logic the bot trades with.
"""

import logging

import pandas as pd

import config

log = logging.getLogger("strategies")


def ma_cross_signal(closes):
    """Moving-average crossover: fast MA crossing the slow MA."""
    if len(closes) < config.LONG_WINDOW + 1:
        log.warning("Not enough price history yet.")
        return None
    s = pd.Series(closes)
    short = s.rolling(config.SHORT_WINDOW).mean()
    long = s.rolling(config.LONG_WINDOW).mean()
    ps, pl = short.iloc[-2], long.iloc[-2]
    cs, cl = short.iloc[-1], long.iloc[-1]
    if ps <= pl and cs > cl:
        return "buy"
    if ps >= pl and cs < cl:
        return "sell"
    return None


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
