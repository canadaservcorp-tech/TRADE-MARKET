# Devin Handoff — Questrade Trading Bot

**Prepared for:** Hicham
**Goal:** A personal automated trading bot that runs a moving-average crossover
strategy on a Questrade self-directed cash account, with a hard risk layer and a
dry-run safety mode. This document contains the full spec, all working code, and a
clear list of what is already done vs. what still needs completing.

> **Owner context you must respect:**
> - This is a REAL-MONEY account (~$50 deposited). The bot ships with `DRY_RUN = True`
>   so it logs trades without sending them. Do NOT change that default. The owner flips
>   it to `False` themselves after testing.
> - Secrets (`.env`, `token.json`) must NEVER be committed. `.gitignore` handles this — verify it.
> - The owner's rule: code lives in GitHub, credentials live in a `.env` file on his machine,
>   never in chat and never in the repo.

---

## 1. What this bot does

- Authenticates to Questrade via OAuth refresh-token rotation.
- Every loop (default hourly): pulls daily price candles for one symbol, computes a
  short/long moving-average crossover, and decides buy / sell / hold.
- A risk layer runs FIRST each loop and can override the strategy: hard stop-loss,
  take-profit, position-size cap, and a max-trades-per-day circuit breaker.
- `DRY_RUN` mode logs the trade it *would* place instead of sending it.
- A separate backtester simulates the same logic on 5 years of free historical data
  (yfinance) so the strategy can be validated before going live.

## 2. Tech stack

- Python 3.10+
- `requests` (Questrade REST), `pandas`, `python-dotenv`, `yfinance` (backtest only)
- No framework. Plain scripts, run from the command line.

## 3. Repo layout (target)

```
trading-bot/
├── .gitignore
├── requirements.txt
├── .env.example
├── config.py
├── questrade.py      # auth + API wrapper
├── bot.py            # main trading loop
├── backtest.py       # historical simulation
└── README.md         # setup/run instructions (Devin to write — see task list)
```

---

## 4. STATUS — done vs. to-do

### Already written (below, complete and working as a first version)
- [x] `.gitignore`
- [x] `requirements.txt`
- [x] `.env.example`
- [x] `config.py`
- [x] `questrade.py`
- [x] `bot.py`
- [x] `backtest.py`

### Devin: please complete these

