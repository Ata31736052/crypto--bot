# ApexSignal root-cause review — 2026-11-02

## Scope and safety boundary

This review applies only to `Ata31736052/crypto--bot` (ApexSignal). It does not use or modify the separate `crypto---bot` repository.

This is a read-only diagnosis of the production strategy plus one workflow reliability correction. No signal thresholds, symbol selection rules, entry/exit logic, risk settings, Telegram content, or live schedule are changed by this report.

## Confirmed production state

- Default branch: `main`.
- Production workflow: `.github/workflows/apex-signal.yml`.
- Current scheduled cadence: `10 */4 * * *` (every four hours).
- Production run on 2026-11-02 at 05:50 UTC completed successfully.
- Its production-scan job completed successfully; regression and read-only-audit jobs were skipped because this was a scheduled production event.
- The run log reports 32 valid Binance Spot USDT symbols, 2 final candidates, and 2 Telegram sends.
- The same run reports zero cases where a setup passed Daily+4H and then failed only at the 1H confirmation step, for both Trend and Bottom Hunter.

A successful Actions run confirms that the job completed; it is not proof of future signal quality or profitable outcomes.

## Root-cause evidence from the 2026-11-02 production log

The run's gate analysis identifies these recurring blockers:

- Trend: 1H confirmation (11), critically weak 4H volume (9), Daily bearish regime (5), and setup-score failures.
- Bottom Hunter: 1H confirmation (10), critically weak 4H volume (9), and setup-score failures.
- The independent base-vs-1H counters show `TREND Daily+4H base-pass=1 / 1H-only-fail=0` and `BOTTOM Daily+4H base-pass=1 / 1H-only-fail=0`.

Interpretation: in this specific run, 1H confirmation was not the incremental blocker after a setup had passed the Daily+4H base. The main scarcity occurred earlier in setup eligibility, especially market regime, 4H participation/volume, and score composition. This does not establish the dominant blocker for every market regime; it is a directly logged snapshot.

The 1H confirmation should therefore remain as-is for now. The 15m and 30m confirmation experiment should not be merged based on the available comparison.

## Multi-timeframe experiment outcome

The completed 180-day replay used a fixed 30-symbol diagnostic cohort and reported:

| Confirmation | Baseline, no resistance gate | Resistance-gate variant |
|---|---:|---:|
| 1H | -20.16% | +18.11% |
| 30m | -29.60% | -3.95% |
| 15m + 30m | -39.44% | -6.35% |

The 1H-only replay reproduced the 1H figures from the three-arm run. These are historical diagnostic outputs on a bounded cohort, not expected live returns and not a production-equivalent full-universe test. The shadow `exclude confirmed trend` row is a diagnostic ledger, not a portfolio result.

Decision: retain 1H confirmation in production; do not add 15m/30m confirmation.

## Positive capabilities already present in production code

- Binance API requests use bounded retries and fail closed when the liquidity universe cannot be verified.
- The scanner excludes the last returned candle to avoid using a still-forming candle.
- Signal candidates are revalidated against a fresh bid/ask quote immediately before Telegram delivery; crossed/invalid books and wide spreads are rejected.
- Entry drift is checked and the trade levels are rebuilt from the fresh ask before sending.
- Stop distance is bounded; TP1/TP2 must satisfy the configured risk/reward constraints.
- Daily + 4H setup and 1H entry confirmation are separated in the code.
- Bottom Hunter requires independent reversal evidence; a high score or location alone is insufficient.
- Deduplication state is written atomically with a temporary file and `os.replace`.
- The scanner emits detailed score, blocker, trigger, and gate diagnostics.
- The workflow runs Python compilation and `main.self_test()` before production execution.
- The bot sends Telegram signals only; it does not place exchange orders.

These are verified code capabilities, not claims that they guarantee a good signal.

## Operational issue corrected in this maintenance branch

The production workflow previously ended its state persistence step with `git push || true`. That suppressed push failures, allowing a run to appear successful even if the updated `signals_state.json` was not persisted to `main`. Losing this state can weaken cross-run deduplication.

This branch removes the error suppression: adding the state file and pushing a changed state must succeed for the workflow step to succeed. If there is no state change, no commit or push is attempted.

This correction does not alter signal selection or trading logic. It makes persistence failures visible instead of silently hiding them.

## Items intentionally not changed

- No change to the four-hour production schedule. Hourly replay cadence was tested experimentally, but the available reports do not isolate a production-equivalent causal benefit from changing the live schedule.
- No change to the $10M 24h liquidity floor or uncapped liquid-universe policy.
- No change to score thresholds, volume gates, RSI, ADX, confirmation logic, risk, SL/TP, or dedup window.
- No resistance gate, 15m confirmation, or 30m confirmation added to `main`.
- No experimental PR is merged by this maintenance branch.

## Final disposition

Keep production strategy settings unchanged. Retain the existing read-only diagnostics and the proven reliability safeguards. Merge this maintenance PR only after its workflow validation passes. Revisit strategy changes only with a controlled, production-equivalent comparison that uses the same universe, decision schedule, entry timing, costs, and exit accounting.
