# Project State

## Current Milestone

- ID: `M-004`
- Name: Fixed forward one-entry-per-symbol/day checkpoint
- Status: `VALIDATING`
- Evidence qualification: the public repository establishes that the protocol
  and readiness evaluator are implemented. It does not establish the current
  maturity, readiness, or outcome of private-runtime evidence.

## Current Objective

Evaluate the frozen one-entry-per-symbol/Eastern-trading-day candidate at its
predeclared 50-trade forward checkpoint without changing scheduled strategy or
broker-order behavior.

## Current Work

No source-code change is recorded as in progress. The public repository contains
the checkpoint protocol and evaluator. Current evidence collection, audit
maturity, replay coverage, and checkpoint outcome in the separate private
runtime are unknown and must not be inferred.

## Last Completed

PR #4 added the forward-checkpoint readiness evaluator and merged into `main` at
`60a46f31272018ad6168b2effcebb203be3c768b`. The corresponding hosted CI run
passed its dependency audit, public-tree audit, and 115-test suite.

## Next Action

When the user explicitly authorizes inspection of the separate private runtime,
run the read-only checkpoint evaluator against its current evidence without
copying source data into this repository. Promote only a reviewed, public-safe
aggregate status or result. Without that authority or evidence, ask the user for
direction rather than inferring progress.

## Blockers and Unknowns

- Current private-runtime evidence maturity, readiness, and outcome are unknown.
- The authoritative definitions and rationale for legacy decision references
  `D-002` through `D-013` are unavailable.
- The CS-001 confirmed-stop diagnostic is implemented, but its validation work
  is deferred and unresolved.

## Active Decisions and Constraints

- The project remains educational, alpha, and paper-only. Live brokerage
  trading is unsupported.
- Paper-order submission remains independently disarmed by default.
- This public repository is authoritative for public code, documentation,
  tests, and assets. Reviewed public revisions may be promoted into the private
  runtime; private credentials, configuration, logs, ledgers, caches, reports,
  and research do not flow back.
- SIP reconstruction remains an estimate and is not a replacement ledger.
- Exploratory research does not establish profitability or authorize strategy,
  risk, trading-window, paper-order, or live-order changes.
- The one-entry-per-symbol/day rule remains a research candidate, not scheduled
  runtime behavior.

## Active Risks

- Crossing the public/private boundary could expose sensitive runtime or account
  information.
- Paper, shadow, reconstructed, and replay results could be misrepresented as
  live-safety or profitability evidence.
- Public project state can become stale because the active research evidence is
  maintained outside this repository.
- Missing legacy decision definitions can tempt later readers to infer rationale
  from code comments or current behavior.
- Dependency resolution uses version ranges without a committed lockfile.

## Authoritative Roadmap

- `docs/ROADMAP.md`

## Authoritative Context

- `README.md` — project purpose, supported scope, safety, setup, validation, and
  public/private boundary.
- `SECURITY.md` — vulnerability reporting and sensitive local-data boundary.
- `docs/ARCHITECTURE.md` — current implementation, integration, persistence, and
  trust boundaries.
- `REENTRY_EXPERIMENT_PLAN.md` — active re-entry experiment contract and fixed
  forward-checkpoint gates.
- `CONFIRMED_STOP_DIAGNOSTIC_PLAN.md` — implemented CS-001 diagnostic and its
  deferred validation protocol.
- `SHADOW_TESTING_REVIEW.md` — historical 52-trade checkpoint evidence and past
  decisions.

## Last Updated

2026-10-03 — initialized during the approved RCS v1.0.0 migration.
