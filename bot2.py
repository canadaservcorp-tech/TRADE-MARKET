"""
Bot 2 — second, independent bot trading a different symbol (default: ETHA,
the spot-Ether ETF) with its own MA windows and risk caps, so the account
is not riding a single position. Separate process, log, state file and
kill switch; shares config.DRY_RUN / IBKR host+port / IBKR_ALLOW_LIVE_ORDERS
with bot.py. IBKR only (the Questrade client is not wired in here).

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
from strategies import ma_cross_signal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    handlers=[logging.FileHandler("bot2.log"), logging.StreamHandler()],
)
log = logging.getLogger("bot2")


def load_state():
    try:
        with open(config.BOT2_STATE_FILE) as f:
            state = json.load(f)
    except FileNotFoundError:
        state = {"trades_today": 0, "date": str(datetime.now().date())}
    state.setdefault("paper_cash", float(config.BOT2_POSITION_DOLLARS))
    state.setdefault("paper_shares", 0)
    state.setdefault("paper_entry", 0.0)
    return state


def save_state(s):
    with open(config.BOT2_STATE_FILE, "w") as f:
        json.dump(s, f)


def reset_daily(s):
    today = str(datetime.now().date())
    if s["date"] != today:
        s["date"], s["trades_today"] = today, 0
    return s


def kill_switch_active():
    return (os.path.exists(config.BOT2_KILL_SWITCH_FILE)
            or os.path.exists(config.KILL_SWITCH_FILE)
            or os.getenv("KILL_SWITCH") == "1")


def act(qt, acct, sym_id, side, qty, price, state, reason="strategy signal"):
    """All trades pass through here — respects DRY_RUN. Returns True on success.
    Never retried: a blind retry could double-fire."""
    qty = int(qty)
    sym = config.BOT2_SYMBOL
    if config.DRY_RUN:
        if side == "buy":
            state["paper_cash"] -= qty * price
            state["paper_shares"], state["paper_entry"] = qty, price
            pnl_txt = ""
        else:
            pnl = (price - state["paper_entry"]) * qty
            pnl_txt = f", P&L on this trade: ${pnl:+.2f}"
            state["paper_cash"] += qty * price
            state["paper_shares"], state["paper_entry"] = 0, 0.0
        balance = state["paper_cash"] + state["paper_shares"] * price
        log.info(f"[DRY_RUN] Would {side} {qty} {sym} @ ${price:.2f} ({reason}) "
                 f"— NO order sent. Paper balance: ${balance:.2f}{pnl_txt}")
        return True
    log.info(f"[LIVE] Sending {side} order for {qty} {sym} ({reason}).")
    try:
        result = qt.place_order(acct, sym_id, qty, side.capitalize())
    except OrderError as e:
        log.error(f"Order REJECTED ({e}). Full response: {e.response_body}. Not retrying.")
        return False
    log.info(f"Order accepted: {result}")
    return True


def main(once=False):
    if not config.BOT2_ENABLED:
        log.info("config.BOT2_ENABLED is False — bot2 disabled. Exiting.")
        return
    if config.BROKER != "ibkr":
        log.error("bot2 supports BROKER = 'ibkr' only. Exiting.")
        return
    mode = "DRY_RUN (no real orders)" if config.DRY_RUN else "LIVE — REAL MONEY"
    log.info(f"Starting bot2. Mode: {mode}. {config.BOT2_SYMBOL} "
             f"MA{config.BOT2_SHORT_WINDOW}/{config.BOT2_LONG_WINDOW}, "
             f"${config.BOT2_POSITION_DOLLARS} cap, stop {config.BOT2_STOP_LOSS_PCT:.0%}, "
             f"target {config.BOT2_TAKE_PROFIT_PCT:.0%}.")
    if not config.DRY_RUN:
        log.warning("LIVE MODE: real money is at risk.")

    state = load_state()
    qt = None

    while True:
        try:
            if qt is None:
                qt = IBKR(client_id=config.BOT2_CLIENT_ID)
            acct = qt.account_id()
            sym_id = qt.symbol_id(config.BOT2_SYMBOL)
            state = reset_daily(state)

            if config.DRY_RUN:
                qty_held, entry = state["paper_shares"], state["paper_entry"]
            else:
                qty_held, entry = qt.position_qty(acct, sym_id)

            # 0. Kill switch — flatten and stop
            if kill_switch_active():
                log.warning("KILL SWITCH detected. Flattening and stopping.")
                if qty_held > 0:
                    act(qt, acct, sym_id, "sell", qty_held, qt.last_price(sym_id),
                        state, reason="kill switch")
                save_state(state)
                return

            # 0.5 Market-hours guard
            if not qt.market_open_now(symbol=config.BOT2_SYMBOL):
                log.info("Market closed. No orders will be attempted.")
                if once:
                    return
                time.sleep(config.LOOP_SECONDS)
                continue

            price = qt.last_price(sym_id)

            # 1. Risk checks first
            acted = False
            if qty_held > 0 and entry > 0:
                change = (price - entry) / entry
                if change <= -config.BOT2_STOP_LOSS_PCT:
                    log.info(f"STOP-LOSS {change:.1%}. Selling.")
                    act(qt, acct, sym_id, "sell", qty_held, price, state,
                        reason=f"stop-loss ({change:.1%})")
                    acted = True
                elif change >= config.BOT2_TAKE_PROFIT_PCT:
                    log.info(f"TAKE-PROFIT {change:.1%}. Selling.")
                    act(qt, acct, sym_id, "sell", qty_held, price, state,
                        reason=f"take-profit ({change:.1%})")
                    acted = True

            # 2. Strategy signal
            if not acted:
                closes = qt.daily_closes(sym_id, config.BOT2_LONG_WINDOW)
                sig = ma_cross_signal(closes, config.BOT2_SHORT_WINDOW,
                                      config.BOT2_LONG_WINDOW)
                pos_txt = "flat" if qty_held == 0 else (
                    f"{int(qty_held)} sh @ ${entry:.2f} "
                    f"(unrealized ${(price - entry) * qty_held:+.2f})")
                log.info(f"{config.BOT2_SYMBOL} price=${price:.2f} signal={sig} position: {pos_txt}")

                if sig == "buy" and qty_held == 0:
                    if state["trades_today"] < config.BOT2_MAX_TRADES_PER_DAY:
                        qty = int(config.BOT2_POSITION_DOLLARS // price)
                        if qty >= 1:
                            if act(qt, acct, sym_id, "buy", qty, price, state):
                                state["trades_today"] += 1
                        else:
                            log.info(f"Can't afford 1 share at ${price:.2f} with a "
                                     f"${config.BOT2_POSITION_DOLLARS} cap. Skipping.")
                    else:
                        log.info("Daily trade cap reached.")
                elif sig == "sell" and qty_held > 0:
                    if act(qt, acct, sym_id, "sell", qty_held, price, state):
                        state["trades_today"] += 1
                else:
                    log.info(f"No action. Signal={sig}, holding={qty_held}.")

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
