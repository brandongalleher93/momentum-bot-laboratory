from dataclasses import dataclass
from math import floor
from bot.config import settings


@dataclass
class RiskDecision:
    approved: bool
    reason: str
    symbol: str
    quantity: int
    entry_price: float
    stop_price: float
    risk_per_share: float
    total_dollars_risked: float
    position_value: float


class RiskManager:
    def __init__(self):
        self.account_equity = settings.SIMULATED_ACCOUNT_EQUITY

        self.max_risk_per_trade = self.account_equity * (
            settings.MAX_RISK_PER_TRADE_PERCENT / 100
        )

        self.max_daily_loss = self.account_equity * (
            settings.MAX_DAILY_LOSS_PERCENT / 100
        )

        self.max_position_value = self.account_equity * (
            settings.MAX_POSITION_VALUE_PERCENT / 100
        )

    def evaluate_trade(
        self,
        symbol: str,
        entry_price: float,
        stop_price: float,
        current_daily_loss: float,
        trades_taken_today: int,
        open_positions_count: int,
    ) -> RiskDecision:
        """
        Evaluates whether a trade is allowed.

        For now, this assumes a long trade:
        Buy at entry_price.
        Exit if price falls to stop_price.

        Example:
        Entry: $100
        Stop: $99
        Risk per share: $1
        """

        if entry_price <= 0:
            return self._reject(symbol, entry_price, stop_price, "Entry price must be greater than 0.")

        if stop_price <= 0:
            return self._reject(symbol, entry_price, stop_price, "Stop price must be greater than 0.")

        if stop_price >= entry_price:
            return self._reject(
                symbol,
                entry_price,
                stop_price,
                "For a long trade, stop price must be below entry price."
            )

        if current_daily_loss >= self.max_daily_loss:
            return self._reject(
                symbol,
                entry_price,
                stop_price,
                "Daily loss limit has already been reached."
            )

        if trades_taken_today >= settings.MAX_TRADES_PER_DAY:
            return self._reject(
                symbol,
                entry_price,
                stop_price,
                "Max trades per day has already been reached."
            )

        if open_positions_count >= settings.MAX_OPEN_POSITIONS:
            return self._reject(
                symbol,
                entry_price,
                stop_price,
                "Max open positions limit has already been reached."
            )

        risk_per_share = entry_price - stop_price

        quantity_by_risk = floor(self.max_risk_per_trade / risk_per_share)
        quantity_by_position_value = floor(self.max_position_value / entry_price)

        quantity = min(quantity_by_risk, quantity_by_position_value)

        if quantity < 1:
            return RiskDecision(
                approved=False,
                reason="Trade rejected: position size would be less than 1 share.",
                symbol=symbol,
                quantity=0,
                entry_price=entry_price,
                stop_price=stop_price,
                risk_per_share=risk_per_share,
                total_dollars_risked=0,
                position_value=0,
            )

        total_dollars_risked = quantity * risk_per_share
        position_value = quantity * entry_price

        return RiskDecision(
            approved=True,
            reason="Trade approved by risk manager.",
            symbol=symbol,
            quantity=quantity,
            entry_price=entry_price,
            stop_price=stop_price,
            risk_per_share=round(risk_per_share, 2),
            total_dollars_risked=round(total_dollars_risked, 2),
            position_value=round(position_value, 2),
        )

    def _reject(self, symbol: str, entry_price: float, stop_price: float, reason: str) -> RiskDecision:
        return RiskDecision(
            approved=False,
            reason=f"Trade rejected: {reason}",
            symbol=symbol,
            quantity=0,
            entry_price=entry_price,
            stop_price=stop_price,
            risk_per_share=0,
            total_dollars_risked=0,
            position_value=0,
        )