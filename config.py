"""All settings in one place."""

# --- SAFETY: stays True until the owner has watched the bot and trusts it ---
# True  = logs the trade it WOULD place, sends nothing. Zero money at risk.
# False = places REAL orders with REAL money.
DRY_RUN = True

# --- Environment: "live" or "practice" ---
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
