"""Streamlit + Plotly command center for the momentum bot MVP.

Run with: streamlit run bot/gui.py
"""

from __future__ import annotations

import base64
import csv
import io
import json
import subprocess
import sys
from dataclasses import fields, replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from html import escape
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

# Streamlit executes this file with bot/ as the script directory. Ensure the
# project package remains importable without requiring an editable install.
_PROJECT_DIRECTORY = Path(__file__).resolve().parent.parent
if str(_PROJECT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(_PROJECT_DIRECTORY))

from bot.security import enforce_private_umask, ensure_private_directory

enforce_private_umask()

from bot.backtest import BacktestEngine, load_bars_csv, load_bars_csv_stream
from bot.config import PROJECT_ROOT, Settings, load_settings, validate_settings
from bot.event_log import redact_sensitive_fields, to_json_safe
from bot.gui_support import (
    PROTECTED_FIELDS,
    active_shadow_protection_rows,
    config_diff,
    execution_audit_table_rows,
    list_profiles,
    load_profile,
    read_jsonl,
    recent_decisions,
    save_profile,
    settings_from_mapping,
    trade_table_rows,
)
from bot.history_store import HistoryStore
from bot.historical_data import (
    AlpacaHistoricalDownloader,
    BarCache,
    TradeCache,
    load_workspace_manifest,
    save_workspace_manifest,
)
from bot.portfolio_backtest import (
    PortfolioReplayEngine,
    ReplayGuardrails,
)
from bot.reconstruction import CandidateReconstructor
from bot.review import build_backtest_report, calculate_metrics
from bot.shadow_runner import ShadowRunner
from bot.shadow_schedule import launch_agent_is_configured
from bot.validation_set import (
    load_validation_targets,
    target_dates_by_symbol,
    validation_download_windows,
)


PROFILE_DIR = PROJECT_ROOT / "output" / "gui_profiles"
RUN_DIR = PROJECT_ROOT / "output" / "gui_backtests"
HISTORY_DIR = PROJECT_ROOT / "output" / "history"
HISTORY_DB = HISTORY_DIR / "history.sqlite3"
SHADOW_EXECUTION_AUDIT = (
    PROJECT_ROOT / "output" / "shadow_paper" / "execution_audit.json"
)
HISTORICAL_WORKSPACE_MANIFEST = HISTORY_DIR / "active_workspace.json"
def _available_validation_manifests() -> dict[str, Path]:
    manifests = {
        "Bundled synthetic example": (
            PROJECT_ROOT / "examples" / "validation_manifest.example.csv"
        )
    }
    private_directory = PROJECT_ROOT / "private_data" / "research" / "data"
    for path in sorted(private_directory.glob("*validation*.csv")):
        label = "Local private — " + path.stem.replace("_", " ").title()
        manifests[label] = path
    return manifests


VALIDATION_MANIFESTS = _available_validation_manifests()
BRAND_ASSET_DIR = PROJECT_ROOT / "assets" / "branding"
BRAND_APP_ICON = BRAND_ASSET_DIR / "momentum-bot-app-icon.png"
BRAND_HORIZONTAL_LOGO = BRAND_ASSET_DIR / "momentum-bot-horizontal-dark.png"


def _safe_error_text(error: Exception) -> str:
    return str(redact_sensitive_fields(str(error)))

HISTORICAL_REPLAY_PROFILES = {
    "Strict baseline": {},
    "Premarket validation": {
        "trade_window_start": time(7, 0),
        "preferred_pullback_depth": Decimal("0.50"),
        "large_upper_wick_ratio": Decimal("0.50"),
        "near_high_of_day_percent": Decimal("0.10"),
        "max_extension_above_vwap_percent": Decimal("0.25"),
        "max_extension_above_ema_percent": Decimal("0.10"),
    },
    "Regular-hours paper validation": {
        "trade_window_start": time(9, 30),
        "preferred_pullback_depth": Decimal("0.50"),
        "large_upper_wick_ratio": Decimal("0.50"),
        "near_high_of_day_percent": Decimal("0.10"),
        "max_extension_above_vwap_percent": Decimal("0.25"),
        "max_extension_above_ema_percent": Decimal("0.10"),
    },
}

REPLAY_GUARDRAIL_PRESETS = {
    "Baseline — no experimental guardrails": ReplayGuardrails(),
    "2-minute re-entry cooldown only": ReplayGuardrails(
        reentry_cooldown_minutes=2
    ),
    "Stop after 2 consecutive losses only": ReplayGuardrails(
        max_consecutive_losses_per_symbol_day=2
    ),
    "Maximum 3 trades per symbol/day only": ReplayGuardrails(
        max_trades_per_symbol_day=3
    ),
    "Combined suggested test": ReplayGuardrails(
        reentry_cooldown_minutes=2,
        max_consecutive_losses_per_symbol_day=2,
        max_trades_per_symbol_day=3,
    ),
}

CONFIG_GROUPS = {
    "Identity": ["version", "parameter_profile", "decision_register_version"],
    "Session": ["timezone", "trade_window_start", "trade_window_end", "primary_timeframe_minutes", "poll_seconds"],
    "Account & risk": ["account_equity_assumption", "max_risk_per_trade", "max_daily_loss", "max_position_value", "max_open_positions", "max_active_entry_orders", "max_trades_per_day", "max_consecutive_losses", "max_consecutive_losses_per_symbol_day", "cooldown_after_loss_minutes", "daily_equity_drawdown_limit"],
    "Scanner": ["scanner_top", "min_price", "max_price", "min_percent_gain", "min_rvol", "min_day_volume", "preferred_float_max", "max_spread_percent", "near_high_of_day_percent"],
    "Flagpole": ["ema_period", "recent_volume_lookback_candles", "recent_range_lookback_candles", "strong_up_move_min_percent", "strong_up_move_max_candles", "min_flagpole_green_candles", "flagpole_volume_ratio_min"],
    "Pullback": ["pullback_candles_min", "pullback_candles_max", "allow_four_candle_pullback", "record_four_candle_counterfactual", "setup_expiration_candles", "small_consolidation_body_to_range_max", "small_consolidation_range_to_flagpole_avg_max", "require_nonincreasing_pullback_highs", "allow_equal_pullback_highs", "preferred_pullback_depth", "hard_max_pullback_depth", "pullback_volume_ratio_max", "large_upper_wick_ratio", "doji_body_to_range_max", "shooting_star_upper_wick_to_range_min"],
    "Entry": ["entry_trigger_buffer_ticks", "entry_limit_offset_ticks", "max_entry_chase_ticks", "entry_order_timeout_seconds"],
    "Exit & plan": ["max_extension_above_ema_percent", "max_extension_above_vwap_percent", "max_allowed_stop_distance", "min_reward_to_risk", "target_r_multiple", "exit_on_vwap_loss", "exit_on_ema_loss", "exit_on_breakout_level_fail", "flat_by_end_of_day"],
    "Execution": ["partial_fill_protection_timeout_seconds", "periodic_reconciliation_seconds"],
    "Backtest": ["same_bar_stop_target_policy", "backtest_entry_slippage_bps", "backtest_exit_slippage_bps", "commission_per_trade"],
}


def historical_replay_settings(settings: Settings, profile: str) -> Settings:
    overrides = HISTORICAL_REPLAY_PROFILES.get(profile)
    if overrides is None:
        raise ValueError(f"Unknown historical replay profile: {profile}")
    if not overrides:
        return settings
    profile_slug = profile.lower().replace(" ", "_")
    return replace(
        settings,
        **overrides,
        parameter_profile=(
            f"{settings.parameter_profile}:historical_{profile_slug}_v1"
        ),
    )


def _imports():
    import pandas as pd
    import plotly.graph_objects as go
    import streamlit as st
    from plotly.subplots import make_subplots
    return st, pd, go, make_subplots


def _fmt_money(value: Any) -> str:
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "—"


def _fmt_pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "—"


def _image_data_uri(path: Path) -> str | None:
    """Return a local PNG as a browser-safe data URI, if it is available."""
    try:
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        return None
    return f"data:image/png;base64,{encoded}"


def _init_state(st) -> None:
    if "gui_settings" not in st.session_state:
        st.session_state.gui_settings = load_settings()
    st.session_state.setdefault("backtest_result", None)
    st.session_state.setdefault("backtest_bars", None)
    st.session_state.setdefault("backtest_source", None)
    st.session_state.setdefault("account", None)
    st.session_state.setdefault("paper_cycle_output", None)
    st.session_state.setdefault("shadow_cycle_output", None)
    st.session_state.setdefault("historical_bars", {})
    st.session_state.setdefault("historical_execution_bars", {})
    st.session_state.setdefault("historical_missing_symbols", [])
    st.session_state.setdefault("historical_feed", None)
    st.session_state.setdefault("historical_workspace_paths", {})
    st.session_state.setdefault("historical_execution_workspace_paths", {})
    st.session_state.setdefault("historical_trade_workspace_paths", {})
    st.session_state.setdefault("historical_validation_dates_by_symbol", {})
    st.session_state.setdefault("historical_validation_name", None)
    st.session_state.setdefault(
        "historical_validation_selection",
        next(iter(VALIDATION_MANIFESTS)),
    )
    st.session_state.setdefault("historical_workspace_restore_attempted", False)
    st.session_state.setdefault("historical_replay_profile", "Strict baseline")
    st.session_state.setdefault(
        "historical_guardrail_preset", "Combined suggested test"
    )
    st.session_state.setdefault("portfolio_result", None)
    st.session_state.setdefault("portfolio_result_profile", None)
    st.session_state.setdefault("portfolio_result_guardrail_preset", None)


