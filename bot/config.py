"""Versioned configuration for the paper-trading MVP.

The defaults mirror the decisions in Momentum_Bot_MVP_Pseudocode_v1_2.docx.
Environment variables may override operational values, but every run records the
configuration version and parameter profile in its diagnostic events.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from datetime import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping, Optional

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # Core tests do not require python-dotenv.
    load_dotenv = None


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_env_file() -> None:
    if load_dotenv is not None:
        load_dotenv(PROJECT_ROOT / ".env")
        return

    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _text(env: Mapping[str, str], name: str, default: str) -> str:
    return env.get(name, default).strip()


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"{name} must be true or false, got {raw!r}.")


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    try:
        return int(env.get(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer.") from exc


def _optional_int(
    env: Mapping[str, str], name: str, default: Optional[int]
) -> Optional[int]:
    raw = env.get(name)
    if raw is None:
        return default
    if raw.strip().lower() in {"", "none", "null"}:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer or null.") from exc


def _decimal(env: Mapping[str, str], name: str, default: str) -> Decimal:
    raw = env.get(name, default)
    try:
        return Decimal(str(raw))
    except InvalidOperation as exc:
        raise ValueError(f"{name} must be a decimal number.") from exc


def _optional_decimal(
    env: Mapping[str, str], name: str, default: Optional[str]
) -> Optional[Decimal]:
    raw = env.get(name, default)
    if raw is None or str(raw).strip().lower() in {"", "none", "null"}:
        return None
    try:
        return Decimal(str(raw))
    except InvalidOperation as exc:
        raise ValueError(f"{name} must be a decimal number or null.") from exc


@dataclass(frozen=True)
class Settings:
    # Identity / traceability
    version: str = "mvp_v1_1_decision_baseline_2026_07"
    parameter_profile: str = "strict_bull_flag_intrabar_v1"
    decision_register_version: str = "2026-07-17"

    # Alpaca safety
    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    alpaca_paper: bool = True
    allow_live_trading: bool = False
    paper_order_submission_enabled: bool = False
    alpaca_data_feed: str = "iex"

    # Session
    timezone: str = "America/New_York"
    trade_window_start: time = time(9, 30)
    trade_window_end: time = time(11, 30)
    primary_timeframe_minutes: int = 1
    poll_seconds: int = 5

    # Account / daily risk (D-003)
    account_equity_assumption: Decimal = Decimal("500")
    max_risk_per_trade: Decimal = Decimal("5")
    max_daily_loss: Decimal = Decimal("15")
    max_position_value: Decimal = Decimal("125")
    max_open_positions: int = 1
    max_active_entry_orders: int = 1
    max_trades_per_day: Optional[int] = None
    max_consecutive_losses: Optional[int] = None
    cooldown_after_loss_minutes: Optional[int] = None
    daily_equity_drawdown_limit: Optional[Decimal] = None

    # Scanner
    scanner_top: int = 20
    min_price: Decimal = Decimal("2")
    max_price: Decimal = Decimal("20")
    min_percent_gain: Decimal = Decimal("0.10")
    min_rvol: Decimal = Decimal("5")
    min_day_volume: int = 500_000
    preferred_float_max: int = 20_000_000
    max_spread_percent: Decimal = Decimal("0.01")
    near_high_of_day_percent: Decimal = Decimal("0.02")

    # Indicators / flagpole
    ema_period: int = 9
    recent_volume_lookback_candles: int = 20
    recent_range_lookback_candles: int = 20
    strong_up_move_min_percent: Decimal = Decimal("0.03")
    strong_up_move_max_candles: int = 5
    min_flagpole_green_candles: int = 1
    flagpole_volume_ratio_min: Decimal = Decimal("1.50")

    # Pullback (D-004 / D-005)
    pullback_candles_min: int = 2
    pullback_candles_max: int = 3
    allow_four_candle_pullback: bool = False
    record_four_candle_counterfactual: bool = True
    setup_expiration_candles: int = 1
    small_consolidation_body_to_range_max: Decimal = Decimal("0.35")
    small_consolidation_range_to_flagpole_avg_max: Decimal = Decimal("0.60")
    require_nonincreasing_pullback_highs: bool = True
    allow_equal_pullback_highs: bool = True
    preferred_pullback_depth: Decimal = Decimal("0.25")
    hard_max_pullback_depth: Decimal = Decimal("0.50")
    pullback_volume_ratio_max: Decimal = Decimal("0.70")
    large_upper_wick_ratio: Decimal = Decimal("0.40")
    doji_body_to_range_max: Decimal = Decimal("0.10")
    shooting_star_upper_wick_to_range_min: Decimal = Decimal("0.50")

    # Entry (D-002)
    entry_trigger_buffer_ticks: int = 1
    entry_limit_offset_ticks: int = 2
    max_entry_chase_ticks: int = 3
    entry_order_timeout_seconds: int = 5

    # Plan / exit (D-006)
    max_extension_above_ema_percent: Decimal = Decimal("0.05")
    max_extension_above_vwap_percent: Decimal = Decimal("0.05")
    max_allowed_stop_distance: Decimal = Decimal("0.20")
    min_reward_to_risk: Decimal = Decimal("2.0")
    target_r_multiple: Decimal = Decimal("2.0")
    exit_on_vwap_loss: bool = True
    exit_on_ema_loss: bool = True
    exit_on_breakout_level_fail: bool = True
    flat_by_end_of_day: bool = True

    # Execution / reconciliation (D-008–D-010)
    partial_fill_protection_timeout_seconds: int = 2
    periodic_reconciliation_seconds: int = 30

    # Backtest (D-007, D-011, D-013)
    same_bar_stop_target_policy: str = "conservative_stop_first"
    backtest_entry_slippage_bps: Decimal = Decimal("10")
    backtest_exit_slippage_bps: Decimal = Decimal("10")
    commission_per_trade: Decimal = Decimal("0")

    # Files (D-012)
    log_dir: Path = PROJECT_ROOT / "logs"
    output_dir: Path = PROJECT_ROOT / "output"

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-safe configuration snapshot for run metadata."""

        values = asdict(self)
        for key, value in list(values.items()):
            if isinstance(value, Decimal):
                values[key] = str(value)
            elif isinstance(value, Path):
                values[key] = str(value)
            elif isinstance(value, time):
                values[key] = value.isoformat()
        values["alpaca_api_key"] = "***" if self.alpaca_api_key else ""
        values["alpaca_secret_key"] = "***" if self.alpaca_secret_key else ""
        return values


