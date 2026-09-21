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

## Controls and recovery

No production schema migration, new dependency, paid feed, or order submission
is part of this experiment. Research reports stay under ignored `output/` and
private to the current OS user. To abandon the candidate, stop running paired
replay; the scheduled baseline remains unchanged. If source data or the replay
engine changes during collection, record the version and restart the forward
comparison under a new predeclared checkpoint.
