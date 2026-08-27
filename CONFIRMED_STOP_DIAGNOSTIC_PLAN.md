# Confirmed-Stop Diagnostic Plan

Status: implemented for read-only exploratory reporting on August 26, 2026.
Future validation remains paper-only and has not been completed.

## Evidence boundary

The diagnostic reads three ignored, local sources:

- `output/shadow_paper/shadow_trades.sqlite3`
- `output/shadow_paper/execution_audit.json`
- `output/shadow_paper/events.jsonl`

It selects recorded protective-stop exits, joins their execution-audit
classification and quote telemetry, and writes a separate private report. It
opens a stable SQLite ledger in read-only/query-only/immutable mode,
fingerprints every source before and after analysis, and aborts if a source
changes while it is being read. A non-empty SQLite WAL sidecar is rejected
because it can represent an active or not-yet-checkpointed source. Missing
telemetry remains missing; it is never treated as evidence that an anomaly did
not occur.

Confirmed rows retain their raw ledger result. Discrepant rows may show the
existing SIP price-path reconstruction as a separate adjusted estimate. The
reconstruction cannot recreate intervening VWAP, EMA, or breakout-close
decisions and is not a replacement ledger.

## CS-001 hypothesis

Protective-stop exits classified as discrepant by mature SIP quotes have a
materially higher rate of exit-decision-time spread anomalies or positive
bid-implied risk overrun than confirmed protective-stop exits.

The primary marker is present when either `spread_warning_observed` or
`risk_overrun_observed` is true. The primary comparison is:

```text
discrepant-stop marker rate - confirmed-stop marker rate
```

The existing 52-trade checkpoint is exploratory. It may estimate this
association, but it cannot validate a hypothesis selected from that sample.

## Future validation criteria

The validation cohort begins with protective-stop entries recorded on or after
August 27, 2026 at 12:00 a.m. Eastern (`2026-08-27T00:00:00-04:00`) while the
strategy remains frozen. This explicit boundary prevents August 26 observations
from being classified retroactively after their results are known. Evaluation
waits until the cohort contains at least:

- 20 total protective-stop exits;
- five confirmed protective-stop exits; and
- five discrepant protective-stop exits.

The hypothesis succeeds if the primary comparison is at least 20 percentage
points in the validation cohort. It fails if the comparison is zero or
negative after the minimum evaluable sample is reached. It remains
inconclusive when the sample minimum is not reached, telemetry is missing for
either cohort, or the comparison is above zero but below 20 percentage points.

The 20-point threshold is an operational materiality threshold, not a claim of
statistical significance. A later inferential analysis would require a sample
size and method selected independently of favorable results.

## Secondary exploratory fields

The report also preserves holding time, local entry hour, repeated-symbol
entry number, maximum observed spread, maximum bid-implied risk overrun, quote
age, and telemetry completeness. These are diagnostic context, not additional
hypotheses to search until something looks favorable.

## Decision boundary

- A successful result supports reviewing the shadow fill model.
- A failed result redirects diagnosis toward entry and setup quality.
- An inconclusive result supports gathering the missing predeclared evidence.
- No outcome authorizes a strategy, stop-distance, risk, trading-window,
  paper-order, or live-order change by itself.

## Run the report

After the post-session SIP audit has matured and no shadow process is writing
the sources:

```bash
python -m bot confirmed-stop-diagnostic
```

The default output is
`output/shadow_paper/confirmed_stop_diagnostic.json`. Alternate local source
and report paths can be supplied with `--ledger`, `--audit`, `--events`, and
`--report`.
