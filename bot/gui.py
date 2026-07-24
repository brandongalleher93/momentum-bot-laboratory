"""Streamlit + Plotly command center for the momentum bot MVP.

Run with: streamlit run bot/gui.py
"""

from __future__ import annotations

import csv
import io
import json
import subprocess
import sys
from dataclasses import fields, replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

# Streamlit executes this file with bot/ as the script directory. Ensure the
# project package remains importable without requiring an editable install.
_PROJECT_DIRECTORY = Path(__file__).resolve().parent.parent
if str(_PROJECT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(_PROJECT_DIRECTORY))

from bot.backtest import BacktestEngine, load_bars_csv, load_bars_csv_stream
from bot.config import PROJECT_ROOT, Settings, load_settings, validate_settings
from bot.event_log import to_json_safe
from bot.gui_support import (
    PROTECTED_FIELDS,
    config_diff,
    list_profiles,
    load_profile,
    read_jsonl,
    recent_decisions,
    save_profile,
    settings_from_mapping,
    trade_table_rows,
)
from bot.history_store import HistoryStore
from bot.historical_data import AlpacaHistoricalDownloader, BarCache
from bot.portfolio_backtest import PortfolioReplayEngine
from bot.reconstruction import CandidateReconstructor
from bot.review import build_backtest_report, calculate_metrics


PROFILE_DIR = PROJECT_ROOT / "output" / "gui_profiles"
RUN_DIR = PROJECT_ROOT / "output" / "gui_backtests"
HISTORY_DIR = PROJECT_ROOT / "output" / "history"
HISTORY_DB = HISTORY_DIR / "history.sqlite3"

CONFIG_GROUPS = {
    "Identity": ["version", "parameter_profile", "decision_register_version"],
    "Session": ["timezone", "trade_window_start", "trade_window_end", "primary_timeframe_minutes", "poll_seconds"],
    "Account & risk": ["account_equity_assumption", "max_risk_per_trade", "max_daily_loss", "max_position_value", "max_open_positions", "max_active_entry_orders", "max_trades_per_day", "max_consecutive_losses", "cooldown_after_loss_minutes", "daily_equity_drawdown_limit"],
    "Scanner": ["scanner_top", "min_price", "max_price", "min_percent_gain", "min_rvol", "min_day_volume", "preferred_float_max", "max_spread_percent", "near_high_of_day_percent"],
    "Flagpole": ["ema_period", "recent_volume_lookback_candles", "recent_range_lookback_candles", "strong_up_move_min_percent", "strong_up_move_max_candles", "min_flagpole_green_candles", "flagpole_volume_ratio_min"],
    "Pullback": ["pullback_candles_min", "pullback_candles_max", "allow_four_candle_pullback", "record_four_candle_counterfactual", "setup_expiration_candles", "small_consolidation_body_to_range_max", "small_consolidation_range_to_flagpole_avg_max", "require_nonincreasing_pullback_highs", "allow_equal_pullback_highs", "preferred_pullback_depth", "hard_max_pullback_depth", "pullback_volume_ratio_max", "large_upper_wick_ratio", "doji_body_to_range_max", "shooting_star_upper_wick_to_range_min"],
    "Entry": ["entry_trigger_buffer_ticks", "entry_limit_offset_ticks", "max_entry_chase_ticks", "entry_order_timeout_seconds"],
    "Exit & plan": ["max_extension_above_ema_percent", "max_extension_above_vwap_percent", "max_allowed_stop_distance", "min_reward_to_risk", "target_r_multiple", "exit_on_vwap_loss", "exit_on_ema_loss", "exit_on_breakout_level_fail", "flat_by_end_of_day"],
    "Execution": ["partial_fill_protection_timeout_seconds", "periodic_reconciliation_seconds"],
    "Backtest": ["same_bar_stop_target_policy", "backtest_entry_slippage_bps", "backtest_exit_slippage_bps", "commission_per_trade"],
}


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


