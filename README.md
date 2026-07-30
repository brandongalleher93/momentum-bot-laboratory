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

Historical minute bars are cached under `output/history/bars/`. Raw historical
trades and derived ten-second bars are cached under `output/history/trades/` and
`output/history/bars_10s/`.
The history subsystem includes:

- `HistoryStore` for scanner snapshots, data provenance, and replay runs
- `BarCache`, `TradeCache`, and `AlpacaHistoricalDownloader` for paginated SDK
  downloads and local SIP/IEX caching
- deterministic aggregation of historical trades into completed ten-second bars
- `CandidateReconstructor` for explicitly approximate historical candidate lists
- `PortfolioReplayEngine` for one-minute setup context, optional ten-second
  breakout triggering, and chronological portfolio gates across symbols

Use raw corporate-action adjustment for downloaded data and keep one feed per
experiment. IEX remains suitable for software validation; SIP is the intended
feed for strategy evaluation.

For live shadow testing before 9:30 a.m. Eastern, the scanner does not use
Alpaca's market-movers endpoint because that endpoint retains the previous
session's movers until the regular-market open. Instead, it refreshes a cached
universe of active Nasdaq, NYSE, and AMEX securities once per minute, discovers
gappers from 15-minute-delayed consolidated SIP snapshots, and computes volume
and time-aligned RVOL from SIP history ending 16 minutes before the decision.
Current IEX quotes and trades remain the execution source. This hybrid approach
provides broader free-plan premarket discovery while explicitly retaining the
15-minute discovery delay as a known limitation.

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

## Run real-time shadow paper

Shadow mode uses the live market-data feed but records entries and exits only in
a local SQLite ledger. It has no broker object and refuses to start whenever
paper order submission is enabled. It applies the frozen premarket validation
profile without changing the regular paper-bot settings.

Use **Start shadow observation** on the Dashboard for continuous forward
testing; **Stop shadow observation** ends it safely. A single diagnostic cycle
can also be run from the Dashboard or a terminal:

```bash
python -m bot shadow --once
```

For continuous observation during the configured 7:00–11:30 a.m. Eastern
window:

```bash
python -m bot shadow
```

Trades survive a restart in
`output/shadow_paper/shadow_trades.sqlite3`; detailed cycle, entry, exit, and
stale-quote events are written to `output/shadow_paper/events.jsonl`. Shadow
fills use the observed ask for entries and bid for exits. Entry quotes older
than 10 seconds are rejected; exit quotes older than 30 seconds or not newer
than the entry quote are ignored. Invalid symbol quotes are logged and isolated
without aborting the full cycle. While a position is open, monitoring takes
priority and slow universe refreshes are skipped. Exit events retain observed
stop slippage, realized loss, planned risk, and any risk overrun rather than
assuming an unrealistically perfect stop fill.

The current account has IEX real-time access, not SIP real-time access. Shadow
results therefore test forward behavior and execution plumbing; they are not
directly comparable to SIP historical replay performance.

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

- The paper account connection has been verified, but broker order submission
  remains intentionally disabled while shadow testing gathers forward evidence.
- Startup intentionally halts entries instead of reconstructing pre-existing trades.
- Float is unavailable from the current Alpaca adapter and is logged as a preference
  warning rather than a hard rejection.
- The live loop polls REST endpoints; streaming can be added after the polling state
  machine has been paper-tested.
- Backtests use OHLCV bars and conservative assumptions, not exact exchange event order.
- Historical replay includes experimental completed-ten-second micro-pullback
  and reversal/reclaim detectors behind the reconstructed scanner gate. Their
  thresholds and trade-frequency behavior still require out-of-sample validation.
- Placeholder parameters must be validated on unseen dates and paper sessions.

These are boundaries to test and improve—not details to hide.