def load_settings(environ: Optional[Mapping[str, str]] = None) -> Settings:
    env = dict(os.environ if environ is None else environ)
    settings = Settings(
        version=_text(env, "CONFIG_VERSION", Settings.version),
        parameter_profile=_text(env, "PARAMETER_PROFILE", Settings.parameter_profile),
        decision_register_version=_text(
            env, "DECISION_REGISTER_VERSION", Settings.decision_register_version
        ),
        alpaca_api_key=_text(env, "ALPACA_API_KEY", ""),
        alpaca_secret_key=_text(env, "ALPACA_SECRET_KEY", ""),
        alpaca_paper=_bool(env, "ALPACA_PAPER", True),
        allow_live_trading=_bool(env, "ALLOW_LIVE_TRADING", False),
        paper_order_submission_enabled=_bool(
            env, "PAPER_ORDER_SUBMISSION_ENABLED", False
        ),
        alpaca_data_feed=_text(env, "ALPACA_DATA_FEED", "iex"),
        account_equity_assumption=_decimal(env, "ACCOUNT_EQUITY_ASSUMPTION", "500"),
        max_risk_per_trade=_decimal(env, "MAX_RISK_PER_TRADE", "5"),
        max_daily_loss=_decimal(env, "MAX_DAILY_LOSS", "15"),
        max_position_value=_decimal(env, "MAX_POSITION_VALUE", "125"),
        max_open_positions=_int(env, "MAX_OPEN_POSITIONS", 1),
        max_active_entry_orders=_int(env, "MAX_ACTIVE_ENTRY_ORDERS", 1),
        max_trades_per_day=_optional_int(env, "MAX_TRADES_PER_DAY", None),
        max_consecutive_losses=_optional_int(
            env, "MAX_CONSECUTIVE_LOSSES", None
        ),
        cooldown_after_loss_minutes=_optional_int(
            env, "COOLDOWN_AFTER_LOSS_MINUTES", None
        ),
        daily_equity_drawdown_limit=_optional_decimal(
            env, "DAILY_EQUITY_DRAWDOWN_LIMIT", None
        ),
        scanner_top=_int(env, "SCANNER_TOP", 20),
        min_price=_decimal(env, "MIN_PRICE", "2"),
        max_price=_decimal(env, "MAX_PRICE", "20"),
        min_percent_gain=_decimal(env, "MIN_PERCENT_GAIN", "0.10"),
        min_rvol=_decimal(env, "MIN_RVOL", "5"),
        min_day_volume=_int(env, "MIN_DAY_VOLUME", 500_000),
        preferred_float_max=_int(env, "PREFERRED_FLOAT_MAX", 20_000_000),
        max_spread_percent=_decimal(env, "MAX_SPREAD_PERCENT", "0.01"),
        near_high_of_day_percent=_decimal(env, "NEAR_HIGH_OF_DAY_PERCENT", "0.02"),
        strong_up_move_min_percent=_decimal(
            env, "STRONG_UP_MOVE_MIN_PERCENT", "0.03"
        ),
        flagpole_volume_ratio_min=_decimal(
            env, "FLAGPOLE_VOLUME_RATIO_MIN", "1.50"
        ),
        preferred_pullback_depth=_decimal(
            env, "PREFERRED_PULLBACK_DEPTH", "0.25"
        ),
        pullback_volume_ratio_max=_decimal(
            env, "PULLBACK_VOLUME_RATIO_MAX", "0.70"
        ),
        entry_limit_offset_ticks=_int(env, "ENTRY_LIMIT_OFFSET_TICKS", 2),
        max_entry_chase_ticks=_int(env, "MAX_ENTRY_CHASE_TICKS", 3),
        entry_order_timeout_seconds=_int(env, "ENTRY_ORDER_TIMEOUT_SECONDS", 5),
        max_allowed_stop_distance=_decimal(
            env, "MAX_ALLOWED_STOP_DISTANCE", "0.20"
        ),
        target_r_multiple=_decimal(env, "TARGET_R_MULTIPLE", "2.0"),
        backtest_entry_slippage_bps=_decimal(
            env, "BACKTEST_ENTRY_SLIPPAGE_BPS", "10"
        ),
        backtest_exit_slippage_bps=_decimal(
            env, "BACKTEST_EXIT_SLIPPAGE_BPS", "10"
        ),
        log_dir=Path(_text(env, "LOG_DIR", str(PROJECT_ROOT / "logs"))),
        output_dir=Path(_text(env, "OUTPUT_DIR", str(PROJECT_ROOT / "output"))),
    )
    validate_settings(settings)
    return settings


