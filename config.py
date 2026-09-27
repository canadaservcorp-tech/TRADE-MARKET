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

# --- Strategy: "rotation" (monthly ETF momentum rotation), "ma_cross"
# (moving-average crossover on SYMBOL) or "rsi" (on SYMBOL) ---
STRATEGY = "rotation"

# rotation settings (only used when STRATEGY = "rotation"; engine: rotation.py)
# Each month hold the ROTATION_TOP_N ETFs with the best average 3/6/12-month
# return (only if positive; otherwise cash), spending up to
# MAX_POSITION_DOLLARS in total. Cheap share classes (all < $100) so whole
# shares fit a small sleeve: US large / US growth / US small / intl developed /
# emerging / long Treasuries / gold / energy / REITs. rotation_backtest.py
# (2012-2026, $1/order): beat buy-and-hold out-of-sample with about half the
# drawdown, lagged it in the 2012-20 US bull run; fees make it a plumbing
# test below ~$2,000. NOTE: at MAX_POSITION_DOLLARS = 40 it cannot afford a
# single share of most of these and will only log "Can't afford".
ROTATION_UNIVERSE = ["SCHX", "SCHG", "SCHA", "SCHF", "SCHE", "SPTL", "IAU", "XLE", "SCHH"]
ROTATION_TOP_N = 1     # 1 while the sleeve is small (least fee drag); 2-3 from ~$2,000

# ma_cross settings
SYMBOL = "F"           # ~$11/share: fits ~3 shares under the $40 cap (AAPL would never fill)
SHORT_WINDOW = 50      # fast MA (days)
LONG_WINDOW = 200      # slow MA (days)

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

# --- Bot 2 (bot2.py — a second rotation sleeve; own state/log/kill switch) ---
# Same engine as STRATEGY = "rotation" above. Only useful with a universe
# DISJOINT from ROTATION_UNIVERSE (each bot reads the account's real shares
# for its tickers, so a shared ticker would be double-counted; bot2.py refuses
# to start on overlap while bot.py is on "rotation"). US sector rotation
# (XLB/XLF/...) was tested as a candidate and failed (python
# rotation_backtest.py sectors): it trails SPY in every window. With bot.py
# already rotating, the simplest plan is to leave this off and put the
# capital into MAX_POSITION_DOLLARS instead.
BOT2_ENABLED = False             # master switch: False = bot2.py exits immediately
BOT2_UNIVERSE = list(ROTATION_UNIVERSE)   # placeholder — replace with a disjoint list before enabling
BOT2_TOP_N = 1                   # 1 at $250 (least fee drag); 2-3 once the sleeve is $2,000+
BOT2_CAPITAL_DOLLARS = 250       # total sleeve for bot 2, split equally across BOT2_TOP_N
BOT2_CLIENT_ID = 2               # must differ from IBKR_CLIENT_ID so both bots can connect
BOT2_STATE_FILE = "bot2_state.json"
BOT2_KILL_SWITCH_FILE = "KILL_SWITCH_BOT2"   # KILL_SWITCH (bot.py's) also stops it

# --- Kill switch ---
# If this file exists (or env var KILL_SWITCH=1), the bot sells any open
# position (respecting DRY_RUN) and exits. Create it with:  touch KILL_SWITCH
KILL_SWITCH_FILE = "KILL_SWITCH"

# --- How often to check (seconds). 3600 = hourly. ---
LOOP_SECONDS = 3600
