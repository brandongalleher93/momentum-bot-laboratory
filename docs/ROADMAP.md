# Roadmap

This is the canonical milestone index for Momentum Bot Laboratory. Detailed
research evidence and protocols remain in their linked source documents. Status
describes tracked public-repository knowledge; it does not imply unverified
private-runtime progress.

The milestone IDs below were assigned during the RCS migration. They organize
existing work and do not claim to be historical identifiers.

## M-001: Establish the paper-only momentum laboratory

- Status: `COMPLETED`
- Objective: provide an educational momentum-strategy application with
  backtesting, Alpaca paper-account integration, risk controls, diagnostics, and
  no live-trading path.
- Dependencies: none; this is the foundation milestone.
- Deliverables:
  - paper-only configuration and broker adapter;
  - scanner, strategy, risk, execution, exit, and reconciliation modules;
  - CLI, local dashboard, tests, and structured local evidence;
  - public-release and sensitive-data protections.
- Exit criteria:
  - implemented paper-only application and test coverage;
  - live-mode configuration rejected;
  - public/private data boundary documented and audited.
- Authoritative context: `README.md`, `SECURITY.md`, `docs/ARCHITECTURE.md`.

## M-002: Review the 52-trade shadow checkpoint

- Status: `COMPLETED`
- Objective: preserve a public-safe structured review of the first 52 shadow
  trades while separating raw IEX ledger outcomes from mature SIP evidence and
  reconstructed estimates.
- Dependencies: `M-001` paper-only shadow and evidence paths.
- Deliverables:
  - aggregate checkpoint and cohort comparison;
  - execution-quality and exit-path findings;
  - explicit decision not to claim an edge or change parameters from the
    sample;
  - operational LaunchAgent identity migration follow-up.
- Exit criteria:
  - 52-trade review documented with evidence limits;
  - parameter-change and live-trading boundaries recorded.
- Authoritative evidence: `SHADOW_TESTING_REVIEW.md`.

## M-003: Implement the confirmed-stop diagnostic

- Status: `IMPLEMENTED`
- Objective: make the confirmed-versus-discrepant protective-stop hypothesis
  reproducible without modifying the source ledger, audit, or event log.
- Dependencies: `M-002` checkpoint and its mature SIP execution audit.
- Deliverables:
  - read-only, source-fingerprinted diagnostic generator;
  - predeclared CS-001 hypothesis and validation criteria;
  - decision boundary that does not authorize parameter changes.
- Exit criteria:
  - diagnostic implementation and tests present;
  - exploratory and future-validation evidence kept distinct.
- Deferred work:
  - CS-001 validation remains unresolved and is not the active milestone. Resume
    only under its existing predeclared criteria; do not infer a result.
- Authoritative protocol: `CONFIRMED_STOP_DIAGNOSTIC_PLAN.md`.

## M-004: Evaluate the fixed forward one-entry checkpoint

- Status: `VALIDATING`
- Objective: evaluate one accepted entry per symbol per Eastern trading day
  against the frozen baseline at the predeclared fixed forward checkpoint.
- Dependencies:
  - 50 new closed baseline trades beginning on or after September 21, 2026;
  - at least 15 later same-symbol entry opportunities;
  - complete required replay coverage;
  - mature SIP classifications for all 50 baseline trades;
  - an exact-window paired replay with the required checkpoint cutoff.
- Deliverables:
  - immutable first-50 cohort assessment;
  - readiness classification that does not accept retrospective evidence;
  - paired baseline/candidate outcome with coverage and drawdown reporting;
  - explicit promising, unfavorable, or inconclusive classification under the
    predeclared gates.
- Exit criteria:
  - evaluate exactly once when the declared evidence gates are met;
  - classify absent evidence or too few later opportunities as inconclusive
    rather than extending the window after outcomes are known;
  - preserve the rule as research-only unless a later, separate approval and
    paper-validation milestone authorizes runtime behavior.
- Evidence boundary:
  - the protocol and evaluator are implemented publicly;
  - current private-runtime maturity, readiness, and outcome are unknown here;
  - no private ledger, audit, cache, report, credential, or research source may
    be copied into this repository.
- Authoritative protocol: `REENTRY_EXPERIMENT_PLAN.md`.

## Deferred and Unresolved

- Resume CS-001 validation only from
  `CONFIRMED_STOP_DIAGNOSTIC_PLAN.md`; its implementation is complete but its
  validation status is unresolved.
- Legacy code references decisions `D-002` through `D-013`. No authoritative
  definitions or rationale are currently available. Do not reconstruct them. If
  the original register is located, assess and migrate it separately.
- No milestone currently authorizes a strategy, risk, trading-window,
  paper-order, or live-order change.
