# One-Entry-Per-Symbol Research Experiment

Status: retrospective research completed September 20, 2026; fixed forward
checkpoint declared before its September 21 start. The scheduled shadow
strategy and broker-order settings are unchanged. This is not an approved
trading-rule change.

## Decision and rationale

Test one accepted entry per symbol per Eastern trading day against the frozen
baseline. The first accepted entry consumes that symbol's daily allowance;
scanner sightings and rejected setups do not. The allowance resets at the next
Eastern trading day.

The September 20 SIP audit resolved all 93 observed shadow trades. An
exploratory screen found 56 first entries with estimated SIP-path P/L of
-$23.42 and 37 repeat entries with estimated SIP-path P/L of -$42.30. The
first-entry group also lost money. These observations justify testing the
rule; they do not establish an edge or show what positions the bot would have
taken with freed capacity.

Alternatives considered were a short re-entry cooldown and stopping after
consecutive same-symbol losses. The one-entry cap is the simplest directly
testable restriction for the observed repeat-entry question. It may discard
later winners and reduce opportunity count. The decision is to test it in
replay first, while leaving the scheduled shadow process unchanged. Revisit
the decision at the fixed forward checkpoint below or if data coverage fails.

## Evidence and trust boundary

- The active macOS schedule runs from the separate private `Trading_Bot`
  checkout. Its ignored ledger, audit, captured scanner history, and replay
  data are source evidence; public-workspace research tooling must treat them
  as read-only inputs.
- The read-only observed-entry screen is generated with
  `python -m scripts.reentry_observed_screen --audit AUDIT_PATH --report REPORT_PATH`.
  It fingerprints the audit and writes only aggregate research results.
- The dashboard's paired replay control runs the same loaded bars, candidate
  source, replay profile, dates, and risk settings twice: baseline and one
  accepted entry per symbol/day. Both runs are local research simulations.
- SIP price-path reconstruction cannot reproduce intervening VWAP, EMA, or
  breakout-close exits. It must remain labeled as an estimate.

## Milestones

### Complete replay dataset

Objective: make a point-in-time replay of the shadow period possible.
Deliverables: captured scanner candidates and historical one-minute, ten-second,
and trade-print data for every scanner-passing symbol/date in the chosen window,
with source, date, and missing-data inventory. Existing cached bars end before
the July–September shadow period, so the dataset needs to be built. Historical
data requests must stay within the existing account's access and rate limits.
Acceptance: all candidate symbol/dates have required data, or the report marks
the comparison incomplete and does not issue a success claim.

### Retrospective paired replay

Objective: compare both rules on identical historical opportunities.
Deliverables: accepted and rejected trades, P/L, average R, drawdown, and
source/coverage notes for each run. Check how closely the baseline replay
reproduces the actual shadow ledger before interpreting a candidate lift.
Acceptance: the two runs use identical inputs except for the one-entry rule;
any baseline mismatch and fill assumption are reported. The 93-trade sample
remains exploratory because it motivated the candidate.

Completed result: the dataset contains all 279 scanner-passing symbols in the
July 27 through September 18 window, with no missing minute bars, ten-second
bars, or SIP trade-print files. The baseline replay produced 436 trades,
-$91.86 net P/L, -0.074 average R, and $98.55 maximum drawdown. The one-entry
candidate produced 192 trades, -$49.72 net P/L, -0.094 average R, and $79.04
maximum drawdown. It improved net P/L by $42.14 and reduced drawdown by $19.51,
but remained negative with a lower average R and profit factor.

The replay baseline generated 436 trades while the observed shadow ledger has
93. This material mismatch means the replay does not faithfully reproduce the
actual shadow decision path. The retrospective candidate therefore fails the
plan's positive-net criterion and supplies no basis for changing the scheduled
strategy. Its lower absolute loss largely reflects taking 244 fewer trades.

### Fixed forward checkpoint

The forward cohort starts with captured opportunities on or after
September 21, 2026 at 12:00 a.m. Eastern. Evaluate once when the frozen
baseline has **50 new closed trades**. Require at least 15 later same-symbol
entry opportunities, complete replay coverage, and mature SIP classifications
for all 50 baseline trades. If these requirements are absent at that checkpoint,
the result is inconclusive; do not extend the checkpoint after seeing outcomes.

The candidate is **promising** only if its paired replay has positive net P/L,
beats baseline net P/L under the same risk assumptions, and has no larger
maximum drawdown. Otherwise the result is unfavorable or inconclusive. This
checkpoint is a directional screen, not a statistical proof of profitability.
Do not change the scheduled bot from this result alone. A favorable result
would justify a separate approval and a further paper-only validation run.

Use `scripts/reentry_forward_checkpoint.py` to track readiness without changing
the evidence sources. It freezes the first 50 qualifying ledger trades, rejects
the retrospective replay as forward evidence, and requires an exact-window
paired replay created with `--role forward_checkpoint`. Missing audit or replay
evidence remains pending; fewer than 15 later same-symbol opportunities in the
completed fixed window makes the checkpoint inconclusive rather than extending
the window after outcomes are known. The forward replay's required
`--checkpoint-cutoff` is the timezone-aware exit timestamp of the fiftieth
trade, preventing later opportunities from the same day from entering the
frozen cohort.

