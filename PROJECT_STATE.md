# Project State

## Current Milestone

- ID: `M-005`
- Name: Collect reproducible forward evidence
- Status: `IMPLEMENTED — PROSPECTIVE ACCEPTANCE PENDING`
- Evidence qualification: local synthetic validation does not establish that the
  separate private runtime is collecting the new evidence. M-004 remains
  unresolved under its original fixed checkpoint; its gates are not replaced.

## Current Objective

Prepare exact-input recording and baseline reproduction so existing background
shadow collection can support a later strategy review. Preserve strategy rules,
parameters, broker interlocks, scheduler, launcher, and original evidence.

## Current Work

Implemented collection records normalized market inputs, cycle clocks,
initial state, decisions, source/runtime versions, and bounded private journals.
Completed segments reproduce through the same shadow engine with no network
client. Health checks distinguish partial capture, errors, and full-session
coverage. Earlier scanner-availability, fill-agreement, and portfolio-risk
corrections are preserved. Existing unrelated research work remains untouched.

Validation: 156 tests pass, including exact entry/exit reproduction, restart,
CLI integration, evidence corruption, storage failure, and health checks.
Configuration validation, public-tree/history audits, and whitespace checks
pass. The history audit recognizes only the exact previously approved image
blobs at their legacy case-sensitive paths.
The requirements dependency audit reports no known vulnerabilities in its
resolved packages. No dependency or scheduler change is included.

Private runtime promotion is recorded in its own source manifest; prospective
acceptance is still required. No new strategy
experiment or parameter change is authorized by these diagnostic results.

## Last Completed

PR #4 added the forward-checkpoint readiness evaluator and merged into `main` at
`60a46f31272018ad6168b2effcebb203be3c768b`. The corresponding hosted CI run
passed its dependency audit, public-tree audit, and 115-test suite.

## Next Action

Verify the first full prospective session before declaring M-005 complete.
Private source promotion follows its ownership rules and source manifest;
preserve configuration, RCS context, evidence, and existing scheduling.
Predeclare any later strategy experiment separately; historical uncaptured
inputs remain a known limit.

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

2026-11-22 — exact-input collection and same-engine baseline reproduction
implemented; prospective acceptance remains pending and private source promotion
is governed by its own manifest.
