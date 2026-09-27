"""
IBKR (Interactive Brokers) client — same interface as questrade.Questrade,
so bot.py can switch brokers with config.BROKER.

Requires IB Gateway or TWS running locally with the API enabled
(Configure -> API -> Settings -> "Enable ActiveX and Socket Clients").
Ports: TWS paper 7497, IB Gateway paper 4002 (live: 7496 / 4001).

SAFETY LAYERS (in addition to config.DRY_RUN, which stops orders before
they ever reach this class):
  - place_order() refuses to send an order to a live IBKR account (ids
    start with "U") unless config.IBKR_ALLOW_LIVE_ORDERS is True.
    Paper-account ids start with "D".
  - Delayed market data is requested, so no paid data subscription is
    needed for the watch phase.
"""

import logging
import datetime as dt
from zoneinfo import ZoneInfo

from ib_async import IB, Stock, MarketOrder

import config
from questrade import OrderError

log = logging.getLogger("ibkr")

DELAYED_DATA = 3  # market data type: delayed (no subscription required)


class IBKR:
    def __init__(self, client_id=None):
        self.ib = IB()
        self.ib.connect(config.IBKR_HOST, config.IBKR_PORT,
                        clientId=client_id or config.IBKR_CLIENT_ID, timeout=20)
        self.ib.reqMarketDataType(DELAYED_DATA)
        accounts = self.ib.managedAccounts()
        kind = "PAPER" if accounts and accounts[0].startswith("D") else "LIVE"
        log.info("Connected to IBKR (port %s). Account(s): %s [%s]",
                 config.IBKR_PORT, accounts, kind)

    # ---- Accounts & market data --------------------------------------------

    def account_id(self):
        return self.ib.managedAccounts()[0]

    def symbol_id(self, name):
        """Returns the qualified contract — bot.py treats it as an opaque id."""
        qualified = self.ib.qualifyContracts(Stock(name, "SMART", "USD"))
        if not qualified:
            raise ValueError(f"Symbol {name} not found on IBKR")
        return qualified[0]

    def last_price(self, contract):
        ticker = self.ib.reqTickers(contract)[0]
        price = ticker.marketPrice()
        if price != price:  # NaN — no tick yet; fall back to last daily close
            closes = self.daily_closes(contract, 1)
            if not closes:
                raise ValueError(f"No price data for {contract.symbol}")
            price = closes[-1]
        return float(price)

    def daily_closes(self, contract, count):
        days = count * 2 + 10
        duration = f"{days} D" if days <= 365 else f"{-(-days // 365)} Y"
        bars = self.ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr=duration,
            barSizeSetting="1 day",
            whatToShow="TRADES",
            useRTH=True,
            formatDate=1,
        )
        return [float(b.close) for b in bars]

    def position_qty(self, account_id, contract):
        for p in self.ib.positions(account_id):
            if p.contract.conId == contract.conId:
                return float(p.position), float(p.avgCost)
        return 0.0, 0.0

    def market_open_now(self, market_name="NASDAQ", symbol=None):
        """True if the symbol (default config.SYMBOL) is in regular (liquid) hours.

        Uses the contract's own liquidHours from IBKR, so holidays and
        half-days are respected. Fails closed: any error means "closed".
        """
        try:
            contract = self.symbol_id(symbol or config.SYMBOL)
            details = self.ib.reqContractDetails(contract)[0]
            tz = ZoneInfo(details.timeZoneId)
            now = dt.datetime.now(tz)
            for session in details.liquidHours.split(";"):
                session = session.strip()
                if not session or session.endswith("CLOSED"):
                    continue
                start_s, end_s = session.split("-")
                start = dt.datetime.strptime(start_s, "%Y%m%d:%H%M").replace(tzinfo=tz)
                if ":" not in end_s:  # older format: end is HHMM on start's date
                    end_s = f"{start_s[:8]}:{end_s}"
                end = dt.datetime.strptime(end_s, "%Y%m%d:%H%M").replace(tzinfo=tz)
                if start <= now < end:
                    return True
            return False
        except Exception as e:
            log.warning("Market-hours check failed (%s); treating as closed.", e)
            return False

    # ---- Orders -------------------------------------------------------------

    def place_order(self, account_id, contract, qty, action):
        """Submit a Market/Day order. action = 'Buy' or 'Sell'.

        Raises OrderError on rejection; never retries (a blind retry could
        double-fire). Refuses live accounts unless explicitly allowed.
        """
        qty = int(qty)
        if qty < 1:
            raise OrderError(f"Quantity must be a positive integer, got {qty}")
        if account_id.startswith("U") and not config.IBKR_ALLOW_LIVE_ORDERS:
            raise OrderError(
                f"Refusing order: {account_id} is a LIVE IBKR account and "
                "config.IBKR_ALLOW_LIVE_ORDERS is False. Use the paper "
                "account (id starts with 'D'), or the owner flips that flag.")

        order = MarketOrder(action.upper(), qty)
        order.account = account_id
        trade = self.ib.placeOrder(contract, order)
        self.ib.sleep(2)  # let the initial status arrive
        status = trade.orderStatus.status
        log.info("Order status: %s. Log: %s", status,
                 [entry.message for entry in trade.log])
        if status in ("Cancelled", "Inactive", "ApiCancelled"):
            raise OrderError(
                f"Order {status}",
                response_body=[entry.message for entry in trade.log])
        return {"orderId": trade.order.orderId, "status": status}
