# Momentum Trading Bot MVP

A paper-trading-only Python application for testing one momentum strategy:
**Bull Flag / First Pullback**.

The bot scans market gainers, builds setups from completed one-minute candles,
watches for an intrabar breakout, sizes the position against a daily risk budget,
and submits protected bracket orders to an Alpaca paper account. Every important
decision produces a structured diagnostic record so results can be traced back to
the configuration and decision register that created them.

The implementation follows
`Trading Bot Doc Garage/Momentum_Bot_MVP_Pseudocode_v1_2.docx`.

> This is educational software under active development. It is not financial
> advice, does not claim profitability, and cannot submit live orders.

## How the application fits together

```text
Alpaca market data
        |
        v
Market scanner -----> rejected setup diagnostics
        |
        v
Completed-bar Bull Flag detector
        |
        v
Intrabar breakout trigger
        |
        v
Trade plan ---> projected daily-risk gate
        |                  |
        |                  +--> rejection diagnostic
        v
Alpaca paper bracket order
        |
        v
Order/fill state machine ---> JSONL event history
        |
        v
Broker stop/target + completed-candle indicator exits
```

The core strategy does not import Alpaca. It operates on the domain objects in
`bot/models.py`, which makes the same decisions testable in live paper trading and
historical simulations.

## Safety behavior

- `ALPACA_PAPER` must be `true`.
- `ALLOW_LIVE_TRADING` must be `false`.
- Paper submission is disarmed by default.
- Existing broker orders or positions halt new entries for manual reconciliation.
- Accepted orders are not counted as positions until fill events arrive.
- Pending orders reserve daily risk.
- Partial fills cancel the remainder and require separate OCO protection.
- If partial-fill protection fails, the adapter requests a flatten and halts entries.
- The default backtest resolves unknowable stop/target order as stop-first.

There are two separate controls intentionally:

```env
ALPACA_PAPER=true
PAPER_ORDER_SUBMISSION_ENABLED=false
```

Keep paper submission disabled while learning, validating data, and reviewing logs.

## Project map

```text
bot/
  config.py            Versioned settings and safety validation
  models.py            Bars, quotes, setups, plans, orders, and positions
  diagnostics.py       Structured results and future-data guard
  event_log.py         Canonical JSONL and flattened CSV helpers
  indicators.py        VWAP, 9 EMA, candle metrics, and extensions
  scanner.py           Price, gain, RVOL, volume, and spread filters
  strategy.py          Bull Flag / First Pullback and intrabar trigger
  risk_manager.py      Position sizing and projected daily-risk gate
  order_state.py       Idempotent broker order lifecycle
  broker.py            Broker-independent interface
  alpaca_adapters.py   Alpaca paper broker and market-data adapters
  execution.py         Risk reservations, submission, and fill handling
  exits.py             Completed-candle VWAP/EMA/breakout exits
  session.py           Local session state
  app.py               Polling paper-trading orchestration
  backtest.py          Conservative single-symbol historical engine
  review.py            Metrics and report generation
  cli.py               Command-line interface
tests/                  Automated behavior checks
examples/               Sample historical input
```

## Setup

Python 3.9 or newer is supported.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Add **paper-account** keys to `.env`. Never commit `.env`.

Validate the local configuration without connecting:

```bash
python -m bot validate
```

Verify Alpaca connectivity without placing an order:

```bash
python -m bot check
```

## Run the tests

The core tests use the Python standard library and do not need Alpaca credentials:

```bash
python -m unittest discover -s tests -v
```

The test suite covers configuration safety, timestamp causality, indicators,
scanner filters, Bull Flag detection, intrabar entries, risk sizing, pending-risk
reservations, partial fills, nested logs, and ambiguous backtest candles.

## Run the sample backtest

```bash
python -m bot backtest examples/sample_bull_flag.csv
```

The input schema is:

```text
timestamp,symbol,open,high,low,close,volume
```

Timestamps should describe completed bars and include an offset. A timezone-naive
timestamp is interpreted using `America/New_York`.

Outputs are written to:

```text
output/backtest_summary.json
output/trades.csv
```

The current backtest accepts one already-screened symbol per invocation. That is a
deliberate boundary: point-in-time universe membership, delisted symbols, splits,
halts, and scanner history require a separately audited dataset before portfolio-
level results would be trustworthy.

## Run the GUI command center

Install the project dependencies, then launch the Streamlit dashboard:

```bash
source .venv/bin/activate
streamlit run bot/gui.py
```

The GUI includes:

- a persistent paper-mode and safety-status header
- a backtest lab with CSV upload, the included sample, Plotly candlesticks,
  trade markers, performance metrics, and archived run reports
- scanner and decision-reasoning views backed by the structured audit log
- current and archived trades
- validated, named configuration profiles for reproducible experiments
- searchable and downloadable logs
- an explicit read-only Alpaca paper-account check

GUI backtest runs and profiles are stored under `output/`. The browser interface
does not host the trading engine, enable live trading, or silently connect to an
account. Paper order submission remains controlled by the existing `.env` safety
flag.

## Historical scanner replay

Every live paper scanner cycle now records its complete point-in-time universe in
`output/history/history.sqlite3`. Rows are labeled `captured`; candidates rebuilt
from historical bars are labeled `reconstructed`, so the two evidence qualities
cannot be confused.

Historical minute bars are cached as Parquet files under `output/history/bars/`.
The history subsystem includes:

- `HistoryStore` for scanner snapshots, data provenance, and replay runs
- `BarCache` and `AlpacaHistoricalDownloader` for paginated SDK downloads and
  local SIP/IEX bar caching
- `CandidateReconstructor` for explicitly approximate historical candidate lists
- `PortfolioReplayEngine` for chronological portfolio gates across symbols

Use raw corporate-action adjustment for downloaded data and keep one feed per
experiment. IEX remains suitable for software validation; SIP is the intended
feed for strategy evaluation.

## Run one paper polling cycle

With paper submission still disabled, this gathers data, evaluates candidates, and
logs decisions without submitting orders:

```bash
python -m bot run --once
```

After reviewing tests, connectivity, configuration, and logs, paper submission can
be armed explicitly in `.env`:

```env
PAPER_ORDER_SUBMISSION_ENABLED=true
```

Then run one cycle again before considering a continuous process:

```bash
python -m bot run --once
```

Continuous paper polling:

```bash
python -m bot run
```

## Logs and traceability

Detailed nested events use JSON Lines as the source of truth:

```text
logs/decision_audit.jsonl
logs/order_events.jsonl
logs/config_change_history.jsonl
logs/runtime.log
logs/errors.log
```

Each diagnostic includes its config version, parameter profile, decision-register
version, decision time, and newest data timestamp. If the newest data is later than
the decision, the diagnostic becomes a critical lookahead rejection.

## Important current limitations

- Paper trading has not been validated with this machine's credentials yet.
- Startup intentionally halts entries instead of reconstructing pre-existing trades.
- Float is unavailable from the current Alpaca adapter and is logged as a preference
  warning rather than a hard rejection.
- The live loop polls REST endpoints; streaming can be added after the polling state
  machine has been paper-tested.
- Backtests use OHLCV bars and conservative assumptions, not exact exchange event order.
- Placeholder parameters must be validated on unseen dates and paper sessions.

These are boundaries to test and improve—not details to hide.
