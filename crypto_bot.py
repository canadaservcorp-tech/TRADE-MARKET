"""
Crypto bot — second, independent bot trading one spot crypto pair on IBKR's
PAXOS venue (24/7), MA crossover on daily bars. Separate process, separate
state file, separate kill switch; shares config.DRY_RUN / IBKR host+port /
IBKR_ALLOW_LIVE_ORDERS with bot.py.

Run:  python crypto_bot.py           (loop every config.LOOP_SECONDS)
      python crypto_bot.py --once    (one pass, then exit — scheduled task)
Stop: Ctrl+C, or create KILL_SWITCH_CRYPTO (or KILL_SWITCH) in this folder.

IBKR crypto notes:
  - Requires crypto trading permission on the account (Account Settings ->
    Trading Permissions -> Cryptocurrencies) and is not available in every
    region/account type. If the contract cannot be qualified, that is why.
  - Buys are sent as a cash-quantity market order (spend $X, receive
    fractional coins); sells are sent for the exact quantity held.
  - Market orders on PAXOS are submitted IOC (fill immediately or cancel).
"""

import os
import sys
import time
import json
import logging
from datetime import datetime

from ib_async import IB, Crypto, MarketOrder

import config
from questrade import OrderError
from strategies import ma_cross_signal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    handlers=[logging.FileHandler("crypto_bot.log"), logging.StreamHandler()],
)
log = logging.getLogger("crypto_bot")

DELAYED_DATA = 3
DUST = 1e-6  # positions below this are treated as flat


def load_state():
    try:
        with open(config.CRYPTO_STATE_FILE) as f:
            state = json.load(f)
    except FileNotFoundError:
        state = {"trades_today": 0, "date": str(datetime.now().date())}
    state.setdefault("paper_cash", float(config.CRYPTO_POSITION_DOLLARS))
    state.setdefault("paper_qty", 0.0)
    state.setdefault("paper_entry", 0.0)
    return state


def save_state(s):
    with open(config.CRYPTO_STATE_FILE, "w") as f:
        json.dump(s, f)


def reset_daily(s):
    today = str(datetime.now().date())
    if s["date"] != today:
        s["date"], s["trades_today"] = today, 0
    return s


def kill_switch_active():
    return (os.path.exists(config.CRYPTO_KILL_SWITCH_FILE)
            or os.path.exists(config.KILL_SWITCH_FILE)
            or os.getenv("KILL_SWITCH") == "1")


class CryptoClient:
    def __init__(self):
        self.ib = IB()
        self.ib.connect(config.IBKR_HOST, config.IBKR_PORT,
                        clientId=config.CRYPTO_CLIENT_ID, timeout=20)
        self.ib.reqMarketDataType(DELAYED_DATA)
        accounts = self.ib.managedAccounts()
        kind = "PAPER" if accounts and accounts[0].startswith("D") else "LIVE"
        log.info("Connected to IBKR (port %s, clientId %s). Account(s): %s [%s]",
                 config.IBKR_PORT, config.CRYPTO_CLIENT_ID, accounts, kind)
        self.account = accounts[0]
        qualified = self.ib.qualifyContracts(Crypto(config.CRYPTO_SYMBOL, "PAXOS", "USD"))
        if not qualified:
            raise ValueError(
                f"{config.CRYPTO_SYMBOL}/USD on PAXOS could not be qualified — "
                "does this account have crypto trading permission?")
        self.contract = qualified[0]

    def last_price(self):
        ticker = self.ib.reqTickers(self.contract)[0]
        price = ticker.marketPrice()
        if price != price:  # NaN
            closes = self.daily_closes(1)
            if not closes:
                raise ValueError("No price data for crypto contract")
            price = closes[-1]
        return float(price)

    def daily_closes(self, count):
        bars = self.ib.reqHistoricalData(
            self.contract, endDateTime="", durationStr=f"{count + 10} D",
            barSizeSetting="1 day", whatToShow="AGGTRADES", useRTH=False,
            formatDate=1)
        return [float(b.close) for b in bars]

    def position(self):
        for p in self.ib.positions(self.account):
            if p.contract.conId == self.contract.conId:
                return float(p.position), float(p.avgCost)
        return 0.0, 0.0

    def place_order(self, action, qty=None, cash=None):
        """Market IOC order. BUY by cash amount, SELL by quantity.
        Never retried: a blind retry could double-fire."""
        if self.account.startswith("U") and not config.IBKR_ALLOW_LIVE_ORDERS:
            raise OrderError(
                f"Refusing order: {self.account} is a LIVE IBKR account and "
                "config.IBKR_ALLOW_LIVE_ORDERS is False.")
        if action == "BUY":
            order = MarketOrder("BUY", 0)
            order.cashQty = round(float(cash), 2)
        else:
            order = MarketOrder("SELL", round(float(qty), 8))
        order.tif = "IOC"
        order.account = self.account
        trade = self.ib.placeOrder(self.contract, order)
        self.ib.sleep(3)
        status = trade.orderStatus.status
        msgs = [entry.message for entry in trade.log]
        log.info("Order status: %s. Log: %s", status, msgs)
        if status in ("Cancelled", "Inactive", "ApiCancelled"):
            raise OrderError(f"Order {status}", response_body=msgs)
        return {"orderId": trade.order.orderId, "status": status}


