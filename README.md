# Questrade Trading Bot (MA crossover / RSI, DRY_RUN by default)

A personal automated trading bot for a Questrade self-directed cash account.
Every loop it pulls daily candles for one symbol, computes a signal
(moving-average crossover or RSI, selectable in `config.py`), applies a hard
risk layer (stop-loss, take-profit, position cap, daily trade cap), and either
logs the trade it *would* place (`DRY_RUN = True`, the default) or places it.

> **This is not financial advice.** The strategy here is a learning scaffold,
> not a proven earner. Simple MA-crossover/RSI systems frequently underperform
> buy-and-hold once fees, spreads, and slippage are included. Expect to lose
> money if you go live without doing your own validation.

## Safety model

- **`DRY_RUN = True` is the default and the primary safety layer.** The bot
  logs every would-be trade to `bot.log` and sends nothing. Only the owner
  flips it to `False`, after the go-live checklist below.
- **Secrets never enter the repo.** `.gitignore` excludes `.env` (your refresh
  token) and `token.json` (the rotated token cache). Before every push, run
  `git status` and confirm neither is listed.
- **Kill switch:** create a file named `KILL_SWITCH` next to `bot.py` (or set
  env var `KILL_SWITCH=1`). On its next loop the bot sells any open position
  (respecting DRY_RUN) and exits.

## Live vs. practice account

Questrade **does** offer a practice environment with its own login portal:
the API docs' token-exchange step lists both
`https://login.questrade.com/oauth2/token` (live) and
`https://practicelogin.questrade.com/oauth2/token` (practice).
Set `ENVIRONMENT = "practice"` in `config.py` to target it. Caveats:

- The two environments are completely separate. A **live refresh token will
  not work against the practice endpoint** (and vice versa) — you need a
  practice account and a refresh token generated from the practice portal.
- If you only have a live account/token, this bot connects to your **LIVE
  account**, and `DRY_RUN` is the only safety layer.

**Important scope caveat (verified in the official docs):** the `trade` OAuth
scope (POST orders) is listed as available to **partner developers only**.
Personal apps get read scopes (account data, market data) — the API Centre UI
shows scopes like "Retrieve balances, positions, orders and executions". This
means that even with `DRY_RUN = False`, `place_order()` may be refused with
HTTP 403 (error code 1016, "Request is out-of-allowed OAuth scopes"). Nothing
in this repo can bypass that; it is a Questrade account-level permission.

## Setup

```bash
cd TRADE-MARKET
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then paste the refresh token into .env
```

To get the refresh token: Questrade API Centre → your personal app →
New manual authorization → Generate new token → copy it into `.env` as
`QUESTRADE_REFRESH_TOKEN=...`. Tokens expire 7 days after generation if
unused; each exchange rotates the refresh token, and the bot saves the new
one to `token.json` automatically.

## Run

```bash
# validate the strategy first (no account needed — uses free yfinance data)
python backtest.py

# run the bot in DRY_RUN (logs trades, sends nothing)
python bot.py
```

Switch strategies by editing `config.py` (`STRATEGY = "ma_cross"` or
`"rsi"`) and re-running `backtest.py` to compare.

## Git

```bash
git status                          # confirm .env and token.json are NOT listed
git add <files>
git commit -m "your message"
git push
```

**Before every push:** run `git status` and confirm `.env` and `token.json`
are NOT listed. This repo is public — a leaked refresh token is immediately
exposed.

## Cash-account realities with ~$50

- **No fractional shares.** Order quantity is always a whole number of shares;
  the bot floors `MAX_POSITION_DOLLARS / price` to an integer.
- **"Can't afford one share"** is expected with a $40 cap (AAPL trades well
  above that). The bot logs and skips. Pick a cheaper symbol or raise the cap.
- **T+1 settlement:** when you sell in a cash account, proceeds settle the next
  business day. A rapid sell-then-buy can be rejected or flagged for
  insufficient settled funds. The bot does not model settlement; the daily
  trade cap limits the damage, but expect occasional rejections if it trades
  more than once a day.

## Go-live checklist (owner only — the bot ships with DRY_RUN = True)

1. **Backtest:** `python backtest.py` and confirm the strategy beats
   buy-and-hold — across a few symbols and settings, not just one lucky run.
   (As shipped, the 20/50 MA crossover on AAPL *underperforms* buy-and-hold.)
2. **Dry run for ~a week:** `python bot.py` with `DRY_RUN = True`; check
   `bot.log` daily and confirm the would-be trades are sensible (right symbol,
   sane quantities, no orders outside market hours, cap respected).
3. Endpoints verified against the Questrade docs (done — see VERIFICATION
   below), and you understand the partner-only trade-scope caveat above.
4. Only then, and only if you accept the risk: set `DRY_RUN = False` in
   `config.py`. Start with the smallest possible position. Remember the kill
   switch: `touch KILL_SWITCH`.

Not financial advice; past backtest performance does not predict live results.

## VERIFICATION — what was checked against the official Questrade API docs