def _init_state(st) -> None:
    if "gui_settings" not in st.session_state:
        st.session_state.gui_settings = load_settings()
    st.session_state.setdefault("backtest_result", None)
    st.session_state.setdefault("backtest_bars", None)
    st.session_state.setdefault("backtest_source", None)
    st.session_state.setdefault("account", None)
    st.session_state.setdefault("paper_cycle_output", None)
    st.session_state.setdefault("historical_bars", {})
    st.session_state.setdefault("historical_missing_symbols", [])
    st.session_state.setdefault("portfolio_result", None)


def _style(st) -> None:
    st.set_page_config(page_title="Momentum Bot Laboratory", page_icon="📈", layout="wide", initial_sidebar_state="expanded")
    st.markdown("""
    <style>
    :root { --navy:#12263a; --blue:#1f6f8b; --teal:#2a9d8f; --amber:#e9c46a; --red:#c8553d; }
    .stApp { background: #f6f8fb; }
    [data-testid="stSidebar"] { background: #12263a; }
    [data-testid="stSidebar"] * { color: #f7fafc; }
    .hero {padding:.35rem 0 .8rem 0}.hero h1{font-size:2rem;margin:0;color:#12263a}.hero p{margin:.2rem 0;color:#52606d}
    .safety {border-left:5px solid #2a9d8f;background:#e8f5f2;padding:.7rem 1rem;border-radius:.3rem;margin:.25rem 0 1rem}
    .warning {border-left-color:#c8553d;background:#fff0ed}
    div[data-testid="stMetric"] {background:#fff;border:1px solid #dce3ea;padding:.7rem;border-radius:.55rem;box-shadow:0 1px 2px rgba(18,38,58,.04)}
    div[data-testid="stDataFrame"] {border:1px solid #dce3ea;border-radius:.4rem}
    .smallcaps {font-size:.72rem;letter-spacing:.08em;text-transform:uppercase;color:#627386;font-weight:700}
    </style>""", unsafe_allow_html=True)


def _status_header(st, settings: Settings) -> None:
    account = st.session_state.account or {}
    result = st.session_state.backtest_result
    metrics = calculate_metrics(result.trades) if result else {}
    cols = st.columns(7)
    cols[0].metric("Mode", "PAPER ONLY")
    cols[1].metric("Order safety", "ARMED" if settings.paper_order_submission_enabled else "DISARMED")
    cols[2].metric("Equity", _fmt_money(account.get("equity", settings.account_equity_assumption)))
    cols[3].metric("Buying power", _fmt_money(account.get("buying_power")))
    cols[4].metric("Open positions", str(account.get("open_positions", 0)))
    cols[5].metric("Latest test P/L", _fmt_money(metrics.get("net_profit")))
    cols[6].metric("Profile", settings.parameter_profile)
    css = "safety warning" if settings.paper_order_submission_enabled else "safety"
    message = "Paper order submission is ARMED. The application still rejects live trading." if settings.paper_order_submission_enabled else "Paper order submission is disarmed. Backtesting and read-only monitoring are safe to use."
    st.markdown(f'<div class="{css}"><b>Safety state:</b> {message}</div>', unsafe_allow_html=True)


def _sidebar(st) -> str:
    st.sidebar.markdown("## Momentum Lab")
    st.sidebar.caption("Observe · Understand · Tune · Measure")
    page = st.sidebar.radio("Workspace", ["Dashboard", "Backtest Lab", "Historical Data", "Scanner & Reasoning", "Trades", "Configuration", "Logs"], label_visibility="collapsed")
    st.sidebar.divider()
    st.sidebar.markdown("**North Star**")
    st.sidebar.caption("Every trade—or rejected trade—should be explainable within 30 seconds.")
    st.sidebar.caption("MVP · Paper trading only")
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
                "equity": account.equity,
                "cash": account.cash,
                "buying_power": account.buying_power,
                "open_positions": len(positions),
                "positions": [to_json_safe(value) for value in positions],
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
            st.success("Connected to the Alpaca paper account. No order was placed.")
            st.rerun()
        except Exception as exc:
            st.error(f"Paper-account check failed: {exc}")


