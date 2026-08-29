"""
Questrade API wrapper.

Questrade's OAuth is unusual: your refresh token is exchanged for a
short-lived access token AND a brand-new refresh token every time. We save
the new refresh token to token.json so the next run works. If a run fails
after the exchange, you may need a fresh token from the app hub again.

Endpoints verified against the official docs
(https://www.questrade.com/api/documentation) — see the VERIFICATION section
of README.md for exactly what was confirmed or changed.

  - Live token URL:      https://login.questrade.com/oauth2/token
  - Practice token URL:  https://practicelogin.questrade.com/oauth2/token
  - Token response: access_token, token_type ("Bearer"), expires_in (seconds),
    refresh_token (rotated every exchange), api_server.
"""

import os
import json
import time
import logging
import datetime as dt

import requests
from dotenv import load_dotenv

import config

load_dotenv()
log = logging.getLogger("questrade")

TOKEN_URLS = {
    "live": "https://login.questrade.com/oauth2/token",
    "practice": "https://practicelogin.questrade.com/oauth2/token",
}
TOKEN_FILE = "token.json"

# Refresh the access token when fewer than this many seconds remain.
TOKEN_REFRESH_MARGIN = 120


class OrderError(Exception):
    """An order attempt failed. `response_body` holds Questrade's full reply."""

    def __init__(self, message, status_code=None, response_body=None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


def _token_url():
    try:
        return TOKEN_URLS[config.ENVIRONMENT]
    except KeyError:
        raise SystemExit(
            f"config.ENVIRONMENT must be one of {sorted(TOKEN_URLS)}, "
            f"got {config.ENVIRONMENT!r}"
        )


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
    """API client. Caches the access token and refreshes it only near expiry."""

    def __init__(self):
        self.access_token = None
        self.api_server = None
        self.token_expires_at = 0.0
        self._ensure_token()

    # ---- Auth -------------------------------------------------------------

    def _ensure_token(self):
        """Refresh the access token only if missing or about to expire."""
        if self.access_token and time.time() < self.token_expires_at - TOKEN_REFRESH_MARGIN:
            return
        rt = _load_refresh_token()
        resp = requests.post(
            _token_url(),
            data={"grant_type": "refresh_token", "refresh_token": rt},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        _save_tokens(data)  # rotate immediately
        self.access_token = data["access_token"]
        # Normalize: docs samples show api_server with and without a trailing
        # slash (and one sample even includes "/v1").
        server = data["api_server"].rstrip("/")
        if server.endswith("/v1"):
            server = server[: -len("/v1")]
        self.api_server = server + "/"
        self.token_expires_at = time.time() + float(data.get("expires_in", 1800))
        log.info(
            "Authenticated with Questrade (%s). Token valid ~%d min.",
            config.ENVIRONMENT, int(data.get("expires_in", 1800)) // 60,
        )

    @property
    def headers(self):
        return {"Authorization": f"Bearer {self.access_token}"}

    def _get(self, path, params=None):
        self._ensure_token()
        r = requests.get(self.api_server + path, headers=self.headers,
                         params=params, timeout=30)
        r.raise_for_status()
        return r.json()

    # ---- Accounts & market data --------------------------------------------

    def account_id(self):
        accts = self._get("v1/accounts")["accounts"]
        return accts[0]["number"]  # first account

    def symbol_id(self, name):
        res = self._get("v1/symbols", params={"names": name})["symbols"]
        if not res:
            raise ValueError(f"Symbol {name} not found")
        return res[0]["symbolId"]

    def last_price(self, symbol_id):
        q = self._get(f"v1/markets/quotes/{symbol_id}")["quotes"][0]
        if q.get("delay"):
            log.warning("Quote is DELAYED (no real-time data package).")
        return q.get("lastTradePrice") or q.get("bidPrice")

    def daily_closes(self, symbol_id, count):
        # Docs require ISO-8601 datetimes with a timezone offset.
        end = dt.datetime.now(dt.timezone.utc)
        start = end - dt.timedelta(days=count * 2 + 10)  # buffer for weekends
        data = self._get(f"v1/markets/candles/{symbol_id}", params={
            "startTime": start.isoformat(timespec="seconds"),
            "endTime": end.isoformat(timespec="seconds"),
            "interval": "OneDay",
        })
        return [c["close"] for c in data["candles"]]

    def position_qty(self, account_id, symbol_id):
        positions = self._get(f"v1/accounts/{account_id}/positions")["positions"]
        for p in positions:
            if p["symbolId"] == symbol_id:
                return float(p["openQuantity"]), float(p.get("averageEntryPrice", 0))
        return 0.0, 0.0

    def market_open_now(self, market_name="NASDAQ"):
        """True if the given market is currently in regular trading hours.

        Uses GET v1/markets, which returns startTime/endTime (regular session)
        for the current trading date, and GET v1/time for the server clock.
        Fails closed: any error means "treat as closed".
        """
        try:
            now = dt.datetime.fromisoformat(self._get("v1/time")["time"])
            markets = self._get("v1/markets")["markets"]
            for m in markets:
                if m["name"] == market_name:
                    start = dt.datetime.fromisoformat(m["startTime"])
                    end = dt.datetime.fromisoformat(m["endTime"])
                    return start <= now < end
            log.warning("Market %s not found in v1/markets.", market_name)
        except Exception as e:
            log.warning("Market-hours check failed (%s); treating as closed.", e)
        return False

    # ---- Orders -------------------------------------------------------------

    def place_order(self, account_id, symbol_id, qty, action):
        """Submit a Market/Day order. action = 'Buy' or 'Sell'.

        Payload verified against POST v1/accounts/:id/orders in the official
        docs. Raises OrderError (with the full response body attached) on any
        rejection; never retries — Questrade can create an order and still
        return an error body, so a blind retry could double-fire.
        """
        qty = int(qty)
        if qty < 1:
            raise OrderError(f"Quantity must be a positive integer, got {qty}")
        payload = {
            "accountNumber": account_id,
            "symbolId": symbol_id,
            "quantity": qty,
            "orderType": "Market",
            "timeInForce": "Day",
            "action": action,
            "primaryRoute": "AUTO",
            "secondaryRoute": "AUTO",
        }
        self._ensure_token()
        try:
            r = requests.post(
                self.api_server + f"v1/accounts/{account_id}/orders",
                headers=self.headers, json=payload, timeout=30,
            )
        except requests.RequestException as e:
            # Ambiguous: the order may or may not have reached Questrade.
            raise OrderError(f"Network error during order submission: {e}")

        try:
            body = r.json()
        except ValueError:
            body = {"raw": r.text}
        log.info("Order response (HTTP %s): %s", r.status_code, body)

        # Questrade signals order-processing errors with `code` + `message`,
        # sometimes with HTTP 200 and an orderId (order created but rejected,
        # e.g. code 3054 "Order was rejected by the exchange").
        if r.status_code != 200 or "code" in body:
            raise OrderError(
                body.get("message", f"HTTP {r.status_code}"),
                status_code=r.status_code,
                response_body=body,
            )
        return body
