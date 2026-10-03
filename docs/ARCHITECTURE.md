# Architecture

## Purpose and Scope

Momentum Bot Laboratory is a local, educational research application for one
momentum-strategy family. It supports historical simulation, real-time shadow
observation with locally simulated fills, and separately interlocked Alpaca
paper-account execution. It does not support live brokerage trading.

This document describes implemented structure and trust boundaries. It does not
reconstruct the unavailable rationale behind legacy decision references
`D-002` through `D-013`.

## System Context

```text
Historical/synthetic inputs ──> backtest and replay engines ──> private reports

Alpaca market data ──> scanner/strategy/risk ──> shadow simulated fills
          │                       │                       │
          │                       └─> diagnostics         └─> private ledger/events
          │
          └─> explicitly armed paper path ──> Alpaca paper broker only

Private runtime evidence ──read-only─> diagnostic/research tools
Reviewed public code ──one-way promotion─> separate private runtime
```

All model, market, file, broker, and external-service data is treated as
untrusted at its boundary. Runtime evidence is local and excluded from Git.

## Primary Interfaces

- `python -m bot` routes command-line validation, connection checks, paper
  polling, shadow observation, scheduling, diagnostics, and backtesting through
  `bot/cli.py`.
- `bot/gui.py` provides the local Streamlit dashboard and research workflows.
- `launcher/automatic_shadow.command` and `bot/shadow_schedule.py` support a
  user-scoped macOS LaunchAgent for automatic shadow observation.
- `scripts/` contains public-release auditing plus bounded research, import,
  replay, and checkpoint utilities.

## Component Boundaries

### Configuration and safety

- `bot/config.py` loads versioned settings, validates ranges and invariants, and
  rejects non-paper configuration.
- `.env.example` documents safe public defaults. Real `.env` files are ignored.
- `bot/security.py` applies user-only permissions to sensitive local artifacts
  where supported.
- `.streamlit/config.toml` keeps the dashboard loopback-bound with CORS and XSRF
  protections enabled.

### Domain and decision logic

- `bot/models.py` defines bars, quotes, setups, plans, orders, positions, and
  diagnostic structures.
- `bot/scanner.py` applies market-candidate filters.
- `bot/strategy.py` detects supported pullback setups and entry triggers.
- `bot/risk_manager.py` sizes positions and applies projected risk gates.
- `bot/order_state.py`, `bot/execution.py`, and `bot/exits.py` manage order,
  fill, protection, reconciliation, and exit behavior.
- `bot/diagnostics.py` preserves decision/data timestamps and blocks lookahead.

Core strategy and risk logic operate on project domain types rather than
directly importing Alpaca SDK classes.

### Orchestration and external adapters

- `bot/app.py` coordinates paper-account polling, scanning, setup evaluation,
  risk approval, execution, position monitoring, and reconciliation.
- `bot/broker.py` defines the broker boundary.
- `bot/alpaca_adapters.py` implements market-data access and a paper broker. Its
  trading client is constructed with `paper=True`.
- `bot/session.py` holds local in-process session and risk state.

Paper order submission has a separate setting that defaults to disabled. Shadow
mode refuses to run when that interlock is armed and uses local simulated fills
instead of broker orders.

### Historical and research paths

- `bot/backtest.py`, `bot/portfolio_backtest.py`, and `bot/reconstruction.py`
  implement chronological simulations and explicitly bounded reconstruction.
- `bot/historical_data.py`, `bot/history_store.py`, and `bot/validation_set.py`
  manage local research inputs, caches, manifests, and provenance.
- `bot/shadow_execution_audit.py` compares recorded shadow fills with mature SIP
  evidence without changing the ledger.
- `bot/confirmed_stop_diagnostic.py` evaluates the predeclared CS-001 diagnostic
  from immutable/read-only sources.
- `bot/reentry_observed_screen.py` and `bot/reentry_forward_checkpoint.py`
  preserve the one-entry experiment's retrospective and fixed-forward gates.

Research reports remain private, local artifacts. Reconstructions are estimates
and cannot reproduce every intervening strategy decision.

### Persistence and observability

- `bot/event_log.py` writes append-only local JSONL events and derived CSV data.
- Shadow observation maintains an ignored local SQLite ledger and private event
  artifacts under the configured output directory.
- Source fingerprinting, query-only access, WAL checks, and pre/post-read
  verification protect read-only diagnostic boundaries where implemented.
- `bot/gui_support.py` reads local status and evidence for presentation without
  making the GUI an authorization boundary.

## Trust and Data Boundaries

### Public repository

Contains public-safe source, tests, documentation, synthetic examples,
screenshots, and branding. `scripts/audit_public_release.py` rejects prohibited
paths, likely secrets, absolute user paths, unsafe defaults, and unapproved
image locations.

### Private runtime

May contain credentials, local configuration, broker/account metadata, logs,
ledgers, market observations, caches, reports, and private research. These are
not repository context and must not be copied into the public checkout.

### Alpaca

Market-data calls may be used by paper and shadow workflows. Brokerage actions
are restricted to an explicitly armed Alpaca paper client. Connectivity and
paper-account state are external and must be checked rather than assumed.

### Local dashboard and scheduler

The dashboard is intended for loopback-only use. The LaunchAgent is an external
user-level system integration and its installed or loaded state is not proven by
repository files.

## Safety Properties

- Live-mode configuration is rejected.
- The Alpaca trading adapter is hard-wired to `paper=True`.
- Paper-order submission is separately interlocked and disabled by default.
- Shadow observation does not submit broker orders.
- Existing broker orders or positions halt new entries for manual review.
- Risk reservations, partial-fill protection, reconciliation, and conservative
  ambiguous-bar handling are covered by tests.
- Credentials and sensitive runtime artifacts are ignored and audited out of
  the public tree.
- The dashboard hides account detail by default and remains loopback-bound.

These controls support the documented paper-only boundary; they are not a claim
that the application is secure, profitable, or ready for live trading.

## Validation Boundary

Canonical checks are routed from `AGENTS.md` and implemented by README and CI.
The standard suite validates configuration, unit and integration behavior,
public-release constraints, and dependencies. Tests use synthetic or temporary
fixtures and do not establish current private-runtime or market behavior.

## Known Architecture Gaps

- No authoritative definitions or rationale are available for legacy decisions
  `D-002` through `D-013`.
- Runtime evidence and scheduler state live outside this public repository, so
  repository state must keep their current status explicit as unknown unless
  verified in the current task.
- Dependencies use bounded version ranges without a committed lockfile.
- Hosted CI validates Python 3.12; packaging and README also claim Python 3.11
  support.