def _style(st) -> None:
    page_icon = str(BRAND_APP_ICON) if BRAND_APP_ICON.is_file() else "📈"
    st.set_page_config(
        page_title="Momentum Bot Laboratory",
        page_icon=page_icon,
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.markdown("""
    <style>
    .stApp {
      --mb-green:#00E676;
      --mb-teal:#00C2A8;
      --mb-white:#F4F7FA;
      --mb-slate:#94A3B8;
      --mb-navy:#0A1320;
      --app-bg:light-dark(#f4f7fa, var(--mb-navy));
      --sidebar-bg:light-dark(#e8eef3, #07101c);
      --sidebar-text:light-dark(var(--mb-navy), var(--mb-white));
      --surface:light-dark(#ffffff, #111c2c);
      --surface-raised:light-dark(#edf3f6, #142235);
      --border:light-dark(#cbd5e1, #2a3b50);
      --border-soft:light-dark(#dbe3ea, #1c2b3d);
      --text:light-dark(var(--mb-navy), var(--mb-white));
      --text-muted:light-dark(#526174, var(--mb-slate));
      --positive:light-dark(#087d48, var(--mb-green));
      --interactive:light-dark(#007c70, var(--mb-teal));
      --safety-bg:light-dark(#e4f7ed, #0b3024);
      --safety-text:light-dark(#155b3b, #d9ffea);
      --warning-bg:light-dark(#fff0ed, #3a1d1b);
      --warning-text:light-dark(#7d2e20, #ffd9d5);
      --amber:#e9c46a;
      --red:light-dark(#a43d2a, #ff7b72);
      background:var(--app-bg);
      color:var(--text);
      font-family:Inter, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    [data-testid="stSidebar"] { background:var(--sidebar-bg); }
    [data-testid="stSidebar"] * { color:var(--sidebar-text); }
    a {color:var(--interactive)}
    div.stButton > button:focus-visible,
    div[data-testid="stDownloadButton"] > button:focus-visible {
      outline:3px solid color-mix(in srgb, var(--interactive) 45%, transparent);
      outline-offset:2px;
    }
    .brand-hero {
      display:flex;
      align-items:center;
      justify-content:space-between;
      gap:1.25rem;
      min-width:0;
      overflow:hidden;
      padding:.65rem clamp(.75rem, 2vw, 1.25rem);
      margin:.15rem 0 .8rem;
      background:linear-gradient(135deg, #0a1320 0%, #0d1f2f 100%);
      border:1px solid color-mix(in srgb, var(--mb-teal) 36%, #25374a);
      border-left:3px solid var(--mb-green);
      border-radius:.65rem;
    }
    .brand-lockup {
      width:min(100%, 34rem);
      min-width:0;
      overflow:hidden;
      aspect-ratio:725 / 196;
    }
    .brand-lockup img {
      display:block;
      width:100%;
      max-width:none;
      transform:translateY(-5.5%);
    }
    .brand-hero-copy {
      flex:0 1 16rem;
      color:var(--mb-slate);
      font-size:.72rem;
      font-weight:600;
      letter-spacing:.16em;
      line-height:1.55;
      text-align:right;
      text-transform:uppercase;
    }
    .brand-hero-copy strong {color:var(--mb-green);font-weight:700}
    .brand-fallback-title {
      color:var(--mb-white);
      font-size:clamp(1.45rem, 4vw, 2.2rem);
      font-weight:700;
      margin:0;
    }
    .sidebar-brand {
      display:flex;
      align-items:center;
      gap:.65rem;
      margin:.1rem 0 .8rem;
    }
    .sidebar-brand-mark {
      flex:0 0 3.25rem;
      width:3.25rem;
      height:3.25rem;
      overflow:hidden;
      border-radius:.8rem;
    }
    .sidebar-brand-mark img {
      display:block;
      width:114%;
      max-width:none;
      transform:translate(-7%, -7%);
    }
    .sidebar-brand-name {
      color:var(--sidebar-text);
      font-size:1rem;
      font-weight:700;
      letter-spacing:.01em;
      line-height:1.1;
    }
    .sidebar-brand-name span {color:var(--positive)}
    .sidebar-brand-subtitle {
      color:var(--text-muted);
      font-size:.62rem;
      font-weight:600;
      letter-spacing:.16em;
      margin-top:.25rem;
      text-transform:uppercase;
    }
    .status-strip {
      display:grid;
      grid-template-columns:repeat(auto-fit, minmax(min(8rem, 100%), 1fr));
      gap:1px;
      background:var(--border-soft);
      border:1px solid var(--border);
      border-radius:.5rem;
      box-shadow:0 1px 3px rgba(0,0,0,.24);
      margin:.15rem 0 .4rem;
      overflow:hidden;
    }
    .status-item {
      min-width:0;
      background:var(--surface);
      padding:.42rem .65rem .45rem;
    }
    .status-label {
      color:var(--text-muted);
      font-size:.63rem;
      font-weight:700;
      letter-spacing:.06em;
      line-height:1.1;
      text-transform:uppercase;
      overflow-wrap:anywhere;
    }
    .status-value {
      color:var(--text);
      font-size:.88rem;
      font-weight:700;
      line-height:1.25;
      margin-top:.16rem;
      overflow-wrap:anywhere;
    }
    .status-item:last-child .status-value {
      font-size:.72rem;
    }
    .status-danger {color:var(--red)}
    .safety {
      border-left:4px solid var(--positive);
      background:var(--safety-bg);
      color:var(--safety-text);
      padding:.38rem .7rem;
      border-radius:.3rem;
      font-size:.8rem;
      line-height:1.35;
      margin:0 0 .8rem;
    }
    .warning {border-left-color:var(--red);background:var(--warning-bg);color:var(--warning-text)}
    div[data-testid="stDataFrame"] {border:1px solid var(--border);border-radius:.4rem}
    .smallcaps {font-size:.72rem;letter-spacing:.08em;text-transform:uppercase;color:var(--text-muted);font-weight:700}
    .metric-grid {
      display:grid;
      grid-template-columns:repeat(auto-fit, minmax(min(9rem, 100%), 1fr));
      gap:.75rem;
      margin:.5rem 0 .75rem;
    }
    .metric-card {
      min-width:0;
      background:var(--surface-raised);
      border:1px solid var(--border);
      border-radius:.55rem;
      box-shadow:0 1px 3px rgba(0,0,0,.24);
      padding:.75rem .85rem;
    }
    .metric-card-label {
      display:flex;
      align-items:flex-start;
      justify-content:space-between;
      gap:.35rem;
      min-height:2.4em;
      color:var(--text-muted);
      font-size:.75rem;
      font-weight:650;
      line-height:1.2;
      overflow-wrap:anywhere;
    }
    .metric-card-label > span:first-child {min-width:0}
    .metric-card-help {
      display:inline-grid;
      place-items:center;
      flex:0 0 auto;
      width:1rem;
      height:1rem;
      border:1px solid var(--text-muted);
      border-radius:50%;
      cursor:help;
      font-size:.67rem;
      line-height:1;
    }
    .metric-card-value {
      color:var(--text);
      font-size:clamp(1.3rem, 2vw, 1.7rem);
      font-variant-numeric:tabular-nums;
      font-weight:650;
      letter-spacing:-.02em;
      line-height:1.15;
      margin-top:.4rem;
      overflow-wrap:anywhere;
    }
    @media (max-width: 520px) {
      .brand-hero {align-items:flex-start;flex-direction:column;gap:.15rem}
      .brand-lockup {width:100%}
      .brand-hero-copy {flex:auto;text-align:left}
      .metric-card-label {min-height:0}
    }
    </style>""", unsafe_allow_html=True)


def _status_header(st, settings: Settings) -> None:
    account = st.session_state.account or {}
    result = st.session_state.backtest_result
    metrics = calculate_metrics(result.trades) if result else {}
    order_safety = (
        "ARMED" if settings.paper_order_submission_enabled else "DISARMED"
    )
    status_items = [
        ("Mode", "PAPER ONLY"),
        ("Order safety", order_safety),
        ("Connection", str(account.get("status", "Not checked"))),
        ("Open positions", str(account.get("open_positions", 0))),
        ("Latest test P/L", _fmt_money(metrics.get("net_profit"))),
        ("Profile", settings.parameter_profile),
    ]
    if settings.gui_show_account_details and account:
        status_items[2:2] = [
            ("Equity", _fmt_money(account.get("equity"))),
            ("Buying power", _fmt_money(account.get("buying_power"))),
        ]
    item_html = []
    for label, value in status_items:
        danger = (
            " status-danger"
            if label == "Order safety"
            and settings.paper_order_submission_enabled
            else ""
        )
        item_html.append(
            '<div class="status-item">'
            f'<div class="status-label">{escape(label)}</div>'
            f'<div class="status-value{danger}">{escape(value)}</div>'
            "</div>"
        )
    st.markdown(
        '<div class="status-strip" role="status" '
        'aria-label="System status">'
        + "".join(item_html)
        + "</div>",
        unsafe_allow_html=True,
    )
    css = "safety warning" if settings.paper_order_submission_enabled else "safety"
    message = "Paper order submission is ARMED. The application still rejects live trading." if settings.paper_order_submission_enabled else "Paper order submission is disarmed. Backtesting and read-only monitoring are safe to use."
    st.markdown(
        f'<div class="{css}"><b>Safety state:</b> {escape(message)}</div>',
        unsafe_allow_html=True,
    )


def _metric_cards(st, items, aria_label: str) -> None:
    cards = []
    for label, value, help_text in items:
        help_html = (
            '<span class="metric-card-help" '
            f'title="{escape(help_text)}" '
            f'aria-label="{escape(help_text)}">?</span>'
            if help_text
            else ""
        )
        cards.append(
            '<div class="metric-card">'
            '<div class="metric-card-label">'
            f"<span>{escape(label)}</span>{help_html}"
            "</div>"
            f'<div class="metric-card-value">{escape(str(value))}</div>'
            "</div>"
        )
    st.markdown(
        '<div class="metric-grid" role="group" '
        f'aria-label="{escape(aria_label)}">'
        + "".join(cards)
        + "</div>",
        unsafe_allow_html=True,
    )


def _brand_hero(st) -> None:
    logo_data = _image_data_uri(BRAND_HORIZONTAL_LOGO)
    logo_html = (
        '<div class="brand-lockup">'
        f'<img src="{logo_data}" alt="Momentum Bot Laboratory">'
        "</div>"
        if logo_data
        else '<h1 class="brand-fallback-title">Momentum Bot Laboratory</h1>'
    )
    st.markdown(
        '<div class="brand-hero">'
        f"{logo_html}"
        '<div class="brand-hero-copy">Built for the <strong>setups</strong> '
        "that matter</div>"
        "</div>",
        unsafe_allow_html=True,
    )


def _sidebar(st) -> str:
    icon_data = _image_data_uri(BRAND_APP_ICON)
    icon_html = (
        '<div class="sidebar-brand-mark">'
        f'<img src="{icon_data}" alt="">'
        "</div>"
        if icon_data
        else ""
    )
    st.sidebar.markdown(
        '<div class="sidebar-brand">'
        f"{icon_html}"
        '<div><div class="sidebar-brand-name">Momentum <span>Bot</span></div>'
        '<div class="sidebar-brand-subtitle">Laboratory</div></div>'
        "</div>",
        unsafe_allow_html=True,
    )
    st.sidebar.caption("Observe · Understand · Tune · Trade")
    page = st.sidebar.radio("Workspace", ["Dashboard", "Backtest Lab", "Historical Data", "Scanner & Reasoning", "Trades", "Configuration", "Logs"], label_visibility="collapsed")
    st.sidebar.divider()
    st.sidebar.markdown("**North Star**")
    st.sidebar.caption("Every trade—or rejected trade—should be explainable within 30 seconds.")
    st.sidebar.caption("MVP · Paper trading only")
    st.sidebar.divider()
    st.sidebar.markdown("**Appearance**")
    st.sidebar.caption(
        "Choose Light or Dark from the top-right ⋮ menu → Settings → "
        "Appearance."
    )
    return page


def _account_check(st, settings: Settings) -> None:
    if st.button("Check Alpaca paper account", use_container_width=True):
        if not settings.alpaca_api_key or not settings.alpaca_secret_key:
            st.warning("Add Alpaca paper credentials to .env first. No connection was attempted.")
            return
        try:
            from bot.alpaca_adapters import AlpacaPaperBroker
            broker = AlpacaPaperBroker(settings)
            account = broker.get_account()
            positions = list(broker.get_positions())
            st.session_state.account = {
                "status": account.status,
                "open_positions": len(positions),
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
            if settings.gui_show_account_details:
                st.session_state.account.update(
                    {
                        "equity": account.equity,
                        "cash": account.cash,
                        "buying_power": account.buying_power,
                        "positions": [to_json_safe(value) for value in positions],
                    }
                )
            st.success("Connected to the Alpaca paper account. No order was placed.")
            st.rerun()
        except Exception as exc:
            st.error(f"Paper-account check failed: {_safe_error_text(exc)}")


def _dashboard(st, pd, settings: Settings) -> None:
    st.header("Dashboard")
    st.caption("Mission control: system health, testing state, and the shortest path to the next useful experiment.")
    left, right = st.columns([1.4, 1])
    with left:
        st.subheader("Current experiment")
        result = st.session_state.backtest_result
        if result:
            metrics = calculate_metrics(result.trades)
            _metric_cards(
                st,
                [
                    ("Symbol", result.symbol, None),
                    ("Trades", metrics["total_trades"], None),
                    ("Win rate", _fmt_pct(metrics["win_rate"]), None),
                    ("Net P/L", _fmt_money(metrics["net_profit"]), None),
                ],
                "Current experiment summary",
            )
            st.info(f"Latest source: {st.session_state.backtest_source} · {result.entry_opportunities} entry opportunities · {result.setup_rejections} setup rejections")
        else:
            st.info("No backtest has been run in this session. Open Backtest Lab and run the included sample or upload a CSV.")
        st.subheader("Recent decisions")
        decisions = recent_decisions(settings.log_dir, 8)
        if decisions:
            frame = pd.DataFrame([{"time": row.get("timestamp"), "symbol": row.get("symbol") or "—", "module": row.get("module"), "status": "PASS" if row.get("passed") else "REJECT", "reason": row.get("reason")} for row in decisions])
            st.dataframe(frame, use_container_width=True, hide_index=True)
        else:
            st.caption("No paper-session decision log exists yet.")
    with right:
        st.subheader("Paper connection")
        account = st.session_state.account
        if account:
            st.success(f"{account.get('status', 'Connected')} · checked {account.get('checked_at', '')}")
            if settings.gui_show_account_details:
                _metric_cards(
                    st,
                    [("Cash", _fmt_money(account.get("cash")), None)],
                    "Paper account summary",
                )
            else:
                st.caption(
                    "Privacy mode is active: balances and position details "
                    "are hidden. Only connection status and counts are shown."
                )
        else:
            st.caption("Connection is checked only when you request it. The GUI never places an order during this check.")
        _account_check(st, settings)
        st.subheader("Safe paper observation")
        if settings.paper_order_submission_enabled:
            st.warning("Disable PAPER_ORDER_SUBMISSION_ENABLED before using the GUI observation cycle.")
        elif not settings.alpaca_api_key:
            st.caption("Add paper credentials to .env to enable a one-cycle scan.")
        elif st.button("Run one disarmed scan cycle", use_container_width=True):
            try:
                completed = subprocess.run([sys.executable, "-m", "bot", "run", "--once"], cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=90, check=False)
                st.session_state.paper_cycle_output = (completed.stdout + "\n" + completed.stderr).strip()
                st.success("Observation cycle finished." if completed.returncode == 0 else "Observation cycle returned an error; review the output below.")
            except subprocess.TimeoutExpired:
                st.error("Observation cycle exceeded 90 seconds and was stopped.")
        if st.session_state.paper_cycle_output:
            st.code(st.session_state.paper_cycle_output, language="text")
        st.subheader("Real-time shadow paper")
        st.caption(
            "Uses live IEX data and local simulated fills. This mode never "
            "calls Alpaca's order-submission API."
        )
        from bot.shadow_paper import ShadowTradeStore, shadow_paper_settings

        active_shadow_settings = shadow_paper_settings(settings)
        with st.container(border=True):
            st.markdown("#### Active Shadow Protections")
            st.caption(
                "Read-only view of the protections enforced during real-time "
                "shadow testing. Historical replay presets do not apply here."
            )
            st.dataframe(
                pd.DataFrame(
                    active_shadow_protection_rows(active_shadow_settings)
                ),
                use_container_width=True,
                hide_index=True,
            )

        shadow_store = ShadowTradeStore(
            settings.output_dir
            / "shadow_paper"
            / "shadow_trades.sqlite3"
        )
        shadow_summary = shadow_store.summary()
        shadow_runner = ShadowRunner(
            settings.output_dir,
            sys.executable,
            PROJECT_ROOT,
        )
        shadow_pid = shadow_runner.active_pid()
        automatic_shadow_schedule = launch_agent_is_configured()
        _metric_cards(
            st,
            [
                ("Open shadow trades", shadow_summary["open_trades"], None),
                (
                    "Closed shadow trades",
                    shadow_summary["closed_trades"],
                    None,
                ),
                (
                    "Shadow net P/L",
                    _fmt_money(shadow_summary["net_profit"]),
                    None,
                ),
            ],
            "Shadow trade summary",
        )
        shadow_trades = list(reversed(shadow_store.all_trades()))[:10]
        if shadow_trades:
            st.caption("Most recent shadow trades")
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "symbol": trade.symbol,
                            "status": trade.status,
                            "entry time": trade.entry_time,
                            "entry": float(trade.entry_price),
                            "exit": (
                                float(trade.exit_price)
                                if trade.exit_price is not None
                                else None
                            ),
                            "P/L": (
                                float(trade.realized_pnl)
                                if trade.realized_pnl is not None
                                else None
                            ),
                            "reason": trade.exit_reason or "—",
                        }
                        for trade in shadow_trades
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.caption(
                "No shadow trades yet. Results are saved automatically "
                "once the shadow runner observes a qualifying setup."
            )

        from bot.shadow_execution_audit import (
            AlpacaSipQuoteProvider,
            ShadowExecutionAuditor,
            load_execution_audit,
            save_execution_audit,
        )

        with st.expander("Post-session SIP execution audit", expanded=False):
            st.caption(
                "Read-only validation of recorded IEX entries and exits against "
                "historical consolidated SIP quotes. The shadow trade ledger, "
                "strategy, and broker settings are never changed."
            )
            audit_disabled = (
                shadow_pid is not None
                or shadow_summary["open_trades"] > 0
                or not settings.alpaca_api_key
                or not settings.alpaca_secret_key
            )
            if st.button(
                "Run post-session SIP audit",
                use_container_width=True,
                disabled=audit_disabled,
                help=(
                    "Available after shadow observation stops and all positions "
                    "are closed. It makes historical market-data requests only."
                ),
            ):
                try:
                    with st.spinner(
                        "Comparing shadow fills with historical SIP quotes…"
                    ):
                        report = ShadowExecutionAuditor(
                            active_shadow_settings,
                            AlpacaSipQuoteProvider(settings),
                        ).audit(shadow_store.all_trades())
                        save_execution_audit(
                            report,
                            SHADOW_EXECUTION_AUDIT,
                        )
                    st.success(
                        "SIP execution audit completed. The shadow ledger was "
                        "not modified."
                    )
                except Exception as exc:
                    st.error(
                        "The SIP execution audit could not complete: "
                        f"{type(exc).__name__}: {_safe_error_text(exc)}"
                    )

            audit_report = load_execution_audit(SHADOW_EXECUTION_AUDIT)
            if audit_report:
                summary = audit_report.get("summary", {})
                _metric_cards(
                    st,
                    [
                        (
                            "Raw ledger P/L",
                            _fmt_money(summary.get("raw_net_profit")),
                            None,
                        ),
                        (
                            "Confirmed",
                            summary.get("confirmed_count", 0),
                            None,
                        ),
                        (
                            "Discrepant / unresolved",
                            (
                                f"{summary.get('discrepant_count', 0)} / "
                                f"{summary.get('unresolved_count', 0)}"
                            ),
                            None,
                        ),
                        (
                            "Estimated SIP-path P/L",
                            _fmt_money(
                                summary.get("estimated_sip_path_net_profit")
                            ),
                            (
                                "Includes confirmed raw outcomes plus "
                                "price-only reconstructions. Unresolved rows "
                                "are excluded."
                            ),
                        ),
                    ],
                    "SIP execution audit summary",
                )
                st.caption(
                    "Generated "
                    f"{audit_report.get('generated_at', '—')}; resolved estimate "
                    f"covers {summary.get('estimated_trade_count', 0)} of "
                    f"{summary.get('ledger_trade_count', 0)} closed trades. "
                    "Price-only reconstructions do not model VWAP, EMA, or "
                    "breakout-close exits."
                )
                audit_rows = execution_audit_table_rows(audit_report)
                if audit_rows:
                    st.dataframe(
                        pd.DataFrame(audit_rows),
                        use_container_width=True,
                        hide_index=True,
                    )
                st.download_button(
                    "Download SIP audit JSON",
                    data=json.dumps(audit_report, indent=2) + "\n",
                    file_name="shadow_execution_audit.json",
                    mime="application/json",
                    use_container_width=True,
                )
            elif audit_disabled and shadow_pid is not None:
                st.info(
                    "Stop or finish the active shadow session before running "
                    "the post-session audit."
                )
            else:
                st.caption(
                    "No saved SIP audit yet. Run one after a completed session."
                )
        if settings.paper_order_submission_enabled:
            st.warning(
                "Shadow mode is blocked while paper-order submission is armed."
            )
        elif shadow_pid is not None:
            st.success(
                "Continuous shadow observation is running. It will stop "
                "automatically after the trading window."
            )
        elif automatic_shadow_schedule:
            st.info(
                "Shadow observation is stopped for now. Automatic weekday "
                "startup is installed for 6:00 a.m. Mac local time."
            )
        else:
            st.info(
                "Continuous shadow observation is stopped. Starting it does "
                "not enable broker orders."
            )
        runner_start, runner_stop = st.columns(2)
        if runner_start.button(
            "Start shadow observation",
            use_container_width=True,
            disabled=(
                settings.paper_order_submission_enabled
                or shadow_pid is not None
            ),
        ):
            shadow_runner.start()
            st.rerun()
        if runner_stop.button(
            (
                "Stop shadow observation for today"
                if automatic_shadow_schedule
                else "Stop shadow observation"
            ),
            use_container_width=True,
            disabled=shadow_pid is None,
        ):
            shadow_runner.stop()
            st.rerun()
        if st.button(
            "Run one shadow-paper cycle",
            use_container_width=True,
            disabled=(
                settings.paper_order_submission_enabled
                or shadow_pid is not None
            ),
            help=(
                "A diagnostic cycle only. Use Start shadow observation for "
                "continuous forward testing."
            ),
        ):
            try:
                completed = subprocess.run(
                    [sys.executable, "-m", "bot", "shadow", "--once"],
                    cwd=PROJECT_ROOT,
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )
                st.session_state.shadow_cycle_output = (
                    completed.stdout + "\n" + completed.stderr
                ).strip()
                if completed.returncode == 0:
                    st.success(
                        "Shadow cycle finished. No broker order was placed."
                    )
                else:
                    st.error(
                        "Shadow cycle returned an error; review the output."
                    )
            except subprocess.TimeoutExpired:
                st.error("Shadow cycle exceeded 120 seconds and was stopped.")
        if st.session_state.shadow_cycle_output:
            st.code(
                st.session_state.shadow_cycle_output,
                language="text",
            )


def _bars_frame(pd, bars):
    return pd.DataFrame([{"timestamp": b.timestamp, "symbol": b.symbol, "open": float(b.open), "high": float(b.high), "low": float(b.low), "close": float(b.close), "volume": b.volume} for b in bars])


def _chart(pd, go, make_subplots, bars, trades=()):
    frame = _bars_frame(pd, bars)
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=.04, row_heights=[.76,.24])
    fig.add_trace(go.Candlestick(x=frame.timestamp, open=frame.open, high=frame.high, low=frame.low, close=frame.close, name="Price", increasing_line_color="#2a9d8f", decreasing_line_color="#c8553d"), row=1, col=1)
    colors = ["#2a9d8f" if c >= o else "#c8553d" for o,c in zip(frame.open, frame.close)]
    fig.add_trace(go.Bar(x=frame.timestamp, y=frame.volume, marker_color=colors, name="Volume"), row=2, col=1)
    for trade in trades:
        fig.add_trace(go.Scatter(x=[trade.entry_time], y=[float(trade.entry_price)], mode="markers", marker=dict(symbol="triangle-up", size=13, color="#1f6f8b"), name="Entry", text=[trade.trade_id]), row=1,col=1)
        fig.add_trace(go.Scatter(x=[trade.exit_time], y=[float(trade.exit_price)], mode="markers", marker=dict(symbol="triangle-down", size=13, color="#c8553d"), name="Exit", text=[trade.exit_reason]), row=1,col=1)
    fig.update_layout(height=650, margin=dict(l=10,r=10,t=35,b=10), hovermode="x unified", xaxis_rangeslider_visible=False, legend_orientation="h", legend_y=1.03)
    fig.update_xaxes(showgrid=True, gridcolor="rgba(128,128,128,.22)")
    fig.update_yaxes(showgrid=True, gridcolor="rgba(128,128,128,.22)")
    return fig


