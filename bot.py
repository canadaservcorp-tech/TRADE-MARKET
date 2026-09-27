"""
Trading bot (Questrade or IBKR via config.BROKER) — DRY_RUN by default
(logs trades, sends nothing).
Strategy: selectable via config.STRATEGY. "ma_cross"/"rsi" trade config.SYMBOL
with the stop/target/daily-cap risk layer below; "rotation" hands the whole
loop to rotation.Rotation (monthly ETF momentum rotation over
config.ROTATION_UNIVERSE, capped at MAX_POSITION_DOLLARS).
Orders are only attempted during regular market hours, and a kill switch
(KILL_SWITCH file or KILL_SWITCH=1 env var) flattens the position and exits.

Run:  python bot.py           (loop every config.LOOP_SECONDS)
      python bot.py --once    (one pass, then exit — for a daily scheduled task)
Stop: Ctrl+C  (or create the kill-switch file)
"""

import os
import sys
import csv
import time
import json
import logging
from datetime import datetime

import config
from questrade import Questrade, OrderError
from ibkr import IBKR
from strategies import get_signal_fn, ma_snapshot
from rotation import Rotation

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()],
)
log = logging.getLogger("bot")


PAPER_CSV = "paper_trades.csv"


def load_state():
    try:
        with open("state.json") as f:
            state = json.load(f)
    except FileNotFoundError:
        state = {"trades_today": 0, "date": str(datetime.now().date())}
    # Paper-trading record for the DRY_RUN watch phase.
    state.setdefault("paper_cash", config.PAPER_STARTING_CASH)
    state.setdefault("paper_shares", 0)
    state.setdefault("paper_entry", 0.0)
    state.setdefault("last_csv_date", "")
    return state


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