def validate_settings(
    value: Optional[Settings] = None, require_alpaca_keys: bool = False
) -> None:
    candidate = settings if value is None else value
    errors: list[str] = []

    if not candidate.alpaca_paper:
        errors.append("ALPACA_PAPER must remain true for the MVP.")
    if candidate.allow_live_trading:
        errors.append("ALLOW_LIVE_TRADING must remain false for the MVP.")
    if require_alpaca_keys and not candidate.alpaca_api_key:
        errors.append("ALPACA_API_KEY is required for Alpaca commands.")
    if require_alpaca_keys and not candidate.alpaca_secret_key:
        errors.append("ALPACA_SECRET_KEY is required for Alpaca commands.")

    positive_money = {
        "ACCOUNT_EQUITY_ASSUMPTION": candidate.account_equity_assumption,
        "MAX_RISK_PER_TRADE": candidate.max_risk_per_trade,
        "MAX_DAILY_LOSS": candidate.max_daily_loss,
        "MAX_POSITION_VALUE": candidate.max_position_value,
    }
    for name, amount in positive_money.items():
        if amount <= 0:
            errors.append(f"{name} must be greater than zero.")
    if candidate.max_risk_per_trade > candidate.max_daily_loss:
        errors.append("MAX_RISK_PER_TRADE cannot exceed MAX_DAILY_LOSS.")
    if candidate.max_position_value > candidate.account_equity_assumption:
        errors.append("MAX_POSITION_VALUE cannot exceed the account assumption.")
    if candidate.min_price >= candidate.max_price:
        errors.append("MIN_PRICE must be below MAX_PRICE.")
    for name, percentage in {
        "MIN_PERCENT_GAIN": candidate.min_percent_gain,
        "MAX_SPREAD_PERCENT": candidate.max_spread_percent,
        "PREFERRED_PULLBACK_DEPTH": candidate.preferred_pullback_depth,
    }.items():
        if percentage < 0 or percentage > 1:
            errors.append(f"{name} must use a decimal fraction between 0 and 1.")
    if candidate.pullback_candles_min < 1:
        errors.append("PULLBACK_CANDLES_MIN must be at least 1.")
    if candidate.pullback_candles_max < candidate.pullback_candles_min:
        errors.append("PULLBACK_CANDLES_MAX must be >= PULLBACK_CANDLES_MIN.")
    if candidate.same_bar_stop_target_policy != "conservative_stop_first":
        errors.append("The MVP requires conservative_stop_first for ambiguous bars.")

    if errors:
        raise ValueError("Invalid settings: " + " ".join(errors))


_load_env_file()
settings = load_settings()
