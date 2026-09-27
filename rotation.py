"""Monthly ETF momentum-rotation engine shared by bot.py (STRATEGY =
"rotation") and bot2.py. See rotation_backtest.py for the evidence.

Once a month (first run of a new calendar month, regular hours) rank the
universe by the average of trailing 3/6/12-month returns and hold the top N
with a positive score, equal dollars each; sell whatever dropped out. If
nothing has positive momentum the sleeve sits in cash. Other days: log only.
IBKR only (needs a year of daily history per symbol).
"""

import os
import json
import time
from datetime import datetime

import config
from questrade import OrderError
from ibkr import IBKR
from strategies import rotation_targets, momentum_score

HISTORY_DAYS = 12 * 21 + 5  # enough daily closes for the 12-month lookback


class Rotation:
    def __init__(self, name, universe, top_n, capital, client_id, state_file,
                 kill_files, log):
        self.name = name
        self.universe = list(universe)
        self.top_n = top_n
        self.capital = float(capital)
        self.client_id = client_id
        self.state_file = state_file
        self.kill_files = kill_files
        self.log = log

    # --- state -----------------------------------------------------------
    def load_state(self):
        try:
            with open(self.state_file) as f:
                state = json.load(f)
        except FileNotFoundError:
            state = {}
        state.setdefault("last_rebalance_month", "")
        state.setdefault("paper_cash", self.capital)
        state.setdefault("paper_positions", {})   # symbol -> shares
        return state

    def save_state(self, s):
        with open(self.state_file, "w") as f:
            json.dump(s, f)

    def kill_switch_active(self):
        return (any(os.path.exists(p) for p in self.kill_files)
                or os.getenv("KILL_SWITCH") == "1")

    # --- trading ---------------------------------------------------------
    def act(self, qt, acct, sym, contract, side, qty, price, state, reason):
        """All trades pass through here — respects DRY_RUN. Never retried."""
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
            self.log.info(f"[DRY_RUN] Would {side} {qty} {sym} @ ${price:.2f} ({reason}) — NO order sent.")
            return True
        self.log.info(f"[LIVE] Sending {side} order for {qty} {sym} ({reason}).")
        try:
            result = qt.place_order(acct, contract, qty, side.capitalize())
        except OrderError as e:
            self.log.error(f"Order REJECTED ({e}). Full response: {e.response_body}. Not retrying.")
            return False
        self.log.info(f"Order accepted: {result}")
        return True

    def holdings(self, qt, acct, contracts, state):
        """symbol -> shares for the universe (paper positions in DRY_RUN)."""
        if config.DRY_RUN:
            return {s: q for s, q in state["paper_positions"].items() if q > 0}
        held = {}
        for sym, c in contracts.items():
            q, _ = qt.position_qty(acct, c)
            if q > 0:
                held[sym] = int(q)
        return held

    def rebalance(self, qt, acct, contracts, prices, closes, state):
        held = self.holdings(qt, acct, contracts, state)
        targets = rotation_targets(closes, self.top_n)
        scores = {s: momentum_score(c) for s, c in closes.items()}
        self.log.info("Momentum scores: " + ", ".join(
            f"{s}={sc:+.1%}" for s, sc in sorted(scores.items(), key=lambda kv: -(kv[1] or -9))
            if sc is not None))
        self.log.info(f"Target: {targets or 'CASH'}  |  currently held: {held or 'nothing'}")

        for sym in [s for s in held if s not in targets]:
            self.act(qt, acct, sym, contracts[sym], "sell", held[sym], prices[sym], state,
                     reason="dropped out of top ranks")

        new = [s for s in targets if s not in held]
        if not new:
            return
        if config.DRY_RUN:
            cash = state["paper_cash"]
        else:
            kept = sum(held[s] * prices[s] for s in held if s in targets)
            cash = self.capital - kept
        for j, sym in enumerate(new):
            slice_ = cash / (len(new) - j)
            qty = int(slice_ // prices[sym])
            if qty >= 1:
                if self.act(qt, acct, sym, contracts[sym], "buy", qty, prices[sym], state,
                            reason=f"rank {targets.index(sym) + 1}"):
                    cash -= qty * prices[sym]
            else:
                self.log.info(f"Can't afford 1 share of {sym} at ${prices[sym]:.2f} "
                              f"with ${slice_:.2f}. Skipping. (Raise the cap.)")

    # --- main loop -------------------------------------------------------
    def run(self, once=False):
        if config.BROKER != "ibkr":
            self.log.error(f"{self.name}: rotation supports BROKER = 'ibkr' only. Exiting.")
            return
        mode = "DRY_RUN (no real orders)" if config.DRY_RUN else "LIVE — REAL MONEY"
        self.log.info(f"Starting {self.name} (momentum rotation). Mode: {mode}. "
                      f"Universe {self.universe}, top {self.top_n}, ${self.capital:.0f} sleeve.")
        if not config.DRY_RUN:
            self.log.warning("LIVE MODE: real money is at risk.")

        state = self.load_state()
        qt = None
        while True:
            try:
                if qt is None:
                    qt = IBKR(client_id=self.client_id)
                acct = qt.account_id()
                contracts = {s: qt.symbol_id(s) for s in self.universe}

                if self.kill_switch_active():
                    self.log.warning("KILL SWITCH detected. Flattening and stopping.")
                    for sym, q in self.holdings(qt, acct, contracts, state).items():
                        self.act(qt, acct, sym, contracts[sym], "sell", q,
                                 qt.last_price(contracts[sym]), state, reason="kill switch")
                    self.save_state(state)
                    return

                if not qt.market_open_now(symbol=self.universe[0]):
                    self.log.info("Market closed. No orders will be attempted.")
                    if once:
                        return
                    time.sleep(config.LOOP_SECONDS)
                    continue

                month = datetime.now().strftime("%Y-%m")
                if state["last_rebalance_month"] == month:
                    held = self.holdings(qt, acct, contracts, state)
                    self.log.info(f"Already rebalanced for {month}. Holding: {held or 'cash'}. No action.")
                else:
                    prices = {s: qt.last_price(c) for s, c in contracts.items()}
                    closes = {s: qt.daily_closes(c, HISTORY_DAYS) for s, c in contracts.items()}
                    self.rebalance(qt, acct, contracts, prices, closes, state)
                    state["last_rebalance_month"] = month
                    if config.DRY_RUN:
                        value = state["paper_cash"] + sum(
                            q * prices[s] for s, q in state["paper_positions"].items())
                        self.log.info(f"Paper sleeve value: ${value:.2f}")

                self.save_state(state)

            except Exception as e:
                self.log.error(f"Loop error (bot keeps running): {e}")
                qt = None

            if once:
                return
            time.sleep(config.LOOP_SECONDS)