def _run_backtest(st, source_name: str, bars, settings: Settings) -> None:
    result = BacktestEngine(settings).run(bars)
    st.session_state.backtest_result = result
    st.session_state.backtest_bars = bars
    st.session_state.backtest_source = source_name
    ensure_private_directory(RUN_DIR)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = build_backtest_report(result)
    payload = {"run_id": stamp, "source": source_name, "config": settings.snapshot(), "result": report}
    run_path = RUN_DIR / f"{stamp}_{result.symbol}.json"
    run_path.write_text(
        json.dumps(to_json_safe(payload), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    run_path.chmod(0o600)


def _backtest(st, pd, go, make_subplots, settings: Settings) -> None:
    st.header("Backtest Lab")
    st.caption("Run one pre-screened symbol at a time, preserve the configuration, and connect every result to the evidence on the chart.")
    source, action = st.columns([2,1])
    with source:
        upload = st.file_uploader("Historical one-minute OHLCV CSV", type=["csv"], help="Required columns: timestamp, symbol, open, high, low, close, volume")
        use_sample = st.checkbox("Use included sample_bull_flag.csv", value=upload is None)
    with action:
        st.markdown("<div style='height:1.72rem'></div>", unsafe_allow_html=True)
        run = st.button("Run backtest", type="primary", use_container_width=True)
        st.caption(f"Active profile: {settings.parameter_profile}")
    if run:
        try:
            if upload is not None:
                text = upload.getvalue().decode("utf-8-sig")
                bars = load_bars_csv_stream(io.StringIO(text), settings.timezone)
                name = upload.name
            elif use_sample:
                path = PROJECT_ROOT / "examples" / "sample_bull_flag.csv"
                bars = load_bars_csv(path, settings.timezone)
                name = path.name
            else:
                st.warning("Upload a CSV or select the included sample.")
                return
            _run_backtest(st, name, bars, settings)
            st.success("Backtest complete. The run and exact configuration were archived in output/gui_backtests.")
        except Exception as exc:
            st.error(f"Backtest could not run: {_safe_error_text(exc)}")

    result, bars = st.session_state.backtest_result, st.session_state.backtest_bars
    if not result or not bars:
        st.info("Run a backtest to populate the chart, metrics, trades, and decision evidence.")
        return
    metrics = calculate_metrics(result.trades)
    items = [("Net P/L",_fmt_money(metrics["net_profit"])),("Trades",metrics["total_trades"]),("Win rate",_fmt_pct(metrics["win_rate"])),("Profit factor",f"{float(metrics['profit_factor']):.2f}" if metrics["profit_factor"] is not None else "—"),("Max drawdown",_fmt_money(metrics["max_drawdown"])),("Avg R",f"{float(metrics['average_r_multiple']):.2f}" if metrics["average_r_multiple"] is not None else "—")]
    _metric_cards(
        st,
        [(label, value, None) for label, value in items],
        "Backtest performance summary",
    )
    st.plotly_chart(_chart(pd,go,make_subplots,bars,result.trades), use_container_width=True, config={"displaylogo":False})
    tab1,tab2,tab3 = st.tabs(["Trades", "Run evidence", "Configuration snapshot"])
    with tab1:
        rows = trade_table_rows(result.trades)
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.info("No trades were taken in this run.")
    with tab2:
        st.write(f"**Setup rejections:** {result.setup_rejections}  ·  **Entry opportunities:** {result.entry_opportunities}  ·  **Ambiguous same-bar trades:** {result.ambiguous_same_bar_count}")
        for note in result.notes: st.caption("• " + note)
        if result.trades:
            trade = st.selectbox("Explain trade", result.trades, format_func=lambda t: f"{t.entry_time:%Y-%m-%d %H:%M} · {t.symbol} · {_fmt_money(t.realized_pnl)}")
            st.markdown(f"**Entry:** Triggered at {_fmt_money(trade.entry_price)} with {trade.quantity} shares.  \n**Risk plan:** Stop {_fmt_money(trade.stop_price)}, target {_fmt_money(trade.target_price)}, initial risk {_fmt_money(trade.initial_risk)}.  \n**Exit:** {trade.exit_reason} at {_fmt_money(trade.exit_price)}.  \n**Outcome:** {_fmt_money(trade.realized_pnl)} ({float(trade.r_multiple):.2f}R).")
            if trade.ambiguous_same_bar: st.warning("The bar touched an ambiguous path; the conservative stop-first policy was applied.")
    with tab3:
        st.json(settings.snapshot(), expanded=False)
        st.download_button("Download run report", json.dumps(to_json_safe(build_backtest_report(result)), indent=2), file_name=f"{result.symbol}_backtest_report.json", mime="application/json")


def _scanner(st, pd, go, make_subplots, settings: Settings) -> None:
    st.header("Scanner & Reasoning")
    st.caption("The decision audit turns raw bot events into a readable answer to: why did this symbol pass, wait, or fail?")
    rows = recent_decisions(settings.log_dir, 1000)
    symbol_rows = [r for r in rows if r.get("symbol")]
    if not symbol_rows:
        st.info("No scanner decisions have been logged yet. Run a disarmed paper observation cycle from Dashboard, or use Backtest Lab to inspect historical trades.")
    else:
        symbols = sorted({str(r["symbol"]) for r in symbol_rows})
        symbol = st.selectbox("Symbol", symbols)
        selected = [r for r in symbol_rows if r.get("symbol") == symbol]
        latest = selected[0]
        _metric_cards(
            st,
            [
                (
                    "Current action",
                    "PASS" if latest.get("passed") else "REJECT",
                    None,
                ),
                ("Last module", str(latest.get("module", "—")), None),
                ("Events", len(selected), None),
            ],
            "Scanner decision summary",
        )
        frame = pd.DataFrame([{"time":r.get("timestamp"),"check":r.get("module"),"state":"Passed" if r.get("passed") else "Failed", "reason":r.get("reason"),"observed":json.dumps(r.get("actual_values",{}), default=str),"expected":json.dumps(r.get("expected_values",{}), default=str)} for r in selected])
        st.dataframe(frame, use_container_width=True, hide_index=True)
    if st.session_state.backtest_bars:
        st.subheader("Current historical chart")
        st.plotly_chart(_chart(pd,go,make_subplots,st.session_state.backtest_bars,getattr(st.session_state.backtest_result,"trades",[])), use_container_width=True, config={"displaylogo":False})


def _historical_data(st, pd, settings: Settings) -> None:
    st.header("Historical Data")
    st.caption("Build the replay dataset while keeping captured and reconstructed history visibly separate.")
    store = HistoryStore(HISTORY_DB)
    summary = store.summary()
    cache = BarCache(HISTORY_DIR / "bars", store)
    execution_cache = BarCache(HISTORY_DIR / "bars_10s", store)
    trade_cache = TradeCache(HISTORY_DIR / "trades")
    manifest = load_workspace_manifest(HISTORICAL_WORKSPACE_MANIFEST)
    if not st.session_state.historical_workspace_restore_attempted:
        st.session_state.historical_workspace_restore_attempted = True
        restored = {}
        for value in manifest.get("paths", []):
            path = Path(value)
            if path.exists():
                bars = BarCache.read(path)
                if bars:
                    restored[bars[0].symbol] = bars
        if restored:
            st.session_state.historical_bars = restored
            restored_execution = {}
            for value in manifest.get("execution_paths", []):
                path = Path(value)
                if path.exists():
                    bars = BarCache.read(path)
                    if bars:
                        restored_execution[bars[0].symbol] = bars
            st.session_state.historical_execution_bars = restored_execution
            st.session_state.historical_missing_symbols = manifest.get("missing_symbols", [])
            st.session_state.historical_feed = manifest.get("feed")
            st.session_state.historical_workspace_paths = {
                Path(value).parent.name.upper(): value
                for value in manifest.get("paths", [])
                if Path(value).exists()
            }
            st.session_state.historical_execution_workspace_paths = {
                Path(value).parent.name.upper(): value
                for value in manifest.get("execution_paths", [])
                if Path(value).exists()
            }
            restored_trade_paths = {
                Path(value).parent.name.upper(): Path(value)
                for value in manifest.get("trade_paths", [])
                if Path(value).exists()
            }
            if not restored_trade_paths and manifest.get("feed"):
                restored_trade_paths = trade_cache.latest_paths(
                    feed=manifest["feed"], symbols=restored
                )
            st.session_state.historical_trade_workspace_paths = {
                symbol: str(path)
                for symbol, path in restored_trade_paths.items()
            }
            st.session_state.historical_validation_dates_by_symbol = {
                symbol.upper(): {
                    date.fromisoformat(value) for value in values
                }
                for symbol, values in manifest.get(
                    "validation_dates_by_symbol", {}
                ).items()
            }
            st.session_state.historical_validation_name = manifest.get(
                "validation_name"
            )
            if (
                manifest.get("validation_name")
                in VALIDATION_MANIFESTS
            ):
                st.session_state.historical_validation_selection = manifest[
                    "validation_name"
                ]
            if manifest.get("evaluation_start"):
                st.session_state.historical_evaluation_start = date.fromisoformat(manifest["evaluation_start"])
            if manifest.get("evaluation_end"):
                st.session_state.historical_evaluation_end = date.fromisoformat(manifest["evaluation_end"])
            if manifest.get("spread_percent") is not None:
                st.session_state.historical_spread_percent = float(manifest["spread_percent"])
            if manifest.get("replay_profile") in HISTORICAL_REPLAY_PROFILES:
                st.session_state.historical_replay_profile = manifest["replay_profile"]
            if (
                manifest.get("guardrail_preset")
                in REPLAY_GUARDRAIL_PRESETS
            ):
                st.session_state.historical_guardrail_preset = manifest[
                    "guardrail_preset"
                ]
    _metric_cards(
        st,
        [
            ("Snapshots", summary["snapshots"], None),
            ("Captured", summary["captured"], None),
            ("Reconstructed", summary["reconstructed"], None),
            ("Bar files", summary["data_files"], None),
            ("Replay runs", summary["replay_runs"], None),
        ],
        "Historical data summary",
    )
    st.info("Captured rows come from an actual scanner cycle. Reconstructed rows are approximations derived from historical bars and estimated spreads.")
    with st.expander(
        "Date-scoped validation manifests", expanded=False
    ):
        validation_name = st.selectbox(
            "Validation batch",
            list(VALIDATION_MANIFESTS),
            key="historical_validation_selection",
        )
        validation_targets = load_validation_targets(
            VALIDATION_MANIFESTS[validation_name]
        )
        validation_dates = target_dates_by_symbol(validation_targets)
        validation_symbols = sorted(validation_dates)
        validation_start = min(
            target.trade_date for target in validation_targets
        )
        validation_end = max(
            target.trade_date for target in validation_targets
        )
        st.caption(
            f"{len(validation_targets)} symbols across "
            f"{len({target.trade_date for target in validation_targets})} "
            "date-scoped rows. The bundled manifest is synthetic and exists "
            "only to demonstrate the file format; it is not trading research."
        )
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "date": target.trade_date,
                        "symbol": target.symbol,
                        "premarket gain": (
                            f"{target.premarket_gain_percent:.2f}%"
                        ),
                        "observed price": f"${target.observed_price:.2f}",
                        "observed volume": target.observed_volume,
                    }
                    for target in validation_targets
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )
        st.info(
            "This batch is date-scoped: each symbol is evaluated only on its "
            "listed momentum day. Minute downloads include 35 calendar days "
            "of lookback for scanner context; raw trades and ten-second bars "
            "are downloaded only for the target day."
        )
        validation_cols = st.columns(2)
        if validation_cols[0].button(
            "1. Download minute context",
            use_container_width=True,
        ):
            if not settings.alpaca_api_key or not settings.alpaca_secret_key:
                st.warning("Add Alpaca paper API credentials to .env first.")
            else:
                downloader = AlpacaHistoricalDownloader(settings, cache)
                downloaded_paths = []
                failures = []
                progress = st.progress(0, text="Downloading minute context…")
                for index, target in enumerate(validation_targets, start=1):
                    minute_start, minute_end, _, _ = (
                        validation_download_windows(
                            target, settings.timezone
                        )
                    )
                    try:
                        downloaded_paths.extend(
                            downloader.download(
                                [target.symbol],
                                minute_start,
                                minute_end,
                                feed="sip",
                                adjustment="raw",
                            )
                        )
                    except Exception as exc:
                        failures.append(
                            f"{target.symbol}: {_safe_error_text(exc)}"
                        )
                    progress.progress(
                        index / len(validation_targets),
                        text=(
                            f"Minute context {index}/"
                            f"{len(validation_targets)}"
                        ),
                    )
                selected_paths = cache.latest_paths(
                    feed="sip", symbols=validation_symbols
                )
                grouped = {
                    symbol: BarCache.read(path)
                    for symbol, path in selected_paths.items()
                }
                selected_execution_paths = execution_cache.latest_paths(
                    feed="sip", symbols=validation_symbols
                )
                selected_trade_paths = trade_cache.latest_paths(
                    feed="sip", symbols=validation_symbols
                )
                st.session_state.historical_bars = grouped
                st.session_state.historical_execution_bars = {
                    symbol: BarCache.read(path)
                    for symbol, path in selected_execution_paths.items()
                }
                st.session_state.historical_feed = "sip"
                st.session_state.historical_workspace_paths = {
                    symbol: str(path)
                    for symbol, path in selected_paths.items()
                }
                st.session_state.historical_execution_workspace_paths = {
                    symbol: str(path)
                    for symbol, path in selected_execution_paths.items()
                }
                st.session_state.historical_trade_workspace_paths = {
                    symbol: str(path)
                    for symbol, path in selected_trade_paths.items()
                }
                st.session_state.historical_missing_symbols = sorted(
                    set(validation_symbols) - set(grouped)
                )
                st.session_state.historical_validation_dates_by_symbol = (
                    validation_dates
                )
                st.session_state.historical_validation_name = validation_name
                st.session_state.historical_evaluation_start = validation_start
                st.session_state.historical_evaluation_end = validation_end
                st.session_state.historical_replay_profile = (
                    "Premarket validation"
                )
                st.session_state.historical_guardrail_preset = (
                    "Baseline — no experimental guardrails"
                )
                save_workspace_manifest(
                    HISTORICAL_WORKSPACE_MANIFEST,
                    {
                        "feed": "sip",
                        "paths": [
                            str(path) for path in selected_paths.values()
                        ],
                        "execution_paths": [
                            str(path)
                            for path in selected_execution_paths.values()
                        ],
                        "trade_paths": [
                            str(path)
                            for path in selected_trade_paths.values()
                        ],
                        "symbols": sorted(grouped),
                        "missing_symbols": (
                            st.session_state.historical_missing_symbols
                        ),
                        "evaluation_start": validation_start.isoformat(),
                        "evaluation_end": validation_end.isoformat(),
                        "validation_name": validation_name,
                        "replay_profile": "Premarket validation",
                        "guardrail_preset": (
                            "Baseline — no experimental guardrails"
                        ),
                        "validation_dates_by_symbol": {
                            symbol: sorted(
                                value.isoformat() for value in dates
                            )
                            for symbol, dates in validation_dates.items()
                        },
                    },
                )
                if failures:
                    st.warning(
                        "Minute context finished with some failures: "
                        + " | ".join(failures)
                    )
                else:
                    st.success(
                        f"Minute context is ready for "
                        f"{len(downloaded_paths)} symbols."
                    )
                st.rerun()
        if validation_cols[1].button(
            "2. Download target-day 10-second data",
            use_container_width=True,
        ):
            if not settings.alpaca_api_key or not settings.alpaca_secret_key:
                st.warning("Add Alpaca paper API credentials to .env first.")
            else:
                downloader = AlpacaHistoricalDownloader(
                    settings,
                    cache,
                    trade_cache=trade_cache,
                    execution_cache=execution_cache,
                )
                failures = []
                progress = st.progress(
                    0, text="Downloading target-day raw trades…"
                )
                for index, target in enumerate(validation_targets, start=1):
                    _, _, execution_start, execution_end = (
                        validation_download_windows(
                            target, settings.timezone
                        )
                    )
                    try:
                        downloader.download_ten_second_bars(
                            [target.symbol],
                            execution_start,
                            execution_end,
                            feed="sip",
                        )
                    except Exception as exc:
                        failures.append(
                            f"{target.symbol}: {_safe_error_text(exc)}"
                        )
                    progress.progress(
                        index / len(validation_targets),
                        text=(
                            f"Target-day execution data {index}/"
                            f"{len(validation_targets)}"
                        ),
                    )
                selected_paths = cache.latest_paths(
                    feed="sip", symbols=validation_symbols
                )
                selected_execution_paths = execution_cache.latest_paths(
                    feed="sip", symbols=validation_symbols
                )
                selected_trade_paths = trade_cache.latest_paths(
                    feed="sip", symbols=validation_symbols
                )
                grouped = {
                    symbol: BarCache.read(path)
                    for symbol, path in selected_paths.items()
                }
                st.session_state.historical_bars = grouped
                st.session_state.historical_execution_bars = {
                    symbol: BarCache.read(path)
                    for symbol, path in selected_execution_paths.items()
                }
                st.session_state.historical_feed = "sip"
                st.session_state.historical_workspace_paths = {
                    symbol: str(path)
                    for symbol, path in selected_paths.items()
                }
                st.session_state.historical_execution_workspace_paths = {
                    symbol: str(path)
                    for symbol, path in selected_execution_paths.items()
                }
                st.session_state.historical_trade_workspace_paths = {
                    symbol: str(path)
                    for symbol, path in selected_trade_paths.items()
                }
                st.session_state.historical_missing_symbols = sorted(
                    set(validation_symbols) - set(grouped)
                )
                st.session_state.historical_validation_dates_by_symbol = (
                    validation_dates
                )
                st.session_state.historical_validation_name = validation_name
                st.session_state.historical_evaluation_start = validation_start
                st.session_state.historical_evaluation_end = validation_end
                st.session_state.historical_replay_profile = (
                    "Premarket validation"
                )
                st.session_state.historical_guardrail_preset = (
                    "Baseline — no experimental guardrails"
                )
                save_workspace_manifest(
                    HISTORICAL_WORKSPACE_MANIFEST,
                    {
                        "feed": "sip",
                        "paths": [
                            str(path) for path in selected_paths.values()
                        ],
                        "execution_paths": [
                            str(path)
                            for path in selected_execution_paths.values()
                        ],
                        "trade_paths": [
                            str(path)
                            for path in selected_trade_paths.values()
                        ],
                        "symbols": sorted(grouped),
                        "missing_symbols": (
                            st.session_state.historical_missing_symbols
                        ),
                        "evaluation_start": validation_start.isoformat(),
                        "evaluation_end": validation_end.isoformat(),
                        "validation_name": validation_name,
                        "replay_profile": "Premarket validation",
                        "guardrail_preset": (
                            "Baseline — no experimental guardrails"
                        ),
                        "validation_dates_by_symbol": {
                            symbol: sorted(
                                value.isoformat() for value in dates
                            )
                            for symbol, dates in validation_dates.items()
                        },
                    },
                )
                if failures:
                    st.warning(
                        "Execution data finished with some failures: "
                        + " | ".join(failures)
                    )
                else:
                    st.success(
                        "Target-day raw trades and ten-second bars are ready."
                    )
                st.rerun()
    with st.expander("Download Alpaca historical bars", expanded=False):
        st.caption("Use SIP for strategy evaluation. Downloads are read-only market-data requests and never place orders.")
        symbols_text = st.text_area(
            "Symbols",
            value=", ".join(sorted(st.session_state.historical_bars)),
            placeholder="AAPL, TSLA, NVDA",
        )
        start_col, end_col, feed_col = st.columns(3)
        start_date = start_col.date_input("Start", value=date.today() - timedelta(days=130))
        end_date = end_col.date_input("End", value=date.today())
        feed = feed_col.selectbox("Feed", ["sip", "iex"], index=0)
        minute_col, execution_col = st.columns(2)
        if minute_col.button("Download and cache minute bars", use_container_width=True):
            symbols = [value.strip().upper() for value in symbols_text.replace("\n", ",").split(",") if value.strip()]
            if not symbols:
                st.warning("Enter at least one symbol.")
            elif not settings.alpaca_api_key or not settings.alpaca_secret_key:
                st.warning("Add Alpaca paper API credentials to .env first. Credentials are never entered or stored in the GUI.")
            else:
                try:
                    downloader = AlpacaHistoricalDownloader(settings, cache)
                    start_dt = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
                    end_dt = datetime.combine(end_date, time.max, tzinfo=timezone.utc)
                    paths = downloader.download(symbols, start_dt, end_dt, feed=feed, adjustment="raw")
                    grouped = {path.parent.name: BarCache.read(path) for path in paths}
                    merged_bars = dict(st.session_state.historical_bars)
                    merged_bars.update(grouped)
                    st.session_state.historical_bars = merged_bars
                    st.session_state.historical_validation_dates_by_symbol = {}
                    st.session_state.historical_validation_name = None
                    st.session_state.historical_feed = feed
                    merged_paths = dict(st.session_state.historical_workspace_paths)
                    merged_paths.update(
                        {path.parent.name.upper(): str(path) for path in paths}
                    )
                    st.session_state.historical_workspace_paths = merged_paths
                    downloaded = set(grouped)
                    missing = sorted(set(symbols) - downloaded)
                    st.session_state.historical_missing_symbols = missing
                    save_workspace_manifest(
                        HISTORICAL_WORKSPACE_MANIFEST,
                        {
                            **manifest,
                            "feed": feed,
                            "paths": list(merged_paths.values()),
                            "symbols": sorted(merged_bars),
                            "missing_symbols": missing,
                            "validation_name": None,
                            "validation_dates_by_symbol": {},
                            "execution_paths": list(
                                st.session_state.historical_execution_workspace_paths.values()
                            ),
                        },
                    )
                    st.success(f"Downloaded {len(paths)} symbol files using the {feed.upper()} feed.")
                    st.rerun()
                except Exception as exc:
                    st.error(
                        "Historical download failed: "
                        + _safe_error_text(exc)
                    )
        if execution_col.button(
            "Download and cache 10-second bars",
            use_container_width=True,
            help=(
                "Downloads read-only historical trades and aggregates them into "
                "completed ten-second bars. No orders are placed."
            ),
        ):
            symbols = [
                value.strip().upper()
                for value in symbols_text.replace("\n", ",").split(",")
                if value.strip()
            ]
            if not symbols:
                st.warning("Enter at least one symbol.")
            elif not settings.alpaca_api_key or not settings.alpaca_secret_key:
                st.warning("Add Alpaca paper API credentials to .env first.")
            else:
                try:
                    downloader = AlpacaHistoricalDownloader(
                        settings,
                        cache,
                        trade_cache=trade_cache,
                        execution_cache=execution_cache,
                    )
                    start_dt = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
                    end_dt = datetime.combine(end_date, time.max, tzinfo=timezone.utc)
                    trade_paths, execution_paths = downloader.download_ten_second_bars(
                        symbols, start_dt, end_dt, feed=feed
                    )
                    grouped_execution = {
                        path.parent.name.upper(): BarCache.read(path)
                        for path in execution_paths
                    }
                    merged_execution = dict(
                        st.session_state.historical_execution_bars
                    )
                    merged_execution.update(grouped_execution)
                    st.session_state.historical_execution_bars = merged_execution
                    st.session_state.historical_validation_dates_by_symbol = {}
                    st.session_state.historical_validation_name = None
                    merged_execution_paths = dict(
                        st.session_state.historical_execution_workspace_paths
                    )
                    merged_execution_paths.update(
                        {
                            path.parent.name.upper(): str(path)
                            for path in execution_paths
                        }
                    )
                    st.session_state.historical_execution_workspace_paths = (
                        merged_execution_paths
                    )
                    merged_trade_paths = dict(
                        st.session_state.historical_trade_workspace_paths
                    )
                    merged_trade_paths.update(
                        {
                            path.parent.name.upper(): str(path)
                            for path in trade_paths
                        }
                    )
                    st.session_state.historical_trade_workspace_paths = (
                        merged_trade_paths
                    )
                    save_workspace_manifest(
                        HISTORICAL_WORKSPACE_MANIFEST,
                        {
                            **manifest,
                            "feed": feed,
                            "paths": list(
                                st.session_state.historical_workspace_paths.values()
                            ),
                            "execution_paths": list(merged_execution_paths.values()),
                            "trade_paths": list(merged_trade_paths.values()),
                            "symbols": sorted(st.session_state.historical_bars),
                            "missing_symbols": st.session_state.historical_missing_symbols,
                            "validation_name": None,
                            "validation_dates_by_symbol": {},
                        },
                    )
                    st.success(
                        f"Cached raw trades and ten-second bars for "
                        f"{len(execution_paths)} symbols."
                    )
                    st.rerun()
                except Exception as exc:
                    st.error(
                        "Ten-second historical download failed: "
                        + _safe_error_text(exc)
                    )
    cached_files = cache.discover()
    if cached_files:
        with st.expander("Load cached historical bars", expanded=not bool(st.session_state.historical_bars)):
            cached_feeds = sorted({value.feed for value in cached_files})
            default_feed = manifest.get("feed")
            if default_feed not in cached_feeds:
                newest_cached = max(cached_files, key=lambda value: value.modified_at)
                default_feed = newest_cached.feed
            feed_index = cached_feeds.index(default_feed)
            cached_feed = st.selectbox("Cached feed", cached_feeds, index=feed_index)
            available_symbols = sorted({value.symbol for value in cached_files if value.feed == cached_feed})
            manifest_symbols = [value for value in manifest.get("symbols", []) if value in available_symbols]
            selected_symbols = st.multiselect(
                "Cached symbols",
                available_symbols,
                default=manifest_symbols or available_symbols,
                help="The newest cached file for each selected symbol will be loaded.",
            )
            if st.button("Load selected cached bars", disabled=not selected_symbols, use_container_width=True):
                try:
                    selected_paths = cache.latest_paths(feed=cached_feed, symbols=selected_symbols)
                    grouped = {symbol: BarCache.read(path) for symbol, path in selected_paths.items()}
                    selected_execution_paths = execution_cache.latest_paths(
                        feed=cached_feed, symbols=selected_symbols
                    )
                    selected_trade_paths = trade_cache.latest_paths(
                        feed=cached_feed, symbols=selected_symbols
                    )
                    grouped_execution = {
                        symbol: BarCache.read(path)
                        for symbol, path in selected_execution_paths.items()
                    }
                    st.session_state.historical_bars = grouped
                    st.session_state.historical_execution_bars = grouped_execution
                    st.session_state.historical_validation_dates_by_symbol = {}
                    st.session_state.historical_validation_name = None
                    st.session_state.historical_missing_symbols = sorted(set(selected_symbols) - set(grouped))
                    st.session_state.historical_feed = cached_feed
                    st.session_state.historical_workspace_paths = {
                        symbol: str(path) for symbol, path in selected_paths.items()
                    }
                    st.session_state.historical_execution_workspace_paths = {
                        symbol: str(path)
                        for symbol, path in selected_execution_paths.items()
                    }
                    st.session_state.historical_trade_workspace_paths = {
                        symbol: str(path)
                        for symbol, path in selected_trade_paths.items()
                    }
                    st.session_state.portfolio_result = None
                    save_workspace_manifest(
                        HISTORICAL_WORKSPACE_MANIFEST,
                        {
                            "feed": cached_feed,
                            "paths": [str(path) for path in selected_paths.values()],
                            "execution_paths": [
                                str(path) for path in selected_execution_paths.values()
                            ],
                            "trade_paths": [
                                str(path) for path in selected_trade_paths.values()
                            ],
                            "symbols": sorted(grouped),
                            "missing_symbols": st.session_state.historical_missing_symbols,
                        },
                    )
                    st.rerun()
                except Exception as exc:
                    st.error(
                        "Cached-bar load failed: " + _safe_error_text(exc)
                    )
    uploads = st.file_uploader("Import one or more OHLCV CSV files", type=["csv"], accept_multiple_files=True, help="Each file may contain one or many symbols. Required columns: timestamp, symbol, open, high, low, close, volume.")
    if st.button("Load files into historical workspace", disabled=not uploads, use_container_width=True):
        try:
            grouped = {}
            for upload in uploads:
                bars = load_bars_csv_stream(io.StringIO(upload.getvalue().decode("utf-8-sig")), settings.timezone)
                for bar in bars: grouped.setdefault(bar.symbol, []).append(bar)
            imported_paths = {}
            for symbol in grouped:
                grouped[symbol].sort(key=lambda bar: bar.timestamp)
                imported_paths[symbol] = cache.write(grouped[symbol], feed="imported", adjustment="raw")
            st.session_state.historical_bars = grouped
            st.session_state.historical_execution_bars = {}
            st.session_state.historical_validation_dates_by_symbol = {}
            st.session_state.historical_validation_name = None
            st.session_state.historical_feed = "imported"
            st.session_state.historical_workspace_paths = {
                symbol: str(path) for symbol, path in imported_paths.items()
            }
            st.session_state.historical_execution_workspace_paths = {}
            st.session_state.historical_trade_workspace_paths = {}
            st.session_state.historical_missing_symbols = []
            save_workspace_manifest(
                HISTORICAL_WORKSPACE_MANIFEST,
                {
                    "feed": "imported",
                    "paths": [str(path) for path in imported_paths.values()],
                    "symbols": sorted(grouped),
                    "missing_symbols": [],
                },
            )
            st.success(f"Loaded and cached {sum(len(v) for v in grouped.values()):,} bars across {len(grouped)} symbols.")
        except Exception as exc:
            st.error(f"Import failed: {_safe_error_text(exc)}")
    bars_by_symbol = st.session_state.historical_bars
    if bars_by_symbol:
        st.subheader("Loaded workspace")
        active_validation_dates = {
            symbol: set(dates)
            for symbol, dates in (
                st.session_state.historical_validation_dates_by_symbol.items()
            )
            if symbol in bars_by_symbol
        }
        if active_validation_dates:
            st.success(
                "Date-scoped validation is active. Each symbol will be scored "
                "only on its preselected momentum day."
            )
        if st.session_state.historical_missing_symbols:
            st.warning("No bars were returned for: " + ", ".join(st.session_state.historical_missing_symbols))
        execution_bars_by_symbol = st.session_state.historical_execution_bars
        rows=[
            {
                "symbol":symbol,
                "minute bars":len(bars),
                "10-second bars":len(execution_bars_by_symbol.get(symbol, [])),
                "start":bars[0].timestamp,
                "end":bars[-1].timestamp,
            }
            for symbol,bars in sorted(bars_by_symbol.items())
        ]
        st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True)
        if execution_bars_by_symbol:
            st.success(
                "Ten-second execution data loaded for: "
                + ", ".join(sorted(execution_bars_by_symbol))
                + ". One-minute bars remain the setup/context layer."
            )
            trade_symbols = sorted(
                symbol
                for symbol, path in (
                    st.session_state.historical_trade_workspace_paths.items()
                )
                if Path(path).exists()
            )
            if trade_symbols:
                st.success(
                    "Exact raw-trade ordering available for: "
                    + ", ".join(trade_symbols)
                    + "."
                )
            else:
                st.warning(
                    "Ten-second bars are loaded, but raw-trade files were not found. "
                    "Replay will retain conservative same-bar assumptions."
                )
        else:
            st.info(
                "No ten-second execution data is loaded yet. Replay will use "
                "one-minute bars until historical trades are downloaded."
            )
        available_dates = [
            bar.timestamp.astimezone(ZoneInfo(settings.timezone)).date()
            for bars in bars_by_symbol.values() for bar in bars
        ]
        minimum_date, maximum_date = min(available_dates), max(available_dates)
        saved_evaluation_start = st.session_state.get("historical_evaluation_start", minimum_date)
        saved_evaluation_end = st.session_state.get("historical_evaluation_end", maximum_date)
        st.session_state.historical_evaluation_start = min(max(saved_evaluation_start, minimum_date), maximum_date)
        st.session_state.historical_evaluation_end = min(max(saved_evaluation_end, minimum_date), maximum_date)
        eval_cols = st.columns(2)
        evaluation_start_date = eval_cols[0].date_input(
            "Evaluation start",
            min_value=minimum_date,
            max_value=maximum_date,
            help="The first trading date to score. Earlier downloaded bars remain available only as lookback history.",
            key="historical_evaluation_start",
        )
        evaluation_end_date = eval_cols[1].date_input(
            "Evaluation end",
            min_value=minimum_date,
            max_value=maximum_date,
            help="The last trading date to score.",
            key="historical_evaluation_end",
        )
        local_zone = ZoneInfo(settings.timezone)
        evaluation_start = datetime.combine(evaluation_start_date, time.min, tzinfo=local_zone).astimezone(timezone.utc)
        evaluation_end = datetime.combine(evaluation_end_date, time.max, tzinfo=local_zone).astimezone(timezone.utc)
        if evaluation_end_date < evaluation_start_date:
            st.error("Evaluation end must be on or after evaluation start.")
        replay_profile = st.selectbox(
            "Historical replay profile",
            list(HISTORICAL_REPLAY_PROFILES),
            key="historical_replay_profile",
            help=(
                "Premarket validation affects historical reconstruction and replay only. "
                "It does not change the live or paper-trading configuration."
            ),
        )
        replay_settings = historical_replay_settings(settings, replay_profile)
        if replay_profile == "Premarket validation":
            st.info(
                "Replay-only validation begins at 7:00 a.m. Eastern and allows the "
                "larger pullbacks and indicator extensions observed in the Ross sample. "
                "The one-minute flagpole requirements remain strict; loaded ten-second "
                "data also enables experimental micro-pullback and reversal/reclaim setups."
            )
        elif replay_profile == "Regular-hours paper validation":
            st.info(
                "This profile keeps the relaxed validation setup rules but "
                "scores entries only from 9:30–11:30 a.m. Eastern, matching "
                "the hours supported by the bot's protected Alpaca bracket orders."
            )
        guardrail_preset = st.selectbox(
            "Replay-only experimental guardrails",
            list(REPLAY_GUARDRAIL_PRESETS),
            key="historical_guardrail_preset",
            help=(
                "These controls affect historical portfolio replay only. They do "
                "not change live or paper-trading behavior."
            ),
        )
        replay_guardrails = REPLAY_GUARDRAIL_PRESETS[guardrail_preset]
        if replay_guardrails.enabled:
            st.info(
                "This run will test the selected guardrails against the same "
                "candidate and market-data history. Filtered opportunities will "
                "appear under Portfolio rejections."
            )
        spread = st.number_input("Estimated reconstructed spread (%)",min_value=0.01,max_value=5.0,value=float(st.session_state.get("historical_spread_percent", 0.50)),step=0.05,key="historical_spread_percent")
        workspace_feed = st.session_state.historical_feed or manifest.get("feed", "sip")
        workspace_paths = {
            symbol: Path(value)
            for symbol, value in st.session_state.historical_workspace_paths.items()
            if symbol in bars_by_symbol and Path(value).exists()
        }
        workspace_manifest = {
            "feed": workspace_feed,
            "paths": [str(path) for path in workspace_paths.values()],
            "execution_paths": list(
                st.session_state.historical_execution_workspace_paths.values()
            ),
            "trade_paths": list(
                st.session_state.historical_trade_workspace_paths.values()
            ),
            "symbols": sorted(bars_by_symbol),
            "missing_symbols": st.session_state.historical_missing_symbols,
            "evaluation_start": evaluation_start_date.isoformat(),
            "evaluation_end": evaluation_end_date.isoformat(),
            "spread_percent": spread,
            "replay_profile": replay_profile,
            "guardrail_preset": guardrail_preset,
            "validation_name": (
                st.session_state.historical_validation_name
                if active_validation_dates
                else None
            ),
            "validation_dates_by_symbol": {
                symbol: sorted(value.isoformat() for value in dates)
                for symbol, dates in active_validation_dates.items()
            },
        }
        decision_source = execution_bars_by_symbol or bars_by_symbol
        decision_minutes = sorted({
            bar.timestamp for bars in decision_source.values() for bar in bars
            if evaluation_start <= bar.timestamp <= evaluation_end
            and replay_settings.trade_window_start
            <= bar.timestamp.astimezone(local_zone).time()
            <= replay_settings.trade_window_end
        })
        a,b,c=st.columns(3)
        invalid_evaluation_window = evaluation_end_date < evaluation_start_date
        if a.button("Reconstruct candidate history",use_container_width=True,disabled=invalid_evaluation_window):
            try:
                count=CandidateReconstructor(replay_settings,store).reconstruct(
                    bars_by_symbol,
                    execution_bars_by_symbol=execution_bars_by_symbol or None,
                    decision_minutes=decision_minutes,
                    decision_dates_by_symbol=(
                        active_validation_dates or None
                    ),
                    estimated_spread_percent=Decimal(str(spread/100)),
                )
                save_workspace_manifest(HISTORICAL_WORKSPACE_MANIFEST, workspace_manifest)
                st.success(f"Recorded {count:,} reconstructed point-in-time scanner rows."); st.rerun()
            except Exception as exc:
                st.error(f"Reconstruction failed: {_safe_error_text(exc)}")
        if b.button(
            "Run portfolio replay",
            use_container_width=True,
            disabled=invalid_evaluation_window,
            help=(
                "Uses the existing reconstructed candidate history. Choose this "
                "when comparing replay-only guardrail presets."
            ),
        ):
            try:
                result=PortfolioReplayEngine(replay_settings,store).run(
                    bars_by_symbol,
                    execution_bars_by_symbol=execution_bars_by_symbol or None,
                    execution_trade_paths_by_symbol={
                        symbol: Path(path)
                        for symbol, path in (
                            st.session_state.historical_trade_workspace_paths.items()
                        )
                        if symbol in bars_by_symbol and Path(path).exists()
                    } or None,
                    source="reconstructed",
                    feed=workspace_feed,
                    evaluation_start=evaluation_start,
                    evaluation_end=evaluation_end,
                    evaluation_dates_by_symbol=(
                        active_validation_dates or None
                    ),
                    guardrails=replay_guardrails,
                )
                st.session_state.portfolio_result=result
                st.session_state.portfolio_result_profile=replay_profile
                st.session_state.portfolio_result_guardrail_preset=guardrail_preset
                save_workspace_manifest(HISTORICAL_WORKSPACE_MANIFEST, workspace_manifest)
                st.success(
                    f"Completed the replay using existing candidate history: "
                    f"{len(result.accepted_trades)} accepted trades and "
                    f"{len(result.rejected_trades)} rejections."
                )
                st.rerun()
            except Exception as exc:
                st.error(f"Portfolio replay failed: {_safe_error_text(exc)}")
        if c.button("Reconstruct and run portfolio replay",use_container_width=True,disabled=invalid_evaluation_window):
            try:
                reconstructed_count = CandidateReconstructor(
                    replay_settings, store
                ).reconstruct(
                    bars_by_symbol,
                    execution_bars_by_symbol=execution_bars_by_symbol or None,
                    decision_minutes=decision_minutes,
                    decision_dates_by_symbol=(
                        active_validation_dates or None
                    ),
                    estimated_spread_percent=Decimal(str(spread / 100)),
                )
                result=PortfolioReplayEngine(replay_settings,store).run(
                    bars_by_symbol,
                    execution_bars_by_symbol=execution_bars_by_symbol or None,
                    execution_trade_paths_by_symbol={
                        symbol: Path(path)
                        for symbol, path in (
                            st.session_state.historical_trade_workspace_paths.items()
                        )
                        if symbol in bars_by_symbol and Path(path).exists()
                    } or None,
                    source="reconstructed",
                    feed=workspace_feed,
                    evaluation_start=evaluation_start,
                    evaluation_end=evaluation_end,
                    evaluation_dates_by_symbol=(
                        active_validation_dates or None
                    ),
                    guardrails=replay_guardrails,
                )
                st.session_state.portfolio_result=result
                st.session_state.portfolio_result_profile=replay_profile
                st.session_state.portfolio_result_guardrail_preset=guardrail_preset
                save_workspace_manifest(HISTORICAL_WORKSPACE_MANIFEST, workspace_manifest)
                st.success(
                    f"Reconstructed {reconstructed_count:,} scanner rows and completed "
                    f"the replay: {len(result.accepted_trades)} accepted trades, "
                    f"{len(result.rejected_trades)} portfolio-gate rejections."
                )
                st.rerun()
            except Exception as exc:
                st.error(f"Portfolio replay failed: {_safe_error_text(exc)}")
        diagnostic_rows = store.candidates(
            source="reconstructed",
            passed_only=False,
            symbols=bars_by_symbol,
            start_time=evaluation_start,
            end_time=evaluation_end,
        )
        if active_validation_dates:
            diagnostic_rows = [
                row
                for row in diagnostic_rows
                if datetime.fromisoformat(row["timestamp"])
                .astimezone(local_zone)
                .date()
                in active_validation_dates.get(row["symbol"], set())
            ]
        if diagnostic_rows:
            passed_symbols = sorted({row["symbol"] for row in diagnostic_rows if row["passed"]})
            st.caption("Scanner-passing symbols in this evaluation: " + (", ".join(passed_symbols) if passed_symbols else "none"))
            failures = [row for row in diagnostic_rows if not row["passed"]]
            if failures:
                failure_counts: dict[tuple[str, str], int] = {}
                for row in failures:
                    key = (row["symbol"], row["reason"])
                    failure_counts[key] = failure_counts.get(key, 0) + 1
                failure_table = [
                    {"symbol": symbol, "reason": reason, "rows": count}
                    for (symbol, reason), count in sorted(failure_counts.items())
                ]
                with st.expander("Why reconstructed candidates failed the scanner"):
                    st.dataframe(pd.DataFrame(failure_table), use_container_width=True, hide_index=True)
    result=st.session_state.portfolio_result
    if result:
        st.subheader("Latest portfolio replay")
        if st.session_state.portfolio_result_profile:
            st.caption(
                "Replay profile: "
                + st.session_state.portfolio_result_profile
                + ". Historical profiles do not alter live or paper settings."
            )
        if st.session_state.portfolio_result_guardrail_preset:
            st.caption(
                "Replay guardrails: "
                + st.session_state.portfolio_result_guardrail_preset
                + ". These do not alter live or paper settings."
            )
        _metric_cards(
            st,
            [
                ("Passing symbols", len(result.symbols), None),
                ("Scoped candidates", result.candidate_count, None),
                ("Accepted trades", len(result.accepted_trades), None),
                (
                    "Net P/L",
                    _fmt_money(result.metrics.get("net_profit")),
                    None,
                ),
            ],
            "Portfolio replay summary",
        )
        tabs=st.tabs(["Accepted trades","Strategy diagnostics","Portfolio rejections","Notes"])
        with tabs[0]:
            if result.accepted_trades: st.dataframe(pd.DataFrame(trade_table_rows(result.accepted_trades)),use_container_width=True,hide_index=True)
            else: st.info("No trades passed the strategy and portfolio gates.")
        with tabs[1]:
            if result.strategy_diagnostics:
                diagnostic_table = []
                for row in sorted(result.strategy_diagnostics, key=lambda value: (value["symbol"], -value["count"], value["reason"])):
                    diagnostic_table.append(
                        {
                            "symbol": row["symbol"],
                            "stage": row["stage"],
                            "reason": row["reason"],
                            "count": row["count"],
                            "representative timestamp": row["representative_timestamp"],
                            "actual": json.dumps(to_json_safe(row["actual_values"]), sort_keys=True),
                            "expected": json.dumps(to_json_safe(row["expected_values"]), sort_keys=True),
                        }
                    )
                st.dataframe(pd.DataFrame(diagnostic_table),use_container_width=True,hide_index=True)
                st.caption("Representative timestamp is the latest occurrence of each rejection type within the scoped replay.")
            else: st.info("No strategy-level setup rejections were recorded.")
        with tabs[2]:
            if result.rejected_trades: st.dataframe(pd.DataFrame(result.rejected_trades),use_container_width=True,hide_index=True)
            else: st.caption("No portfolio-gate rejections.")
        with tabs[3]:
            for note in result.notes: st.caption("• "+note)


