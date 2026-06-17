from alpaca.trading.client import TradingClient
from bot.config import settings, validate_settings


def main():
    validate_settings()

    trading_client = TradingClient(
        api_key=settings.ALPACA_API_KEY,
        secret_key=settings.ALPACA_SECRET_KEY,
        paper=True
    )

    account = trading_client.get_account()

    print("Connected to Alpaca paper trading.")
    print(f"Account status: {account.status}")
    print(f"Cash: {account.cash}")
    print(f"Equity: {account.equity}")
    print(f"Buying power: {account.buying_power}")
    print("No orders were placed.")


if __name__ == "__main__":
    main()