Checked 2026-08-29 against https://www.questrade.com/api/documentation
(Getting started, Security, Error handling, Rate limiting, and the REST
operation pages for each call used).

### OAuth token exchange — confirmed, one correction
- **Confirmed:** live URL `https://login.questrade.com/oauth2/token` with
  `grant_type=refresh_token&refresh_token=...`. Docs show both GET-with-query
  and POST-form usage; the code now uses POST with form data (the documented
  request shape) so the token isn't in a URL/query string.
- **Confirmed:** response fields `access_token`, `token_type` ("Bearer"),
  `expires_in` (seconds; docs samples show 300 and 1800), `refresh_token`
  (rotated on every exchange), `api_server`.
- **Added:** practice endpoint `https://practicelogin.questrade.com/oauth2/token`
  (documented in Getting started), selectable via `config.ENVIRONMENT`.
- **Fixed:** docs samples show `api_server` inconsistently (with/without a
  trailing slash; one sample includes `/v1`). The code normalizes it before
  appending paths.
- **Added:** the access token and `expires_in` are now cached; the bot only
  re-exchanges the refresh token within 120 s of expiry instead of re-authing
  every loop.

### GET v1/symbols?names= — confirmed
`names` is a documented comma-separated symbol-name parameter (mutually
exclusive with `ids`); response is `symbols[]` with `symbolId`. Changed to
pass it via `requests` params for proper URL-encoding.

### GET v1/markets/quotes/:id — confirmed, one addition
Response is `quotes[]` with `lastTradePrice` / `bidPrice` as used. Docs warn
quotes are **delayed** without a real-time data package (`delay` field) — the
bot now logs a warning when the quote is delayed.

### GET v1/markets/candles/:id — confirmed, formatting fixed
`startTime`, `endTime`, `interval` are the documented params; `OneDay` is a
valid Historical Data Granularity value; candles expose `close`. Docs samples
use ISO-8601 datetimes **with a timezone offset** (e.g.
`2014-10-01T00:00:00-05:00`). The old code appended a literal `Z` to a naive
timestamp inside a hand-built query string; it now sends timezone-aware
ISO-8601 values through `requests` params (which also URL-encodes the `+`).
Note: the call returns at most 2,000 candles per request (fine here).

### POST v1/accounts/:id/orders — ORDER PAYLOAD confirmed field-by-field
Against the documented request parameters and sample request:
- `accountNumber` — in the docs' sample request body. Confirmed.
- `symbolId` (Integer) — confirmed.
- `quantity` (Integer) — confirmed; the code enforces `int` ≥ 1 (no
  fractional shares in a cash account).
- `orderType": "Market"` — "Market" is a documented Order Type enum value
  (Market, Limit, Stop, StopLimit, TrailStop*, LimitOnOpen, LimitOnClose).
  Note `limitPrice` is only required for limit-type orders; a Market order
  omits it, as this code does. Confirmed.
- `timeInForce": "Day"` — "Day" is a documented Order Time-In-Force enum
  value (Day, GoodTillCanceled, GoodTillExtendedDay, GoodTillDate,
  ImmediateOrCancel, FillOrKill). It appears in the docs' sample request
  body. Confirmed.
- `action": "Buy" | "Sell"` — exactly the two documented Order Action values.
  Confirmed.
- `primaryRoute": "AUTO"` / `secondaryRoute": "AUTO"` — both documented
  parameters; the docs' own sample uses `"AUTO"`/`"AUTO"`, and GET v1/markets
  lists `AUTO` among primary/secondary order routes. Confirmed. (`orderRoute`
  is a **response** field only, not a request field.)
- Success response: `orderId` + `orders[]` (with `state`, e.g. "Pending").
  Confirmed.

### Order error handling — rewritten per the docs' Error handling page
- General errors return `{code, message}` with a 4xx/5xx status.
- **Order-processing errors can return HTTP 200 with an error body** that
  includes `code`, `message`, and an `orderId` (e.g. code 3054 "Order was
  rejected by the exchange") — i.e. an order object was created even though
  it was rejected. `place_order()` therefore parses and logs the full response
  body, raises `OrderError` on any error (including embedded-in-200 errors),
  and the bot **never retries a failed submission** (a blind retry could
  double-fire); it re-evaluates fresh on the next loop instead.

### Market-hours guard — added, from documented endpoints
GET `v1/markets` returns `startTime`/`endTime` (regular session) for the
current trading date, and GET `v1/time` returns the server clock. The bot
checks these each loop and attempts no orders outside regular hours; the
check fails closed (any error = treat market as closed).

### Rate limits — documented, for reference
Account calls: 30/s, 30,000/h. Market data: 20/s, 15,000/h. Exceeding them
returns HTTP 429 (code 1006). At one loop per hour this bot is far below.

### Trade scope — honest finding
The docs' OAuth scope table marks `POST accounts/:id/orders` under
"Trade (**partner developers only**)". A personal app's token may therefore
be unable to place orders at all (403 / code 1016). Documented above; not
worked around.
