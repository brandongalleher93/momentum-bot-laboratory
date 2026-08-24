"""Small manual risk demonstration. Automated coverage lives in tests/."""

from datetime import datetime, timezone
from decimal import Decimal

from bot.config import Settings
from bot.models import RiskSnapshot, TradePlan
from bot.risk_manager import RiskManager


def main() -> None:
    plan = TradePlan(
        symbol="DEMO",
        setup_id="manual-demo",
        maximum_entry_price=Decimal("5.10"),
        stop_price=Decimal("5.00"),
        target_price=Decimal("5.30"),
        maximum_risk_per_share=Decimal("0.10"),
        reward_to_risk=Decimal("2"),
        breakout_level=Decimal("5.08"),
    )
    result = RiskManager(Settings()).evaluate(
        plan, RiskSnapshot(), datetime.now(timezone.utc)
    )
    print(f"Approved: {result.approval.approved}")
    print(f"Reason: {result.approval.reason}")
    print(f"Quantity: {result.approval.quantity}")
    print(f"Proposed risk: ${result.approval.proposed_risk}")
    print(f"Projected daily loss: ${result.approval.projected_daily_loss}")


if __name__ == "__main__":
    main()