## Controls and recovery

### Corrected-engine exploratory review

The execution review preserves the first-50 checkpoint and treats later
observations as supplemental evidence. The `projected_risk_v2` replay includes
realized losses, open initial stop risk, and proposed initial risk in its
portfolio budget and prevents simultaneous positions in the same symbol.
These corrections change the replay engine, not the scheduled strategy.
Reports explicitly set `protocol_compatible` to false; they cannot satisfy the
original fixed-forward gate. A future validation requires a new predeclared
comparison, consistent with the restart rule below.

The alternative of treating corrected results as the original validation was
rejected because it changes the instrument after collection. Retaining the
known risk mismatch for new exploratory comparisons was also rejected because
it permits simulated exposure that the runtime would deny. Preserve original
reports for comparison; use corrected results to diagnose fidelity and select
a later experiment, not to authorize runtime changes. Revisit this boundary
only after fidelity and data coverage have been reviewed.

`scripts/run_reentry_paired_replay.py --role corrected_exploratory` supports an
explicit timezone-aware `--checkpoint-cutoff` for original-window and combined
comparisons. Derive the supplemental slice from the combined run so earlier
same-day losses and symbol allowances are preserved rather than reset.
Use separate private report paths and a research copy of the history database:
the replay records run metadata in that database. Coverage checks require
nonempty symbol/date observations and do not prove uninterrupted coverage.

Known limitations remain: proposal generation precedes portfolio filtering,
and execution uses assumed spreads and configured slippage rather than
historical executable bid/ask quotes. Candidate lift must be qualified by
these limitations and by observed-baseline matching.

No production schema migration, new dependency, paid feed, or order submission
is part of this experiment. Research reports stay under ignored `output/` and
private to the current OS user. To abandon the candidate, stop running paired
replay; the scheduled baseline remains unchanged. If source data or the replay
engine changes during collection, record the version and restart the forward
comparison under a new predeclared checkpoint.

## Scanner availability correction

Captured replay now requires retained scanner approval diagnostics. Use
`--scanner-diagnostics LOG.jsonl [ARCHIVE.jsonl ...]` with the paired replay
command, or provide those local paths in the dashboard's captured comparison.
Market-event time remains unchanged in history. Eligibility uses the diagnostic
creation timestamp, joined by symbol, market cutoff, and scanner actual values.
Repeated approvals are retained independently; duplicate events are deduplicated.
Naive timestamps, invalid chronology, or unmatched captured rows block replay.
All loaded captured rows up to the end cutoff require matching evidence,
including older rows whose delayed approvals might enter the requested window.
Use a research history copy and retain the relevant archives; do not invent a
fixed delay or rewrite original timestamps to make incomplete evidence pass.

This sidecar approach avoids changing the runtime or migrating historical
storage. A database availability column was considered but cannot recover
missing historical approvals and would require separately reviewed runtime
promotion. Using market time or an assumed fixed delay was rejected because
neither establishes when a particular approval became available. Revisit storage
when durable prospective input capture is designed.

The corrected instrument is labeled `scanner_availability_v3`, remains
`protocol_compatible: false`, and fingerprints the supplied logs. It does not
reproduce armed-setup lifetime, polling order, exact original market payloads,
or executable quotes. Existing minute/micro scanner-gate durations remain
unchanged. Temporal safety is not decision-path parity or profitability evidence.
Before a new prospective comparison, reconcile feed inputs and chronological
runtime behavior, declare parity criteria, and freeze the research instrument.

## Prospective evidence instrument

The next collection uses the existing shadow rules with an exact-input journal.
The journal records the actual normalized feed values consumed by scanner,
setup, quote, risk, and exit paths; baseline reproduction invokes the same
chronological shadow engine. A completed segment must reproduce all recorded
market calls, decision diagnostics, armed setups, trades, and results. Missing
inputs, provider errors, open positions at shutdown, or source/runtime version
drift prevent a successful qualification. Original records remain immutable.

Decision: collect these inputs prospectively rather than reconstruct unavailable
original responses. Alternatives were historical SIP substitution and another
independent simulator rewrite. SIP substitution cannot establish the original
IEX decision path, and a second implementation would retain ordering drift.
Using the runtime engine with an offline adapter gives a direct baseline parity
check without changing strategy rules or adding provider calls. The trade-offs
are local storage/serialization cost, strict version coupling, and exclusion of
segments with errors. Revisit if collection overhead creates gaps or if a new
candidate requires market inputs the baseline did not request.

The implementation's operational checks distinguish baseline reproduction from
full-session coverage (start within two minutes, reach session end, and no cycle
gap above two minutes). These checks are declared before prospective collection
and measure instrumentation only. They do not replace M-004 gates, make the old
sample untouched, or define a new strategy-validation sample size. First review
new full-session capture and any unresolved input/decision gaps; then predeclare
a separate candidate experiment and its acceptance criteria before inspecting
candidate outcomes. There is no automatic strategy promotion.