def append_paper_csv(date, action, price, balance, reason):
    """One spreadsheet-friendly line per day/action: the paper-trading record.
    The reason column is the audit trail required by CHARTER.md."""
    new_file = not os.path.exists(PAPER_CSV)
    with open(PAPER_CSV, "a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["date", "symbol", "action", "price", "paper_balance", "reason"])
        w.writerow([date, config.SYMBOL, action, f"{price:.2f}", f"{balance:.2f}", reason])


def make_client():
    """Both clients expose the same interface (account_id, symbol_id,
    last_price, daily_closes, position_qty, market_open_now, place_order)."""
    if config.BROKER == "ibkr":
        return IBKR()
    if config.BROKER == "questrade":
        return Questrade()  # token is cached; refreshes only near expiry
    raise ValueError(f"Unknown config.BROKER: {config.BROKER!r}")


def act(qt, acct, sym_id, side, qty, price, state, reason="strategy signal"):
    """Central place trades pass through — respects DRY_RUN.

    Returns True if the trade went through (or would have, in DRY_RUN).
    A rejected order is logged and NOT retried: Questrade can create an
    order and still return an error, so a blind retry could double-fire.

    In DRY_RUN the hypothetical position is tracked in state (paper_cash /
    paper_shares / paper_entry) and each would-be trade is appended to
    paper_trades.csv, so the watch phase leaves a reviewable record.
    """
    qty = int(qty)
    if config.DRY_RUN:
        today = str(datetime.now().date())
        if side == "buy":
            state["paper_cash"] -= qty * price
            state["paper_shares"] = qty
            state["paper_entry"] = price
            pnl_txt = ""
        else:
            pnl = (price - state["paper_entry"]) * qty
            pnl_txt = f", P&L on this trade: ${pnl:+.2f}"
            state["paper_cash"] += qty * price
            state["paper_shares"] = 0
            state["paper_entry"] = 0.0
        balance = state["paper_cash"] + state["paper_shares"] * price
        log.info(f"[DRY_RUN] Would {side} {qty} {config.SYMBOL} @ ${price:.2f} "
                 f"— NO order sent. Paper balance: ${balance:.2f}{pnl_txt}")
        append_paper_csv(today, side.upper(), price, balance, reason)
        state["last_csv_date"] = today
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


def main(once=False):
    if config.STRATEGY == "rotation":
        Rotation(
            name="bot",
            universe=config.ROTATION_UNIVERSE,
            top_n=config.ROTATION_TOP_N,
            capital=config.MAX_POSITION_DOLLARS,
            client_id=config.IBKR_CLIENT_ID,
            state_file="rotation_state.json",
            kill_files=[config.KILL_SWITCH_FILE],
            log=log,
        ).run(once=once)
        return

    mode = "DRY_RUN (no real orders)" if config.DRY_RUN else "LIVE — REAL MONEY"
    log.info(f"Starting. Mode: {mode}. Broker: {config.BROKER}. "
             f"Environment: {config.ENVIRONMENT}. "
             f"Symbol: {config.SYMBOL}. Strategy: {config.STRATEGY}.")
    if not config.DRY_RUN:
        log.warning("LIVE MODE: real money is at risk.")

    signal_fn = get_signal_fn()
    state = load_state()
    qt = None

    while True:
        try:
            if qt is None:
                qt = make_client()
            acct = qt.account_id()
            sym_id = qt.symbol_id(config.SYMBOL)
            state = reset_daily(state)

            # In DRY_RUN the position is the hypothetical (paper) one — the
            # real account never trades, so sells/stops would never trigger
            # if we read the real (always-empty) position.
            if config.DRY_RUN:
                qty_held, entry = state["paper_shares"], state["paper_entry"]
            else:
                qty_held, entry = qt.position_qty(acct, sym_id)

            # 0. Kill switch — flatten and stop
            if kill_switch_active():
                log.warning("KILL SWITCH detected. Flattening and stopping.")
                if qty_held > 0:
                    act(qt, acct, sym_id, "sell", qty_held,
                        qt.last_price(sym_id), state, reason="kill switch")
                save_state(state)
                return

            # 0.5 Market-hours guard — no orders outside regular hours
            if not qt.market_open_now("NASDAQ"):
                log.info("Market closed. No orders will be attempted.")
                if once:
                    return
                time.sleep(config.LOOP_SECONDS)
                continue

            price = qt.last_price(sym_id)

            # 1. Risk checks first — can override strategy
            acted = False
            if qty_held > 0 and entry > 0:
                change = (price - entry) / entry
                if change <= -config.STOP_LOSS_PCT:
                    log.info(f"STOP-LOSS {change:.1%}. Selling.")
                    act(qt, acct, sym_id, "sell", qty_held, price, state,
                        reason=f"stop-loss ({change:.1%})")
                    acted = True
                elif change >= config.TAKE_PROFIT_PCT:
                    log.info(f"TAKE-PROFIT {change:.1%}. Selling.")
                    act(qt, acct, sym_id, "sell", qty_held, price, state,
                        reason=f"take-profit ({change:.1%})")
                    acted = True

            # 2. Strategy signal
            if not acted:
                closes = qt.daily_closes(sym_id, config.LONG_WINDOW)
                sig = signal_fn(closes)

                # Paper-trading record line: everything needed to review the
                # watch phase later at a glance.
                mas = ma_snapshot(closes) if config.STRATEGY == "ma_cross" else None
                ma_txt = (f" MA{config.SHORT_WINDOW}=${mas[0]:.2f}"
                          f" MA{config.LONG_WINDOW}=${mas[1]:.2f}" if mas else "")
                if qty_held > 0:
                    unrealized = (price - entry) * qty_held
                    pos_txt = (f"{int(qty_held)} sh @ ${entry:.2f} "
                               f"(unrealized ${unrealized:+.2f})")
                else:
                    pos_txt = "flat"
                paper_balance = state["paper_cash"] + state["paper_shares"] * price
                log.info(f"{config.SYMBOL} price=${price:.2f}{ma_txt} "
                         f"signal={sig} paper position: {pos_txt}, "
                         f"paper balance: ${paper_balance:.2f}")

                if sig == "buy" and qty_held == 0:
                    if state["trades_today"] < config.MAX_TRADES_PER_DAY:
                        # Cash account: whole shares only, no fractional shares.
                        qty = int(config.MAX_POSITION_DOLLARS // price)
                        if qty >= 1:
                            if act(qt, acct, sym_id, "buy", qty, price, state):
                                state["trades_today"] += 1
                        else:
                            log.info(
                                f"Can't afford 1 share at ${price:.2f} with a "
                                f"${config.MAX_POSITION_DOLLARS} cap. Skipping. "
                                "(Pick a cheaper symbol or raise the cap.)")
                    else:
                        log.info("Daily trade cap reached.")
                elif sig == "sell" and qty_held > 0:
                    if act(qt, acct, sym_id, "sell", qty_held, price, state):
                        state["trades_today"] += 1
                else:
                    log.info(f"No action. Signal={sig}, holding={qty_held}.")

            # 3. Daily one-line summary — HOLD row if nothing traded today.
            today = str(datetime.now().date())
            if config.DRY_RUN and state.get("last_csv_date") != today:
                balance = state["paper_cash"] + state["paper_shares"] * price
                append_paper_csv(today, "HOLD", price, balance,
                                 "no signal" if not acted else "risk exit today")
                state["last_csv_date"] = today

            save_state(state)

        except Exception as e:
            log.error(f"Loop error (bot keeps running): {e}")
            qt = None  # rebuild the client next loop in case auth went stale

        if once:
            return
        time.sleep(config.LOOP_SECONDS)


if __name__ == "__main__":
    try:
        main(once="--once" in sys.argv[1:])
    except KeyboardInterrupt:
        log.info("Stopped by user. Clean exit.")
