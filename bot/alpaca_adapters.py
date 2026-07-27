"""Lazy Alpaca SDK adapters. Importing core strategy code never requires alpaca-py."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Sequence
from zoneinfo import ZoneInfo

from bot.broker import BrokerAccount, BrokerOrder, BrokerPosition
from bot.config import Settings, validate_settings
from bot.historical_data import TradeTick, aggregate_trades_to_bars
from bot.models import Bar, MarketSnapshot, Quote, TradePlan


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value))


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


class AlpacaPaperBroker:
    def __init__(self, settings: Settings):
        validate_settings(settings, require_alpaca_keys=True)
        if not settings.alpaca_paper or settings.allow_live_trading:
            raise ValueError("AlpacaPaperBroker requires paper-only settings.")
        try:
            from alpaca.trading.client import TradingClient
        except ModuleNotFoundError as exc:
            raise RuntimeError("Install requirements.txt to use Alpaca commands.") from exc
        self.settings = settings
        self.client = TradingClient(
            settings.alpaca_api_key, settings.alpaca_secret_key, paper=True
        )

    def get_account(self) -> BrokerAccount:
        value = self.client.get_account()
        return BrokerAccount(
            account_id=str(value.id),
            status=_enum_value(value.status),
            equity=_decimal(value.equity),
            cash=_decimal(value.cash),
            buying_power=_decimal(value.buying_power),
            trading_blocked=bool(value.trading_blocked),
        )

    def get_open_orders(self) -> Sequence[BrokerOrder]:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        values = self.client.get_orders(
            filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, nested=True)
        )
        return [self._map_order(value) for value in values]

    def get_recent_orders(self) -> Sequence[BrokerOrder]:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        values = self.client.get_orders(
            filter=GetOrdersRequest(status=QueryOrderStatus.ALL, limit=100, nested=True)
        )
        return [self._map_order(value) for value in values]

    def get_positions(self) -> Sequence[BrokerPosition]:
        return [
            BrokerPosition(
                symbol=value.symbol,
                quantity=int(Decimal(str(value.qty))),
                average_entry_price=_decimal(value.avg_entry_price),
                current_price=_decimal(value.current_price),
            )
            for value in self.client.get_all_positions()
        ]

    def submit_bracket_entry(
        self,
        *,
        trade_id: str,
        client_order_id: str,
        plan: TradePlan,
        quantity: int,
    ) -> BrokerOrder:
        from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
        from alpaca.trading.requests import (
            LimitOrderRequest,
            StopLossRequest,
            TakeProfitRequest,
        )

        request = LimitOrderRequest(
            symbol=plan.symbol,
            qty=quantity,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
            limit_price=float(plan.maximum_entry_price),
            order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=float(plan.target_price)),
            stop_loss=StopLossRequest(stop_price=float(plan.stop_price)),
            client_order_id=client_order_id,
            extended_hours=False,
        )
        return self._map_order(self.client.submit_order(order_data=request))

    def cancel_order(self, broker_order_id: str) -> None:
        self.client.cancel_order_by_id(broker_order_id)

    def replace_order(
        self,
        broker_order_id: str,
        *,
        limit_price: Decimal | None = None,
        stop_price: Decimal | None = None,
    ) -> BrokerOrder:
        from alpaca.trading.requests import ReplaceOrderRequest

        request = ReplaceOrderRequest(
            limit_price=float(limit_price) if limit_price is not None else None,
            stop_price=float(stop_price) if stop_price is not None else None,
        )
        return self._map_order(
            self.client.replace_order_by_id(broker_order_id, order_data=request)
        )

    def submit_oco_exit(
        self,
        *,
        trade_id: str,
        symbol: str,
        quantity: int,
        target_price: Decimal,
        stop_price: Decimal,
    ) -> BrokerOrder:
        from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
        from alpaca.trading.requests import (
            LimitOrderRequest,
            StopLossRequest,
            TakeProfitRequest,
        )

        request = LimitOrderRequest(
            symbol=symbol,
            qty=quantity,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
            limit_price=float(target_price),
            order_class=OrderClass.OCO,
            take_profit=TakeProfitRequest(limit_price=float(target_price)),
            stop_loss=StopLossRequest(stop_price=float(stop_price)),
            client_order_id=f"mvp-{trade_id[:12]}-protect",
            extended_hours=False,
        )
        return self._map_order(self.client.submit_order(order_data=request))

    def close_position(self, symbol: str) -> None:
        self.client.close_position(symbol)

    def cancel_orders_for_symbol(self, symbol: str) -> None:
        for order in self.get_open_orders():
            if order.symbol == symbol:
                self.cancel_order(order.broker_order_id)

    def market_is_open(self) -> bool:
        return bool(self.client.get_clock().is_open)

    @staticmethod
    def _map_order(value: Any) -> BrokerOrder:
        raw_legs = getattr(value, "legs", None) or []
        legs = tuple(str(leg.id) for leg in raw_legs)
        take_profit_order_id = None
        stop_loss_order_id = None
        exit_fill_price = None
        exit_fill_quantity = 0
        exit_order_id = None
        exit_filled_at = None
        for leg in raw_legs:
            order_type = _enum_value(getattr(leg, "type", None))
            if order_type == "limit":
                take_profit_order_id = str(leg.id)
            elif order_type in {"stop", "stop_limit"}:
                stop_loss_order_id = str(leg.id)
            if (
                _enum_value(getattr(leg, "side", "")) == "sell"
                and Decimal(str(getattr(leg, "filled_qty", 0) or 0)) > 0
                and getattr(leg, "filled_avg_price", None) is not None
            ):
                exit_fill_price = _decimal(leg.filled_avg_price)
                exit_fill_quantity = int(Decimal(str(leg.filled_qty)))
                exit_order_id = str(leg.id)
                exit_filled_at = getattr(leg, "filled_at", None)
        submitted = value.submitted_at or value.created_at or datetime.now(timezone.utc)
        side = _enum_value(getattr(value, "side", ""))
        if (
            side == "sell"
            and Decimal(str(getattr(value, "filled_qty", 0) or 0)) > 0
            and getattr(value, "filled_avg_price", None) is not None
        ):
            exit_fill_price = _decimal(value.filled_avg_price)
            exit_fill_quantity = int(Decimal(str(value.filled_qty)))
            exit_order_id = str(value.id)
            exit_filled_at = getattr(value, "filled_at", None)
        return BrokerOrder(
            broker_order_id=str(value.id),
            client_order_id=str(value.client_order_id),
            symbol=str(value.symbol),
            status=_enum_value(value.status),
            requested_quantity=int(Decimal(str(value.qty or 0))),
            filled_quantity=int(Decimal(str(value.filled_qty or 0))),
            average_fill_price=(
                _decimal(value.filled_avg_price)
                if value.filled_avg_price is not None
                else None
            ),
            submitted_at=submitted,
            child_order_ids=legs,
            take_profit_order_id=take_profit_order_id,
            stop_loss_order_id=stop_loss_order_id,
            side=side,
            filled_at=getattr(value, "filled_at", None),
            exit_fill_price=exit_fill_price,
            exit_fill_quantity=exit_fill_quantity,
            exit_order_id=exit_order_id,
            exit_filled_at=exit_filled_at,
        )


class AlpacaMarketData:
    def __init__(self, settings: Settings):
        validate_settings(settings, require_alpaca_keys=True)
        try:
            from alpaca.data.historical import StockHistoricalDataClient
            from alpaca.data.historical.screener import ScreenerClient
        except ModuleNotFoundError as exc:
            raise RuntimeError("Install requirements.txt to use Alpaca commands.") from exc
        self.settings = settings
        self.stock = StockHistoricalDataClient(
            settings.alpaca_api_key, settings.alpaca_secret_key
        )
        self.screener = ScreenerClient(
            settings.alpaca_api_key, settings.alpaca_secret_key
        )

    def get_scanner_snapshots(self, now: datetime) -> list[MarketSnapshot]:
        from alpaca.data.enums import MarketType
        from alpaca.data.requests import MarketMoversRequest, StockSnapshotRequest

        movers = self.screener.get_market_movers(
            MarketMoversRequest(top=self.settings.scanner_top, market_type=MarketType.STOCKS)
        )
        gainers = list(movers.gainers)
        if not gainers:
            return []
        symbols = [mover.symbol for mover in gainers]
        snapshots = self.stock.get_stock_snapshot(
            StockSnapshotRequest(symbol_or_symbols=symbols, feed=self._feed())
        )
        values: list[MarketSnapshot] = []
        for mover in gainers:
            snapshot = snapshots.get(mover.symbol)
            if snapshot is None or snapshot.latest_quote is None or snapshot.daily_bar is None:
                continue
            quote = snapshot.latest_quote
            bars = self.get_completed_bars(mover.symbol, now, lookback_days=35)
            values.append(
                MarketSnapshot(
                    symbol=mover.symbol,
                    timestamp=now,
                    price=_decimal(mover.price),
                    percent_gain=_decimal(mover.percent_change) / Decimal("100"),
                    rvol=self._time_aligned_rvol(bars, now),
                    day_volume=int(snapshot.daily_bar.volume),
                    bid=_decimal(quote.bid_price),
                    ask=_decimal(quote.ask_price),
                    float_shares=None,
                )
            )
        return values

    def get_completed_bars(
        self, symbol: str, end: datetime, lookback_days: int = 2
    ) -> list[Bar]:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Minute,
            start=end - timedelta(days=lookback_days),
            end=end,
            feed=self._feed(),
        )
        response = self.stock.get_stock_bars(request)
        raw_bars = response.data.get(symbol, [])
        result: list[Bar] = []
        for value in raw_bars:
            completed_at = value.timestamp + timedelta(minutes=1)
            if completed_at > end:
                continue
            result.append(
                Bar(
                    symbol=symbol,
                    timestamp=completed_at,
                    open=_decimal(value.open),
                    high=_decimal(value.high),
                    low=_decimal(value.low),
                    close=_decimal(value.close),
                    volume=int(value.volume),
                )
            )
        return result

    def get_latest_quote(self, symbol: str) -> Quote:
        from alpaca.data.requests import StockLatestQuoteRequest

        response = self.stock.get_stock_latest_quote(
            StockLatestQuoteRequest(symbol_or_symbols=symbol, feed=self._feed())
        )
        value = response[symbol]
        return Quote(
            symbol=symbol,
            timestamp=value.timestamp,
            bid=_decimal(value.bid_price),
            ask=_decimal(value.ask_price),
        )

    def get_completed_ten_second_bars(
        self,
        symbol: str,
        end: datetime,
        lookback_minutes: int = 30,
    ) -> list[Bar]:
        """Aggregate recent real-time-feed trades into completed ten-second bars."""

        from alpaca.data.requests import StockTradesRequest

        response = self.stock.get_stock_trades(
            StockTradesRequest(
                symbol_or_symbols=symbol,
                start=end - timedelta(minutes=lookback_minutes),
                end=end,
                feed=self._feed(),
            )
        )
        trades = [
            TradeTick(
                symbol=symbol,
                timestamp=value.timestamp,
                price=_decimal(value.price),
                size=int(value.size),
            )
            for value in response.data.get(symbol, [])
            if int(value.size) > 0 and value.timestamp <= end
        ]
        return [
            bar
            for bar in aggregate_trades_to_bars(trades, seconds=10)
            if bar.timestamp <= end
        ]

    def _feed(self) -> Any:
        from alpaca.data.enums import DataFeed

        return getattr(DataFeed, self.settings.alpaca_data_feed.upper())

    def _time_aligned_rvol(self, bars: Sequence[Bar], now: datetime) -> Decimal:
        eastern = ZoneInfo(self.settings.timezone)
        local_now = now.astimezone(eastern)
        by_day: dict[Any, int] = {}
        for bar in bars:
            local = bar.timestamp.astimezone(eastern)
            if local.time() > local_now.time():
                continue
            by_day[local.date()] = by_day.get(local.date(), 0) + bar.volume
        current = by_day.pop(local_now.date(), 0)
        history = list(by_day.values())[-20:]
        if current <= 0 or not history:
            return Decimal("0")
        baseline = Decimal(sum(history)) / Decimal(len(history))
        return Decimal(current) / baseline if baseline > 0 else Decimal("0")
