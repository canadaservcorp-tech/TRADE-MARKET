"""
Questrade trading bot — DRY_RUN by default (logs trades, sends nothing).
Strategy: selectable via config.STRATEGY. Risk layer enforces stops + daily cap.
Orders are only attempted during regular market hours, and a kill switch
(KILL_SWITCH file or KILL_SWITCH=1 env var) flattens the position and exits.

Run:  python bot.py
Stop: Ctrl+C  (or create the kill-switch file)
"""

import os
import time
import json
import logging
from datetime import datetime

import config
from questrade import Questrade, OrderError
from strategies import get_signal_fn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()],
)
log = logging.getLogger("bot")


def load_state():
    try:
        with open("state.json") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"trades_today": 0, "date": str(datetime.now().date())}


def save_state(s):
    with open("state.json", "w") as f:
        json.dump(s, f)


def reset_daily(s):
    today = str(datetime.now().date())
    if s["date"] != today:
        s["date"], s["trades_today"] = today, 0
    return s


def kill_switch_active():
    return os.path.exists(config.KILL_SWITCH_FILE) or os.getenv("KILL_SWITCH") == "1"


def act(qt, acct, sym_id, side, qty):
    """Central place trades pass through — respects DRY_RUN.

    Returns True if the trade went through (or would have, in DRY_RUN).
    A rejected order is logged and NOT retried: Questrade can create an
    order and still return an error, so a blind retry could double-fire.
    """
    qty = int(qty)
    if config.DRY_RUN:
        log.info(f"[DRY_RUN] Would {side} {qty} shares — NO order sent.")
        return True
    log.info(f"[LIVE] Sending {side} order for {qty} shares.")
    try:
        result = qt.place_order(acct, sym_id, qty, side.capitalize())
    except OrderError as e:
        log.error(f"Order REJECTED ({e}). Full response: {e.response_body}. "
                  "Not retrying — will re-evaluate next loop.")
        return False
    log.info(f"Order accepted: {result}")
    return True


def main():
    mode = "DRY_RUN (no real orders)" if config.DRY_RUN else "LIVE — REAL MONEY"
    log.info(f"Starting. Mode: {mode}. Environment: {config.ENVIRONMENT}. "
             f"Symbol: {config.SYMBOL}. Strategy: {config.STRATEGY}.")
    if not config.DRY_RUN:
        log.warning("LIVE MODE: real money is at risk.")

    signal_fn = get_signal_fn()
    state = load_state()
    qt = None

    while True:
        try:
            if qt is None:
                qt = Questrade()  # token is cached; refreshes only near expiry
            acct = qt.account_id()
            sym_id = qt.symbol_id(config.SYMBOL)
            state = reset_daily(state)

            qty_held, entry = qt.position_qty(acct, sym_id)

            # 0. Kill switch — flatten and stop
            if kill_switch_active():
                log.warning("KILL SWITCH detected. Flattening and stopping.")
                if qty_held > 0:
                    act(qt, acct, sym_id, "sell", qty_held)
                save_state(state)
                return

            # 0.5 Market-hours guard — no orders outside regular hours
            if not qt.market_open_now("NASDAQ"):
                log.info("Market closed. No orders will be attempted.")
                time.sleep(config.LOOP_SECONDS)
                continue

            price = qt.last_price(sym_id)

            # 1. Risk checks first — can override strategy
            acted = False
            if qty_held > 0 and entry > 0:
                change = (price - entry) / entry
                if change <= -config.STOP_LOSS_PCT:
                    log.info(f"STOP-LOSS {change:.1%}. Selling.")
                    act(qt, acct, sym_id, "sell", qty_held)
                    acted = True
                elif change >= config.TAKE_PROFIT_PCT:
                    log.info(f"TAKE-PROFIT {change:.1%}. Selling.")
                    act(qt, acct, sym_id, "sell", qty_held)
                    acted = True

            # 2. Strategy signal
            if not acted:
                closes = qt.daily_closes(sym_id, config.LONG_WINDOW)
                sig = signal_fn(closes)
                if sig == "buy" and qty_held == 0:
                    if state["trades_today"] < config.MAX_TRADES_PER_DAY:
                        # Cash account: whole shares only, no fractional shares.
                        qty = int(config.MAX_POSITION_DOLLARS // price)
                        if qty >= 1:
                            if act(qt, acct, sym_id, "buy", qty):
                                state["trades_today"] += 1
                        else:
                            log.info(
                                f"Can't afford 1 share at ${price:.2f} with a "
                                f"${config.MAX_POSITION_DOLLARS} cap. Skipping. "
                                "(Pick a cheaper symbol or raise the cap.)")
                    else:
                        log.info("Daily trade cap reached.")
                elif sig == "sell" and qty_held > 0:
                    if act(qt, acct, sym_id, "sell", qty_held):
                        state["trades_today"] += 1
                else:
                    log.info(f"No action. Signal={sig}, holding={qty_held}.")

            save_state(state)

        except Exception as e:
            log.error(f"Loop error (bot keeps running): {e}")
            qt = None  # rebuild the client next loop in case auth went stale

        time.sleep(config.LOOP_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Stopped by user. Clean exit.")