def act(client, side, price, state, qty=None, cash=None, reason="strategy signal"):
    """All trades pass through here — respects DRY_RUN. Returns True on success."""
    sym = config.CRYPTO_SYMBOL
    if config.DRY_RUN:
        if side == "buy":
            qty = cash / price
            state["paper_cash"] -= cash
            state["paper_qty"], state["paper_entry"] = qty, price
            pnl_txt = ""
        else:
            pnl = (price - state["paper_entry"]) * qty
            pnl_txt = f", P&L on this trade: ${pnl:+.2f}"
            state["paper_cash"] += qty * price
            state["paper_qty"], state["paper_entry"] = 0.0, 0.0
        balance = state["paper_cash"] + state["paper_qty"] * price
        log.info(f"[DRY_RUN] Would {side} {qty:.6f} {sym} @ ${price:.2f} ({reason}) "
                 f"— NO order sent. Paper balance: ${balance:.2f}{pnl_txt}")
        return True
    what = f"${cash:.2f} of {sym}" if side == "buy" else f"{qty:.6f} {sym}"
    log.info(f"[LIVE] Sending {side} order for {what} ({reason}).")
    try:
        result = client.place_order(side.upper(), qty=qty, cash=cash)
    except OrderError as e:
        log.error(f"Order REJECTED ({e}). Full response: {e.response_body}. Not retrying.")
        return False
    log.info(f"Order accepted: {result}")
    return True


def main(once=False):
    if not config.CRYPTO_ENABLED:
        log.info("config.CRYPTO_ENABLED is False — crypto bot disabled. Exiting.")
        return
    mode = "DRY_RUN (no real orders)" if config.DRY_RUN else "LIVE — REAL MONEY"
    log.info(f"Starting crypto bot. Mode: {mode}. {config.CRYPTO_SYMBOL}/USD "
             f"MA{config.CRYPTO_SHORT_WINDOW}/{config.CRYPTO_LONG_WINDOW}, "
             f"${config.CRYPTO_POSITION_DOLLARS} ticket, stop {config.CRYPTO_STOP_LOSS_PCT:.0%}, "
             f"target {config.CRYPTO_TAKE_PROFIT_PCT:.0%}.")
    if not config.DRY_RUN:
        log.warning("LIVE MODE: real money is at risk.")

    state = load_state()
    client = None

    while True:
        try:
            if client is None:
                client = CryptoClient()
            state = reset_daily(state)

            if config.DRY_RUN:
                qty_held, entry = state["paper_qty"], state["paper_entry"]
            else:
                qty_held, entry = client.position()
            flat = qty_held < DUST

            # 0. Kill switch — flatten and stop
            if kill_switch_active():
                log.warning("KILL SWITCH detected. Flattening and stopping.")
                if not flat:
                    act(client, "sell", client.last_price(), state,
                        qty=qty_held, reason="kill switch")
                save_state(state)
                return

            price = client.last_price()

            # 1. Risk checks first
            acted = False
            if not flat and entry > 0:
                change = (price - entry) / entry
                if change <= -config.CRYPTO_STOP_LOSS_PCT:
                    log.info(f"STOP-LOSS {change:.1%}. Selling.")
                    act(client, "sell", price, state, qty=qty_held,
                        reason=f"stop-loss ({change:.1%})")
                    acted = True
                elif change >= config.CRYPTO_TAKE_PROFIT_PCT:
                    log.info(f"TAKE-PROFIT {change:.1%}. Selling.")
                    act(client, "sell", price, state, qty=qty_held,
                        reason=f"take-profit ({change:.1%})")
                    acted = True

            # 2. Strategy signal
            if not acted:
                closes = client.daily_closes(config.CRYPTO_LONG_WINDOW)
                sig = ma_cross_signal(closes, config.CRYPTO_SHORT_WINDOW,
                                      config.CRYPTO_LONG_WINDOW)
                pos_txt = "flat" if flat else (
                    f"{qty_held:.6f} @ ${entry:.2f} (unrealized ${(price - entry) * qty_held:+.2f})")
                log.info(f"{config.CRYPTO_SYMBOL} price=${price:.2f} signal={sig} position: {pos_txt}")

                if sig == "buy" and flat:
                    if state["trades_today"] < config.CRYPTO_MAX_TRADES_PER_DAY:
                        if act(client, "buy", price, state, cash=float(config.CRYPTO_POSITION_DOLLARS)):
                            state["trades_today"] += 1
                    else:
                        log.info("Daily trade cap reached.")
                elif sig == "sell" and not flat:
                    if act(client, "sell", price, state, qty=qty_held):
                        state["trades_today"] += 1
                else:
                    log.info(f"No action. Signal={sig}, holding={qty_held:.6f}.")

            save_state(state)

        except Exception as e:
            log.error(f"Loop error (bot keeps running): {e}")
            client = None

        if once:
            return
        time.sleep(config.LOOP_SECONDS)


if __name__ == "__main__":
    try:
        main(once="--once" in sys.argv[1:])
    except KeyboardInterrupt:
        log.info("Stopped by user. Clean exit.")
