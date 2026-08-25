# Shadow Testing Review: 52-Trade Checkpoint

Status: structured review completed on August 25, 2026. The strategy remained
paper-only and its parameters were frozen throughout this sample.

This document preserves an aggregate, public-safe checkpoint. The underlying
SQLite ledger, event logs, decision logs, and execution-audit JSON remain local
and ignored by Git because they contain generated research data.

## Evidence boundary

The local shadow ledger contains 52 closed trades observed from July 27 through
August 25, 2026. Entries and exits in that ledger use locally simulated fills
from the live IEX observation path.

A post-session audit generated at `2026-08-25T16:29:35.435035+00:00` compared
all 52 trades with mature historical consolidated SIP quotes. The audit does
not modify the ledger.

- `confirmed` means nearby SIP entry and exit quotes support the recorded fill.
- `discrepant` means SIP evidence does not support at least one recorded fill.
- A price-path reconstruction is an estimate. It cannot recreate intervening
  VWAP, EMA, or breakout-close decisions.
- The reconstructed result is not a replacement ledger and is not evidence of
  live-trading profitability.

## Results

| Measure | Raw IEX ledger | SIP-adjusted estimate |
| --- | ---: | ---: |
| Trades | 52 | 52 |
| Wins / losses / flat | 13 / 38 / 1 | 15 / 36 / 1 |
| Net profit | -$109.74 | -$24.08 |
| Average result | -0.848R | -0.228R |
| Profit factor | 0.239 | 0.650 |

The execution audit classified 37 trades as confirmed, 15 as discrepant, and
zero as unresolved. The 37 confirmed trades had raw net profit of -$5.33, but
that subset must not be treated as an independent strategy backtest because
confirmation status is related to execution behavior.

The raw ledger materially overstates losses, but the adjusted estimate remains
negative. This checkpoint does not demonstrate a positive strategy edge.

## Cohort comparison

| Cohort | Trades | Confirmed | Discrepant | Raw net | Adjusted net | Adjusted W/L/F | Adjusted average |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| First checkpoint | 43 | 30 | 13 | -$93.17 | -$15.33 | 14 / 28 / 1 | -0.195R |
| New observations | 9 | 7 | 2 | -$16.57 | -$8.75 | 1 / 8 / 0 | -0.388R |

The additional nine trades weakened the adjusted sample. One discrepant USDE
stop-out reconstructed to a 2R target result; the other discrepant new trade,
SUGP, later reached its stop and retained the recorded loss.

## Exit-path findings

| Recorded exit path | Trades | Adjusted W/L | Adjusted net | Average result |
| --- | ---: | ---: | ---: | ---: |
| Protective stop | 27 | 2 / 25 | -$46.74 | -0.985R |
| 2R target | 9 | 9 / 0 | +$32.54 | +2.000R |
| Breakout failure | 6 | 0 / 6 | -$8.77 | -0.556R |
| VWAP loss | 7 | 3 / 3, one flat | -$0.21 | +0.051R |
| 9 EMA loss | 3 | 1 / 2 | -$0.90 | -0.095R |

All 15 discrepancies occurred on recorded protective-stop exits. Twelve stops
were directly confirmed and lost $27.99. The 15 discrepant stops lost $104.41
in the raw ledger but reconstruct to an estimated $18.75 loss, with two target
wins and 13 losses.

Execution quality is central to this sample: 44 of 52 trades closed within one
minute, and 33 closed within 30 seconds. The adjusted 9:00 Eastern cohort was
near flat at -0.082R per trade across 23 trades; the 10:00 Eastern cohort
averaged -0.314R across 23 trades. These timing slices are diagnostic leads,
not approved parameter changes.

## Decision at this checkpoint

1. Keep the application paper-only. Do not enable paper-order submission or
   add any live-order path based on these results.
2. Do not claim profitability, production readiness, or a validated edge.
3. Keep strategy parameters frozen until one diagnostic hypothesis is selected
   and its evaluation criteria are written before further testing.
4. Do not tune from the raw ledger. Any review must separate confirmed fills
   from price-path reconstructions.
5. Treat the 52-trade threshold as the start of structured diagnosis, not as an
   automatic reason to change settings or accumulate more unchanged trades.

## Next bounded milestone

The next milestone is a confirmed-stop diagnostic, not a strategy rewrite. It
should:

1. Reproduce the aggregate checkpoint from the ignored ledger and audit without
   modifying either source.
2. Compare the 12 directly confirmed stops with the 15 discrepant stop paths.
3. Segment holding time, entry hour, repeated-symbol entries, spread warnings,
   and risk overrun without selecting a favorable result after the fact.
4. Produce one explicit hypothesis and predeclared success/failure criteria.
5. Validate that hypothesis with new paper-only observations or an isolated
   replay before proposing a parameter change.

## Operational follow-up

The installed macOS LaunchAgent still uses the private-development label
`local.brandon.tradingbot.shadow`. At this checkpoint it was loaded, not
running after the observation window, had 15 recorded launches, and reported a
last exit code of zero.

The public-release code uses `app.momentumbot.shadow`, so the current
`shadow-schedule status` command does not recognize the installed older label.
The existing schedule is still launching the repository's portable command,
but label migration must be handled as a separate operational change with a
real scheduled-launch smoke test. Installing or uninstalling from the new code
before that migration is reviewed could leave duplicate or orphaned agents.
