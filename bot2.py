"""
Bot 2 — monthly ETF momentum rotation (see rotation_backtest.py).

Once a month (first run of a new calendar month), rank config.BOT2_UNIVERSE
by the average of trailing 3/6/12-month returns and hold the top
config.BOT2_TOP_N with a positive score, equal dollars each; sell whatever
dropped out. If nothing has positive momentum the sleeve sits in cash.
Between rebalances the bot only logs. Separate process, log, state file and
kill switch from bot.py; shares DRY_RUN / IBKR host+port / live-order flag.
IBKR only.

Run:  python bot2.py           (loop every config.LOOP_SECONDS)
      python bot2.py --once    (one pass, then exit — scheduled task)
Stop: Ctrl+C, or create KILL_SWITCH_BOT2 (or KILL_SWITCH) in this folder.
"""

import os
import sys
import time
import json
import logging
from datetime import datetime

import config
from questrade import OrderError
from ibkr import IBKR
from strategies import rotation_targets, momentum_score

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    handlers=[logging.FileHandler("bot2.log"), logging.StreamHandler()],
)
log = logging.getLogger("bot2")

HISTORY_DAYS = 12 * 21 + 5  # enough daily closes for the 12-month lookback


def load_state():
    try:
        with open(config.BOT2_STATE_FILE) as f:
            state = json.load(f)
    except FileNotFoundError:
        state = {}
    state.setdefault("last_rebalance_month", "")
    state.setdefault("paper_cash", float(config.BOT2_CAPITAL_DOLLARS))
    state.setdefault("paper_positions", {})   # symbol -> shares
    return state


def save_state(s):
    with open(config.BOT2_STATE_FILE, "w") as f:
        json.dump(s, f)


def kill_switch_active():
    return (os.path.exists(config.BOT2_KILL_SWITCH_FILE)
            or os.path.exists(config.KILL_SWITCH_FILE)
            or os.getenv("KILL_SWITCH") == "1")


def act(qt, acct, sym, contract, side, qty, price, state, reason):
    """All trades pass through here — respects DRY_RUN. Returns True on success.
    Never retried: a blind retry could double-fire."""
    qty = int(qty)
    if qty < 1:
        return False
    if config.DRY_RUN:
        pos = state["paper_positions"]
        if side == "buy":
            state["paper_cash"] -= qty * price
            pos[sym] = pos.get(sym, 0) + qty
        else:
            state["paper_cash"] += qty * price
            pos.pop(sym, None)
        log.info(f"[DRY_RUN] Would {side} {qty} {sym} @ ${price:.2f} ({reason}) — NO order sent.")
        return True
    log.info(f"[LIVE] Sending {side} order for {qty} {sym} ({reason}).")
    try:
        result = qt.place_order(acct, contract, qty, side.capitalize())
    except OrderError as e:
        log.error(f"Order REJECTED ({e}). Full response: {e.response_body}. Not retrying.")
        return False
    log.info(f"Order accepted: {result}")
    return True


def holdings(qt, acct, contracts, state):
    """symbol -> shares for the universe (paper positions in DRY_RUN)."""
    if config.DRY_RUN:
        return {s: q for s, q in state["paper_positions"].items() if q > 0}
    held = {}
    for sym, c in contracts.items():
        q, _ = qt.position_qty(acct, c)
        if q > 0:
            held[sym] = int(q)
    return held


def rebalance(qt, acct, contracts, prices, closes, state):
    held = holdings(qt, acct, contracts, state)
    targets = rotation_targets(closes, config.BOT2_TOP_N)
    scores = {s: momentum_score(c) for s, c in closes.items()}
    log.info("Momentum scores: " + ", ".join(
        f"{s}={sc:+.1%}" for s, sc in sorted(scores.items(), key=lambda kv: -(kv[1] or -9))
        if sc is not None))
    log.info(f"Target: {targets or 'CASH'}  |  currently held: {held or 'nothing'}")

    for sym in [s for s in held if s not in targets]:
        act(qt, acct, sym, contracts[sym], "sell", held[sym], prices[sym], state,
            reason="dropped out of top ranks")

    new = [s for s in targets if s not in held]
    if new:
        if config.DRY_RUN:
            cash = state["paper_cash"]
        else:
            # Live: spend the sleeve minus what the kept positions are worth.
            kept = sum(held[s] * prices[s] for s in held if s in targets)
            cash = config.BOT2_CAPITAL_DOLLARS - kept
        for j, sym in enumerate(new):
            slice_ = cash / (len(new) - j)
            qty = int(slice_ // prices[sym])
            if qty >= 1:
                if act(qt, acct, sym, contracts[sym], "buy", qty, prices[sym], state,
                       reason=f"rank {targets.index(sym) + 1}"):
                    cash -= qty * prices[sym]
            else:
                log.info(f"Can't afford 1 share of {sym} at ${prices[sym]:.2f} with ${slice_:.2f}. Skipping.")


def main(once=False):
    if not config.BOT2_ENABLED:
        log.info("config.BOT2_ENABLED is False — bot2 disabled. Exiting.")
        return
    if config.BROKER != "ibkr":
        log.error("bot2 supports BROKER = 'ibkr' only. Exiting.")
        return
    mode = "DRY_RUN (no real orders)" if config.DRY_RUN else "LIVE — REAL MONEY"
    log.info(f"Starting bot2 (momentum rotation). Mode: {mode}. Universe {config.BOT2_UNIVERSE}, "
             f"top {config.BOT2_TOP_N}, ${config.BOT2_CAPITAL_DOLLARS} sleeve.")
    if not config.DRY_RUN:
        log.warning("LIVE MODE: real money is at risk.")

    state = load_state()
    qt = None

    while True:
        try:
            if qt is None:
                qt = IBKR(client_id=config.BOT2_CLIENT_ID)
            acct = qt.account_id()
            contracts = {s: qt.symbol_id(s) for s in config.BOT2_UNIVERSE}

            # 0. Kill switch — flatten everything and stop
            if kill_switch_active():
                log.warning("KILL SWITCH detected. Flattening and stopping.")
                for sym, q in holdings(qt, acct, contracts, state).items():
                    act(qt, acct, sym, contracts[sym], "sell", q,
                        qt.last_price(contracts[sym]), state, reason="kill switch")
                save_state(state)
                return

            # 0.5 Market-hours guard
            if not qt.market_open_now(symbol=config.BOT2_UNIVERSE[0]):
                log.info("Market closed. No orders will be attempted.")
                if once:
                    return
                time.sleep(config.LOOP_SECONDS)
                continue

            month = datetime.now().strftime("%Y-%m")
            if state["last_rebalance_month"] == month:
                held = holdings(qt, acct, contracts, state)
                log.info(f"Already rebalanced for {month}. Holding: {held or 'cash'}. No action.")
            else:
                prices = {s: qt.last_price(c) for s, c in contracts.items()}
                closes = {s: qt.daily_closes(c, HISTORY_DAYS) for s, c in contracts.items()}
                rebalance(qt, acct, contracts, prices, closes, state)
                state["last_rebalance_month"] = month
                if config.DRY_RUN:
                    value = state["paper_cash"] + sum(
                        q * prices[s] for s, q in state["paper_positions"].items())
                    log.info(f"Paper sleeve value: ${value:.2f}")

            save_state(state)

        except Exception as e:
            log.error(f"Loop error (bot keeps running): {e}")
            qt = None

        if once:
            return
        time.sleep(config.LOOP_SECONDS)


if __name__ == "__main__":
    try:
        main(once="--once" in sys.argv[1:])
    except KeyboardInterrupt:
        log.info("Stopped by user. Clean exit.")