def _trades(st, pd, settings: Settings) -> None:
    st.header("Trades")
    tabs = st.tabs(["Current session", "Saved backtest runs", "Paper positions"])
    with tabs[0]:
        result = st.session_state.backtest_result
        if result and result.trades:
            st.dataframe(pd.DataFrame(trade_table_rows(result.trades)), use_container_width=True, hide_index=True)
        else: st.info("No trades are available in the current GUI session.")
    with tabs[1]:
        files = sorted(RUN_DIR.glob("*.json"), reverse=True) if RUN_DIR.exists() else []
        if not files: st.info("Saved GUI backtests will appear here.")
        else:
            chosen = st.selectbox("Archived run", files, format_func=lambda p:p.stem)
            payload = json.loads(chosen.read_text(encoding="utf-8"))
            metrics = payload["result"]["metrics"]
            _metric_cards(
                st,
                [
                    ("Symbol", payload["result"]["symbol"], None),
                    ("Trades", metrics["total_trades"], None),
                    (
                        "Net P/L",
                        _fmt_money(metrics["net_profit"]),
                        None,
                    ),
                    ("Win rate", _fmt_pct(metrics["win_rate"]), None),
                ],
                "Archived backtest summary",
            )
            st.dataframe(pd.DataFrame(payload["result"]["trades"]), use_container_width=True, hide_index=True)
    with tabs[2]:
        account = st.session_state.account
        if not settings.gui_show_account_details:
            st.info(
                "Position details are hidden by default. Set "
                "GUI_SHOW_ACCOUNT_DETAILS=true only for a private local session."
            )
        elif account and account.get("positions"):
            st.dataframe(
                pd.DataFrame(account["positions"]),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info(
                "Check the Alpaca paper account from Dashboard to load current "
                "positions. This view is read-only."
            )


def _config_input(st, name: str, value: Any):
    label = name.replace("_", " ").title()
    key = "cfg_" + name
    if isinstance(value, bool): return st.checkbox(label, value=value, key=key)
    if isinstance(value, Decimal): return Decimal(str(st.number_input(label, value=float(value), format="%.6f", key=key)))
    if isinstance(value, time): return st.time_input(label, value=value, key=key)
    if isinstance(value, int): return int(st.number_input(label, value=value, step=1, key=key))
    if value is None:
        raw = st.text_input(label, value="", placeholder="Optional — leave blank for none", key=key)
        return None if not raw.strip() else raw.strip()
    if name == "same_bar_stop_target_policy": return st.selectbox(label,["conservative_stop_first"],key=key)
    return st.text_input(label, value=str(value), key=key)


def _configuration(st, pd, settings: Settings) -> None:
    st.header("Configuration")
    st.caption("Tune approved parameters, validate the complete configuration, and save reproducible profiles. Credentials and live-trading safety fields are never editable here.")
    profiles = list_profiles(PROFILE_DIR)
    a,b,c = st.columns([2,1,1])
    with a:
        selected = st.selectbox("Saved profile", [None]+profiles, format_func=lambda p:"— Select a profile —" if p is None else p.stem)
    with b:
        if st.button("Load profile", use_container_width=True, disabled=selected is None):
            try:
                st.session_state.gui_settings = load_profile(selected, load_settings())
                for key in list(st.session_state):
                    if key.startswith("cfg_"): del st.session_state[key]
                st.success("Profile loaded."); st.rerun()
            except Exception as exc:
                st.error(_safe_error_text(exc))
    with c:
        if st.button("Reset defaults", use_container_width=True):
            st.session_state.gui_settings = load_settings()
            for key in list(st.session_state):
                if key.startswith("cfg_"): del st.session_state[key]
            st.rerun()
    edited: dict[str,Any] = {}
    with st.form("config_form"):
        for group,names in CONFIG_GROUPS.items():
            with st.expander(group, expanded=group in {"Account & risk","Scanner","Backtest"}):
                cols=st.columns(2)
                for index,name in enumerate(names):
                    with cols[index%2]: edited[name]=_config_input(st,name,getattr(settings,name))
        apply = st.form_submit_button("Validate and apply to this GUI session", type="primary", use_container_width=True)
    if apply:
        try:
            candidate=settings_from_mapping(settings,edited)
            validate_settings(candidate)
            st.session_state.gui_settings=candidate
            st.success("Configuration is valid and active for new GUI backtests."); st.rerun()
        except Exception as exc:
            st.error(_safe_error_text(exc))
    st.subheader("Save current profile")
    name_col,button_col=st.columns([3,1]); profile_name=name_col.text_input("Profile name",value=settings.parameter_profile,label_visibility="collapsed")
    if button_col.button("Save profile",use_container_width=True):
        try: path=save_profile(PROFILE_DIR,profile_name,settings); st.success(f"Saved {path.name}")
        except Exception as exc:
            st.error(_safe_error_text(exc))
    differences=config_diff(Settings(),settings)
    st.subheader(f"Changes from code defaults ({len(differences)})")
    if differences: st.dataframe(pd.DataFrame(differences),use_container_width=True,hide_index=True)
    else: st.caption("This GUI session matches the code defaults.")


def _logs(st, pd, settings: Settings) -> None:
    st.header("Logs")
    st.caption("Filter previous-run evidence without losing access to the canonical files.")
    options = [settings.log_dir / name for name in ["decision_audit.jsonl","config_change_history.jsonl","order_events.jsonl","position_events.jsonl"]]
    options += [settings.log_dir / name for name in ["runtime.log","errors.log"]]
    existing=[p for p in options if p.exists()]
    if not existing:
        st.info("No log files exist yet. Run a backtest or a disarmed paper observation cycle first."); return
    chosen=st.selectbox("Log file",existing,format_func=lambda p:p.name)
    if chosen.suffix==".jsonl":
        rows=read_jsonl(chosen,2000)
        severities=sorted({str(r.get("severity","unknown")) for r in rows})
        selected=st.multiselect("Severity",severities,default=severities)
        query=st.text_input("Search")
        filtered=[r for r in rows if str(r.get("severity","unknown")) in selected and (not query or query.lower() in json.dumps(r,default=str).lower())]
        st.caption(f"Showing {len(filtered)} of {len(rows)} recent events")
        st.dataframe(pd.DataFrame(filtered[::-1]),use_container_width=True,hide_index=True)
        content="\n".join(json.dumps({k:v for k,v in row.items() if k!="_line"},default=str) for row in filtered)+"\n"
    else:
        content = str(
            redact_sensitive_fields(
                chosen.read_text(encoding="utf-8", errors="replace")
            )
        )
        query=st.text_input("Search")
        lines=[line for line in content.splitlines() if not query or query.lower() in line.lower()]
        st.code("\n".join(lines[-1000:]),language="text")
    st.download_button("Download filtered log",content,file_name=chosen.name,mime="text/plain")


def render_app() -> None:
    st,pd,go,make_subplots=_imports()
    _style(st); _init_state(st)
    settings:Settings=st.session_state.gui_settings
    _brand_hero(st)
    _status_header(st,settings)
    page=_sidebar(st)
    if page=="Dashboard": _dashboard(st,pd,settings)
    elif page=="Backtest Lab": _backtest(st,pd,go,make_subplots,settings)
    elif page=="Historical Data": _historical_data(st,pd,settings)
    elif page=="Scanner & Reasoning": _scanner(st,pd,go,make_subplots,settings)
    elif page=="Trades": _trades(st,pd,settings)
    elif page=="Configuration": _configuration(st,pd,settings)
    else: _logs(st,pd,settings)


def main() -> int:
    """Launch the Streamlit app from the installed console script."""
    from streamlit.web import cli as stcli
    sys.argv = [
        "streamlit",
        "run",
        str(Path(__file__).resolve()),
        "--server.headless=true",
        "--server.address=127.0.0.1",
    ]
    return int(stcli.main())


if __name__ == "__main__":
    render_app()