1. **VERIFY ALL QUESTRADE ENDPOINTS against the official API docs**
   (https://www.questrade.com/api/documentation). The code was written from
   prior knowledge and NOT verified against a live API. Confirm and correct:
   - Token exchange URL and the shape of the token response (`access_token`,
     `api_server`, `refresh_token`, `expires_in`).
   - `v1/symbols?names=`, `v1/markets/quotes/{id}`, `v1/markets/candles/{id}`.
   - The **order payload** in `questrade.place_order()` — field names, `orderType`,
     `timeInForce`, `action`, routes. This is the highest-risk part; a wrong field
     here means real orders fail or misbehave.

2. **Add a live-vs-practice account check.** Confirm whether Questrade offers a
   practice/demo account this token can target. If yes, make the target account
   selectable in `config.py`. If no, document clearly that this connects to the
   live account and that `DRY_RUN` is the only safety layer.

3. **Handle access-token expiry within a long run.** Currently the bot re-auths
   every loop (simple, works for hourly). If loop frequency increases, cache the
   access token and only refresh when `expires_in` is near. Add this properly.

4. **Robust error handling on orders.** On a real order, capture and log the full
   Questrade response, handle rejected orders (insufficient funds, market closed,
   invalid symbol) without crashing the loop, and never silently retry a rejected
   order in a way that could double-fire.

5. **Market-hours guard.** Don't attempt orders outside market hours. Add a check
   (Questrade returns market metadata; or gate on a simple ET schedule).

6. **Fractional/settlement reality for a $50 cash account.** Questrade cash accounts
   don't do fractional shares — confirm the quantity math handles "can't afford one
   share" gracefully (bot.py already logs and skips; verify against real prices).
   Note cash-account settlement (T+1) can block rapid re-buys — document behavior.

7. **Write `README.md`** with the exact setup + run + git steps (copy from section 6
   of this doc), plus a "how to go live" checklist.

8. **(Optional, recommended) Add a second strategy** behind a config switch so the
   owner can compare crossover vs. e.g. RSI in the backtester before committing.

9. **(Optional) A kill-switch.** A simple file or env flag that, when present,
   forces the bot to flatten positions and stop — useful insurance for an
   unattended real-money bot.

---

## 5. Full source (current version)

### `.gitignore`
```
# Secrets — NEVER commit these
.env
token.json

# Python
__pycache__/
*.pyc
.venv/
venv/

# Logs & state
logs/
*.log
state.json
```

### `requirements.txt`
```
requests==2.32.3
pandas==2.2.2
python-dotenv==1.0.1
yfinance==0.2.40
```

### `.env.example`
```
# Paste the refresh token from Questrade's "New device" step here.
# This file (.env) is gitignored so it never reaches GitHub.
QUESTRADE_REFRESH_TOKEN=paste_your_refresh_token_here
```

### `config.py`
```python
"""All settings in one place."""

# --- SAFETY: stays True until the owner has watched the bot and trusts it ---
# True  = logs the trade it WOULD place, sends nothing. Zero money at risk.
# False = places REAL orders with REAL money.
DRY_RUN = True

# --- Strategy: moving-average crossover ---
SYMBOL = "AAPL"        # one symbol to start
SHORT_WINDOW = 20      # fast MA (days)
LONG_WINDOW = 50       # slow MA (days)

# --- Risk controls ---
MAX_POSITION_DOLLARS = 40    # with a $50 deposit, keep this under the balance
STOP_LOSS_PCT = 0.05         # sell if down 5% from entry
TAKE_PROFIT_PCT = 0.10       # sell if up 10%
MAX_TRADES_PER_DAY = 3       # circuit breaker

# --- How often to check (seconds). 3600 = hourly. ---
LOOP_SECONDS = 3600
```

### `questrade.py`
```python
"""
Questrade API wrapper.

Questrade's OAuth is unusual: your refresh token is exchanged for a
short-lived access token AND a brand-new refresh token every time. We save
the new refresh token to token.json so the next run works. If a run fails
after the exchange, you may need a fresh token from the app hub again.

DEVIN: VERIFY THESE URLS against the official API docs before real trading:
  - Token URL:  https://login.questrade.com/oauth2/token
  - The token response gives you `api_server` — all other calls use that.
"""

import os
import json
import logging
import requests
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger("questrade")

TOKEN_URL = "https://login.questrade.com/oauth2/token"
TOKEN_FILE = "token.json"


def _load_refresh_token():
    # Prefer the rotated token saved from a previous run; fall back to .env
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE) as f:
            return json.load(f)["refresh_token"]
    rt = os.getenv("QUESTRADE_REFRESH_TOKEN")
    if not rt:
        raise SystemExit(
            "No refresh token. Put it in .env (QUESTRADE_REFRESH_TOKEN) "
            "or generate a new one in Questrade's app hub."
        )
    return rt


def _save_tokens(data):
    # Persist the NEW refresh token Questrade just handed back
    with open(TOKEN_FILE, "w") as f:
        json.dump({"refresh_token": data["refresh_token"]}, f)


class Questrade:
    def __init__(self):
        rt = _load_refresh_token()
        resp = requests.get(TOKEN_URL, params={
            "grant_type": "refresh_token",
            "refresh_token": rt,
        })
        resp.raise_for_status()
        data = resp.json()
        _save_tokens(data)  # rotate immediately
        self.access_token = data["access_token"]
        self.api_server = data["api_server"]  # e.g. https://api01.iq.questrade.com/
        self.headers = {"Authorization": f"Bearer {self.access_token}"}
        log.info("Authenticated with Questrade.")

    def _get(self, path):
        r = requests.get(self.api_server + path, headers=self.headers)
        r.raise_for_status()
        return r.json()

    def account_id(self):
        accts = self._get("v1/accounts")["accounts"]
        return accts[0]["number"]  # first account

    def symbol_id(self, name):
        res = self._get(f"v1/symbols?names={name}")["symbols"]
        if not res:
            raise ValueError(f"Symbol {name} not found")
        return res[0]["symbolId"]

    def last_price(self, symbol_id):
        q = self._get(f"v1/markets/quotes/{symbol_id}")["quotes"][0]
        return q.get("lastTradePrice") or q.get("bidPrice")

    def daily_closes(self, symbol_id, count):
        # Pull recent daily candles. Adjust the date math to your needs.
        import datetime as dt
        end = dt.datetime.utcnow()
        start = end - dt.timedelta(days=count * 2 + 10)  # buffer for weekends
        path = (f"v1/markets/candles/{symbol_id}"
                f"?startTime={start.isoformat()}Z"
                f"&endTime={end.isoformat()}Z&interval=OneDay")
        candles = self._get(path)["candles"]
        return [c["close"] for c in candles]

    def position_qty(self, account_id, symbol_id):
        positions = self._get(f"v1/accounts/{account_id}/positions")["positions"]
        for p in positions:
            if p["symbolId"] == symbol_id:
                return float(p["openQuantity"]), float(p.get("averageEntryPrice", 0))
        return 0.0, 0.0

    def place_order(self, account_id, symbol_id, qty, action):
        """action = 'Buy' or 'Sell'. DEVIN: VERIFY this payload against QT docs."""
        payload = {
            "accountNumber": account_id,
            "symbolId": symbol_id,
            "quantity": int(qty),
            "orderType": "Market",
            "timeInForce": "Day",
            "action": action,
            "primaryRoute": "AUTO",
            "secondaryRoute": "AUTO",
        }
        r = requests.post(
            self.api_server + f"v1/accounts/{account_id}/orders",
            headers=self.headers, json=payload,
        )
        r.raise_for_status()
        return r.json()
```

### `bot.py`
```python
"""
Questrade trading bot — DRY_RUN by default (logs trades, sends nothing).
Strategy: moving-average crossover. Risk layer enforces stops + daily cap.

Run:  python bot.py
Stop: Ctrl+C
"""

import time
import json
import logging
from datetime import datetime

import pandas as pd

import config
from questrade import Questrade

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


def signal_from_closes(closes):
    """Return 'buy', 'sell', or None from an MA crossover."""
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


def act(qt, acct, sym_id, side, qty):
    """Central place trades pass through — respects DRY_RUN."""
    if config.DRY_RUN:
        log.info(f"[DRY_RUN] Would {side} {qty} shares — NO order sent.")
        return
    log.info(f"[LIVE] Sending {side} order for {qty} shares.")
    result = qt.place_order(acct, sym_id, qty, side.capitalize())
    log.info(f"Order result: {result}")


def main():
    mode = "DRY_RUN (no real orders)" if config.DRY_RUN else "LIVE — REAL MONEY"
    log.info(f"Starting. Mode: {mode}. Symbol: {config.SYMBOL}")
    if not config.DRY_RUN:
        log.warning("LIVE MODE: real money is at risk.")

    state = load_state()

    while True:
        try:
            qt = Questrade()  # re-auth each loop; rotates token safely
            acct = qt.account_id()
            sym_id = qt.symbol_id(config.SYMBOL)
            state = reset_daily(state)

            qty_held, entry = qt.position_qty(acct, sym_id)
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
                sig = signal_from_closes(closes)
                if sig == "buy" and qty_held == 0:
                    if state["trades_today"] < config.MAX_TRADES_PER_DAY:
                        qty = int(config.MAX_POSITION_DOLLARS // price)
                        if qty >= 1:
                            act(qt, acct, sym_id, "buy", qty)
                            state["trades_today"] += 1
                        else:
                            log.info("Price too high for 1 share at cap.")
                    else:
                        log.info("Daily trade cap reached.")
                elif sig == "sell" and qty_held > 0:
                    act(qt, acct, sym_id, "sell", qty_held)
                    state["trades_today"] += 1
                else:
                    log.info(f"No action. Signal={sig}, holding={qty_held}.")

            save_state(state)

        except Exception as e:
            log.error(f"Loop error (bot keeps running): {e}")

        time.sleep(config.LOOP_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("Stopped by user. Clean exit.")
```

### `backtest.py`
```python
"""
Backtest the MA-crossover strategy on historical data.

Simulates the SAME logic as bot.py (crossover signal + stop-loss +
take-profit) against years of past prices, then reports how it did versus
simply buying and holding.

Run:  python backtest.py

HONESTY NOTE (read before trusting any number this prints):
  - Results IGNORE fees, slippage, and the bid/ask spread. Real results are worse.
  - A strategy tuned to look good on past data often fails live ("overfitting").
  - Past performance does not predict future performance.
"""

import yfinance as yf
import pandas as pd

import config

STARTING_CASH = 1000.0
YEARS = 5


def run_backtest():
    print(f"Downloading {YEARS}y of daily data for {config.SYMBOL}...")
    df = yf.download(config.SYMBOL, period=f"{YEARS}y",
                     interval="1d", auto_adjust=True, progress=False)
    if df.empty:
        print("No data returned. Check the symbol.")
        return

    close = df["Close"].squeeze()
    short_ma = close.rolling(config.SHORT_WINDOW).mean()
    long_ma = close.rolling(config.LONG_WINDOW).mean()

    cash = STARTING_CASH
    shares = 0
    entry_price = 0.0
    trades = []
    equity_curve = []

    for i in range(config.LONG_WINDOW + 1, len(close)):
        price = close.iloc[i]
        date = close.index[i].date()

        if shares > 0 and entry_price > 0:
            change = (price - entry_price) / entry_price
            if change <= -config.STOP_LOSS_PCT or change >= config.TAKE_PROFIT_PCT:
                cash += shares * price
                trades.append((date, "SELL (stop/target)", round(price, 2), shares))
                shares, entry_price = 0, 0.0
                equity_curve.append(cash)
                continue

        ps, pl = short_ma.iloc[i - 1], long_ma.iloc[i - 1]
        cs, cl = short_ma.iloc[i], long_ma.iloc[i]
        buy = ps <= pl and cs > cl
        sell = ps >= pl and cs < cl

        if buy and shares == 0:
            qty = int(config.MAX_POSITION_DOLLARS // price)
            if qty >= 1:
                cash -= qty * price
                shares, entry_price = qty, price
                trades.append((date, "BUY", round(price, 2), qty))
        elif sell and shares > 0:
            cash += shares * price
            trades.append((date, "SELL (signal)", round(price, 2), shares))
            shares, entry_price = 0, 0.0

        equity_curve.append(cash + shares * price)

    final_value = cash + shares * close.iloc[-1]
    strat_return = (final_value - STARTING_CASH) / STARTING_CASH
    buy_hold_return = (close.iloc[-1] - close.iloc[config.LONG_WINDOW + 1]) \
        / close.iloc[config.LONG_WINDOW + 1]

    eq = pd.Series(equity_curve)
    drawdown = (eq - eq.cummax()) / eq.cummax()
    max_drawdown = drawdown.min()

    sells = [t for t in trades if t[1].startswith("SELL")]
    wins, last_buy = 0, None
    for t in trades:
        if t[1] == "BUY":
            last_buy = t[2]
        elif t[1].startswith("SELL") and last_buy is not None:
            if t[2] > last_buy:
                wins += 1
            last_buy = None
    win_rate = (wins / len(sells)) if sells else 0

    print("\n" + "=" * 50)
    print(f"BACKTEST: {config.SYMBOL}  ({YEARS} years, daily)")
    print(f"Strategy: {config.SHORT_WINDOW}/{config.LONG_WINDOW} MA crossover")
    print("=" * 50)
    print(f"Starting cash:        ${STARTING_CASH:,.2f}")
    print(f"Ending value:         ${final_value:,.2f}")
    print(f"Strategy return:      {strat_return:+.1%}")
    print(f"Buy-and-hold return:  {buy_hold_return:+.1%}  <-- did the bot beat this?")
    print(f"Number of trades:     {len(trades)}")
    print(f"Win rate:             {win_rate:.0%}  (of {len(sells)} completed trades)")
    print(f"Max drawdown:         {max_drawdown:.1%}")
    print("=" * 50)

    if strat_return < buy_hold_return:
        print("\n>> The strategy UNDERPERFORMED buy-and-hold. Common — it added risk "
              "without adding return.")
    else:
        print("\n>> The strategy beat buy-and-hold in THIS period. Don't over-trust it: "
              "one period, no fees, overfitting risk.")

    print("\nLast 10 trades:")
    for t in trades[-10:]:
        print(f"  {t[0]}  {t[1]:20s}  {t[3]} @ ${t[2]}")


if __name__ == "__main__":
    run_backtest()
```

---

## 6. Setup / run / push (put this in README.md)

```bash
# setup
cd trading-bot
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then paste the refresh token into .env

# validate the strategy first (no account needed)
python backtest.py

# run the bot in DRY_RUN (logs trades, sends nothing)
python bot.py

# push to GitHub
git init
git add .
git commit -m "Questrade trading bot: MA crossover, dry-run default, backtester"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/trading-bot.git
git push -u origin main
```

**Before every push:** run `git status` and confirm `.env` and `token.json` are NOT listed.

### Go-live checklist (owner does this, not Devin)
1. `python backtest.py` beats buy-and-hold across a few symbols/settings.
2. `python bot.py` in DRY_RUN for at least a week; `bot.log` shows sensible trades.
3. Endpoints verified by Devin against Questrade docs.
4. Only then: set `DRY_RUN = False` in `config.py`.

## 7. Honest caveats to carry into the build
- The 20/50 MA crossover is a learning scaffold, not a proven money-maker. It
  frequently underperforms buy-and-hold, especially in choppy markets.
- Backtest results exclude fees/slippage and are prone to overfitting.
- A $50 cash account is enough to prove the plumbing works, not to make meaningful
  returns. Treat this as a test rig.
- This document is not financial advice.
