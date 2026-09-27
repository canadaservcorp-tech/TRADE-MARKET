"""All settings in one place."""

# --- SAFETY: stays True until the owner has watched the bot and trusts it ---
# True  = logs the trade it WOULD place, sends nothing. Zero money at risk.
# False = places REAL orders with REAL money.
DRY_RUN = False

# --- Broker: "questrade" or "ibkr" ---
# "ibkr" needs IB Gateway or TWS running locally with the API enabled
# (see README "IBKR setup"). Point IBKR_PORT at the PAPER port first.
BROKER = "ibkr"

# IBKR connection settings (only used when BROKER = "ibkr").
# Ports — TWS: paper 7497 / live 7496.  IB Gateway: paper 4002 / live 4001.
IBKR_HOST = "127.0.0.1"
IBKR_PORT = 7496
IBKR_CLIENT_ID = 1
# Belt-and-braces: even if DRY_RUN were ever flipped, orders addressed to a
# LIVE IBKR account (id starts with "U") are refused unless this is True.
# Paper accounts (id starts with "D") are unaffected.
IBKR_ALLOW_LIVE_ORDERS = True

# --- Environment: "live" or "practice" (Questrade only) ---
# Questrade offers a free practice account with its own login portal
# (practicelogin.questrade.com). A practice refresh token only works with
# ENVIRONMENT = "practice", and a live token only with "live" — the two
# environments are completely separate (separate signup, separate tokens).
# Generate the practice refresh token from the practice API centre.
ENVIRONMENT = "live"

# --- Strategy: "ma_cross" (moving-average crossover) or "rsi" ---
STRATEGY = "ma_cross"

# ma_cross settings
SYMBOL = "AAPL"        # one symbol to start
SHORT_WINDOW = 20      # fast MA (days)
LONG_WINDOW = 50       # slow MA (days)

# rsi settings (only used when STRATEGY = "rsi")
RSI_PERIOD = 14
RSI_OVERSOLD = 30      # buy when RSI crosses up through this
RSI_OVERBOUGHT = 70    # sell when RSI crosses down through this

# --- Backtest grid (python backtest.py --grid) ---
# Symbols and (short, long) MA windows to sweep. Cheap symbols matter:
# with MAX_POSITION_DOLLARS = 40, one share of an expensive stock is
# unaffordable and every buy signal gets skipped.
BACKTEST_SYMBOLS = ["AAPL", "F", "SOFI"]
BACKTEST_MA_GRID = [(10, 30), (20, 50), (50, 200)]

# --- Paper-trading record (DRY_RUN watch phase) ---
# Hypothetical starting balance used for the paper position tracked in
# state.json and the running balance written to paper_trades.csv.
PAPER_STARTING_CASH = 50.0

# --- Risk controls ---
MAX_POSITION_DOLLARS = 40    # with a $50 deposit, keep this under the balance
STOP_LOSS_PCT = 0.05         # sell if down 5% from entry
TAKE_PROFIT_PCT = 0.10       # sell if up 10%
MAX_TRADES_PER_DAY = 3       # circuit breaker

# --- Kill switch ---
# If this file exists (or env var KILL_SWITCH=1), the bot sells any open
# position (respecting DRY_RUN) and exits. Create it with:  touch KILL_SWITCH
KILL_SWITCH_FILE = "KILL_SWITCH"

# --- How often to check (seconds). 3600 = hourly. ---
LOOP_SECONDS = 3600
