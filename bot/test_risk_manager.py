from bot.config import validate_settings
from bot.risk_manager import RiskManager


def print_decision(decision):
    print("-" * 60)
    print(f"Approved: {decision.approved}")
    print(f"Reason: {decision.reason}")
    print(f"Symbol: {decision.symbol}")
    print(f"Quantity: {decision.quantity}")
    print(f"Entry: {decision.entry_price}")
    print(f"Stop: {decision.stop_price}")
    print(f"Risk/share: {decision.risk_per_share}")
    print(f"Total $ risked: {decision.total_dollars_risked}")
    print(f"Position value: {decision.position_value}")


def main():
    validate_settings()

    risk_manager = RiskManager()

    print("Risk manager loaded.")
    print(f"Simulated account equity: ${risk_manager.account_equity}")
    print(f"Max risk per trade: ${risk_manager.max_risk_per_trade}")
    print(f"Max daily loss: ${risk_manager.max_daily_loss}")
    print(f"Max position value: ${risk_manager.max_position_value}")

    # Test 1: Normal valid trade
    decision_1 = risk_manager.evaluate_trade(
        symbol="SPY",
        entry_price=100.00,
        stop_price=99.00,
        current_daily_loss=0,
        trades_taken_today=0,
        open_positions_count=0,
    )
    print_decision(decision_1)

    # Test 2: Stop is invalid because it is above entry
    decision_2 = risk_manager.evaluate_trade(
        symbol="SPY",
        entry_price=100.00,
        stop_price=101.00,
        current_daily_loss=0,
        trades_taken_today=0,
        open_positions_count=0,
    )
    print_decision(decision_2)

    # Test 3: Daily loss already reached
    decision_3 = risk_manager.evaluate_trade(
        symbol="SPY",
        entry_price=100.00,
        stop_price=99.00,
        current_daily_loss=15,
        trades_taken_today=0,
        open_positions_count=0,
    )
    print_decision(decision_3)

    # Test 4: Too many trades already taken
    decision_4 = risk_manager.evaluate_trade(
        symbol="SPY",
        entry_price=100.00,
        stop_price=99.00,
        current_daily_loss=0,
        trades_taken_today=3,
        open_positions_count=0,
    )
    print_decision(decision_4)

    # Test 5: Position already open
    decision_5 = risk_manager.evaluate_trade(
        symbol="SPY",
        entry_price=100.00,
        stop_price=99.00,
        current_daily_loss=0,
        trades_taken_today=0,
        open_positions_count=1,
    )
    print_decision(decision_5)


if __name__ == "__main__":
    main()