def _dashboard(st, pd, settings: Settings) -> None:
    st.header("Dashboard")
    st.caption("Mission control: system health, testing state, and the shortest path to the next useful experiment.")
    left, right = st.columns([1.4, 1])
    with left:
        st.subheader("Current experiment")
        result = st.session_state.backtest_result
        if result:
            metrics = calculate_metrics(result.trades)
            a,b,c,d = st.columns(4)
            a.metric("Symbol", result.symbol)
            b.metric("Trades", metrics["total_trades"])
            c.metric("Win rate", _fmt_pct(metrics["win_rate"]))
            d.metric("Net P/L", _fmt_money(metrics["net_profit"]))
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
            st.metric("Cash", _fmt_money(account.get("cash")))
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
    fig.update_layout(height=650, margin=dict(l=10,r=10,t=35,b=10), paper_bgcolor="white", plot_bgcolor="white", hovermode="x unified", xaxis_rangeslider_visible=False, legend_orientation="h", legend_y=1.03)
    fig.update_xaxes(showgrid=True, gridcolor="#edf1f5")
    fig.update_yaxes(showgrid=True, gridcolor="#edf1f5")
    return fig


def _run_backtest(st, source_name: str, bars, settings: Settings) -> None:
    result = BacktestEngine(settings).run(bars)
    st.session_state.backtest_result = result
    st.session_state.backtest_bars = bars
    st.session_state.backtest_source = source_name
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report = build_backtest_report(result)
    payload = {"run_id": stamp, "source": source_name, "config": settings.snapshot(), "result": report}
    (RUN_DIR / f"{stamp}_{result.symbol}.json").write_text(json.dumps(to_json_safe(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")


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
            st.error(f"Backtest could not run: {exc}")

    result, bars = st.session_state.backtest_result, st.session_state.backtest_bars
    if not result or not bars:
        st.info("Run a backtest to populate the chart, metrics, trades, and decision evidence.")
        return
    metrics = calculate_metrics(result.trades)
    cols = st.columns(6)
    items = [("Net P/L",_fmt_money(metrics["net_profit"])),("Trades",metrics["total_trades"]),("Win rate",_fmt_pct(metrics["win_rate"])),("Profit factor",f"{float(metrics['profit_factor']):.2f}" if metrics["profit_factor"] is not None else "—"),("Max drawdown",_fmt_money(metrics["max_drawdown"])),("Avg R",f"{float(metrics['average_r_multiple']):.2f}" if metrics["average_r_multiple"] is not None else "—")]
    for col,(label,value) in zip(cols,items): col.metric(label,value)
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
        a,b,c = st.columns(3)
        a.metric("Current action", "PASS" if latest.get("passed") else "REJECT")
        b.metric("Last module", str(latest.get("module","—")))
        c.metric("Events", len(selected))
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
    cols = st.columns(5)
    for col, (label, value) in zip(cols, [("Snapshots",summary["snapshots"]),("Captured",summary["captured"]),("Reconstructed",summary["reconstructed"]),("Bar files",summary["data_files"]),("Replay runs",summary["replay_runs"])]):
        col.metric(label, value)
    st.info("Captured rows come from an actual scanner cycle. Reconstructed rows are approximations derived from historical bars and estimated spreads.")
    with st.expander("Download Alpaca historical bars", expanded=False):
        st.caption("Use SIP for strategy evaluation. Downloads are read-only market-data requests and never place orders.")
        symbols_text = st.text_area("Symbols", placeholder="AAPL, TSLA, NVDA")
        start_col, end_col, feed_col = st.columns(3)
        start_date = start_col.date_input("Start", value=date.today() - timedelta(days=130))
        end_date = end_col.date_input("End", value=date.today())
        feed = feed_col.selectbox("Feed", ["sip", "iex"], index=0)
        if st.button("Download and cache minute bars", use_container_width=True):
            symbols = [value.strip().upper() for value in symbols_text.replace("\n", ",").split(",") if value.strip()]
            if not symbols:
                st.warning("Enter at least one symbol.")
            elif not settings.alpaca_api_key or not settings.alpaca_secret_key:
                st.warning("Add Alpaca paper API credentials to .env first. Credentials are never entered or stored in the GUI.")
            else:
                try:
                    cache = BarCache(HISTORY_DIR / "bars", store)
                    downloader = AlpacaHistoricalDownloader(settings, cache)
                    start_dt = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
                    end_dt = datetime.combine(end_date, time.max, tzinfo=timezone.utc)
                    paths = downloader.download(symbols, start_dt, end_dt, feed=feed, adjustment="raw")
                    grouped = {path.parent.name: BarCache.read(path) for path in paths}
                    st.session_state.historical_bars = grouped
                    downloaded = set(grouped)
                    missing = sorted(set(symbols) - downloaded)
                    st.session_state.historical_missing_symbols = missing
                    st.success(f"Downloaded {len(paths)} symbol files using the {feed.upper()} feed.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Historical download failed: {exc}")
    uploads = st.file_uploader("Import one or more OHLCV CSV files", type=["csv"], accept_multiple_files=True, help="Each file may contain one or many symbols. Required columns: timestamp, symbol, open, high, low, close, volume.")
    if st.button("Load files into historical workspace", disabled=not uploads, use_container_width=True):
        try:
            grouped = {}
            cache = BarCache(HISTORY_DIR / "bars", store)
            for upload in uploads:
                bars = load_bars_csv_stream(io.StringIO(upload.getvalue().decode("utf-8-sig")), settings.timezone)
                for bar in bars: grouped.setdefault(bar.symbol, []).append(bar)
            for symbol in grouped:
                grouped[symbol].sort(key=lambda bar: bar.timestamp)
                cache.write(grouped[symbol], feed="imported", adjustment="raw")
            st.session_state.historical_bars = grouped
            st.success(f"Loaded and cached {sum(len(v) for v in grouped.values()):,} bars across {len(grouped)} symbols.")
        except Exception as exc: st.error(f"Import failed: {exc}")
    bars_by_symbol = st.session_state.historical_bars
    if bars_by_symbol:
        st.subheader("Loaded workspace")
        if st.session_state.historical_missing_symbols:
            st.warning("No bars were returned for: " + ", ".join(st.session_state.historical_missing_symbols))
        rows=[{"symbol":symbol,"bars":len(bars),"start":bars[0].timestamp,"end":bars[-1].timestamp} for symbol,bars in sorted(bars_by_symbol.items())]
        st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True)
        available_dates = [
            bar.timestamp.astimezone(ZoneInfo(settings.timezone)).date()
            for bars in bars_by_symbol.values() for bar in bars
        ]
        eval_cols = st.columns(2)
        evaluation_start_date = eval_cols[0].date_input(
            "Evaluation start",
            value=min(available_dates),
            min_value=min(available_dates),
            max_value=max(available_dates),
            help="The first trading date to score. Earlier downloaded bars remain available only as lookback history.",
        )
        evaluation_end_date = eval_cols[1].date_input(
            "Evaluation end",
            value=max(available_dates),
            min_value=min(available_dates),
            max_value=max(available_dates),
            help="The last trading date to score.",
        )
        local_zone = ZoneInfo(settings.timezone)
        evaluation_start = datetime.combine(evaluation_start_date, time.min, tzinfo=local_zone).astimezone(timezone.utc)
        evaluation_end = datetime.combine(evaluation_end_date, time.max, tzinfo=local_zone).astimezone(timezone.utc)
        if evaluation_end_date < evaluation_start_date:
            st.error("Evaluation end must be on or after evaluation start.")
        spread = st.number_input("Estimated reconstructed spread (%)",min_value=0.01,max_value=5.0,value=0.50,step=0.05)
        a,b=st.columns(2)
        invalid_evaluation_window = evaluation_end_date < evaluation_start_date
        if a.button("Reconstruct candidate history",use_container_width=True,disabled=invalid_evaluation_window):
            try:
                decision_minutes = sorted({
                    bar.timestamp for bars in bars_by_symbol.values() for bar in bars
                    if evaluation_start <= bar.timestamp <= evaluation_end
                    and settings.trade_window_start <= bar.timestamp.astimezone(local_zone).time() <= settings.trade_window_end
                })
                count=CandidateReconstructor(settings,store).reconstruct(
                    bars_by_symbol,
                    decision_minutes=decision_minutes,
                    estimated_spread_percent=Decimal(str(spread/100)),
                )
                st.success(f"Recorded {count:,} reconstructed point-in-time scanner rows."); st.rerun()
            except Exception as exc: st.error(f"Reconstruction failed: {exc}")
        if b.button("Run portfolio replay",use_container_width=True,disabled=invalid_evaluation_window):
            try:
                result=PortfolioReplayEngine(settings,store).run(
                    bars_by_symbol,
                    source="reconstructed",
                    feed="imported",
                    evaluation_start=evaluation_start,
                    evaluation_end=evaluation_end,
                )
                st.session_state.portfolio_result=result
                st.success(f"Portfolio replay complete: {len(result.accepted_trades)} accepted trades, {len(result.rejected_trades)} portfolio-gate rejections."); st.rerun()
            except Exception as exc: st.error(f"Portfolio replay failed: {exc}")
        diagnostic_rows = store.candidates(
            source="reconstructed",
            passed_only=False,
            symbols=bars_by_symbol,
            start_time=evaluation_start,
            end_time=evaluation_end,
        )
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
        a,b,c,d=st.columns(4); a.metric("Passing symbols",len(result.symbols)); b.metric("Scoped candidates",result.candidate_count); c.metric("Accepted trades",len(result.accepted_trades)); d.metric("Net P/L",_fmt_money(result.metrics.get("net_profit")))
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
            a,b,c,d=st.columns(4); a.metric("Symbol",payload["result"]["symbol"]); b.metric("Trades",metrics["total_trades"]); c.metric("Net P/L",_fmt_money(metrics["net_profit"])); d.metric("Win rate",_fmt_pct(metrics["win_rate"]))
            st.dataframe(pd.DataFrame(payload["result"]["trades"]), use_container_width=True, hide_index=True)
    with tabs[2]:
        account = st.session_state.account
        if account and account.get("positions"): st.dataframe(pd.DataFrame(account["positions"]), use_container_width=True, hide_index=True)
        else: st.info("Check the Alpaca paper account from Dashboard to load current positions. This view is read-only.")


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
            except Exception as exc: st.error(str(exc))
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
        except Exception as exc: st.error(str(exc))
    st.subheader("Save current profile")
    name_col,button_col=st.columns([3,1]); profile_name=name_col.text_input("Profile name",value=settings.parameter_profile,label_visibility="collapsed")
    if button_col.button("Save profile",use_container_width=True):
        try: path=save_profile(PROFILE_DIR,profile_name,settings); st.success(f"Saved {path.name}")
        except Exception as exc: st.error(str(exc))
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
        content=chosen.read_text(encoding="utf-8",errors="replace")
        query=st.text_input("Search")
        lines=[line for line in content.splitlines() if not query or query.lower() in line.lower()]
        st.code("\n".join(lines[-1000:]),language="text")
    st.download_button("Download filtered log",content,file_name=chosen.name,mime="text/plain")


def render_app() -> None:
    st,pd,go,make_subplots=_imports()
    _style(st); _init_state(st)
    settings:Settings=st.session_state.gui_settings
    st.markdown('<div class="hero"><h1>Momentum Bot Laboratory</h1><p>Observe, understand, tune, and measure the paper-trading MVP.</p></div>',unsafe_allow_html=True)
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
    sys.argv=["streamlit","run",str(Path(__file__).resolve()),"--server.headless=true"]
    return int(stcli.main())


if __name__ == "__main__":
    render_app()
