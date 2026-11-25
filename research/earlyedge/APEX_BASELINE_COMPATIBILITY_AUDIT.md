# ApexSignal baseline compatibility audit for EarlyEdge

Audit date: 2026-11-25
Repository: `Ata31736052/crypto--bot`
Target production branch: `main`

## Current production snapshot

- `main` SHA at audit: `b2d382b09225d1c1facd1d8f8eef3ec52d2ca951`.
- `main.py` identifies the production strategy as ApexSignal v3.25.0, Daily + 4H primary with 1H confirmation, Telegram signals only, no auto-trading.
- The production entry point `analyze_symbol(symbol, btc_d, btc_h)` obtains 1d, 4h, and 1h frames internally using `klines()`; it does not expose an argument for injecting historical frames. This prevents the current production snapshot from being replayed directly against a fixed historical dataset without a research adapter/refactor.

## Existing historical replay found on a different branch

An older experimental branch, `experiment/resistance-headroom-gate-20261021-sync`, contains:
- `historical_backtest.py`
- `.github/workflows/historical-backtest.yml`
- `tests/test_shadow_ledger.py`

That harness does use historical frame injection, but its branch is not the current production baseline:
- Compare `main` to that branch: status `diverged`.
- The experimental branch is 31 commits ahead and 52 commits behind `main`.
- Its `main.py` differs from current `main` (100 additions and 4 deletions in the compare result).
- Its workflow is explicitly restricted to pushes on that old experimental branch.

Therefore, any A/B result from that old branch would test an older, divergent ApexSignal implementation, not the current `main` snapshot. Do not use it as the production comparator for EarlyEdge.

## Required safe approach

1. Keep the EarlyEdge branch isolated.
2. Add a research-only adapter or optional historical-frame injection seam that lets the exact current `main.py` strategy logic consume pre-downloaded, closed 1d/4h/1h candles. Do not change the live workflow, Telegram behavior, schedule, or live defaults.
3. Verify by hash/diff that the strategy functions and constants used by production are unchanged; only the research injection seam is added on the experimental branch.
4. Replay current ApexSignal and EarlyEdge on the same symbols, 180-day interval, candle cutoffs, next executable price convention, capital, risk sizing, fees, slippage, and stop/target collision policy. Where the native strategies differ (e.g. 4h decision schedule and 1h execution vs 15m EarlyEdge decisions), report both native schedules and a clearly defined comparable execution benchmark rather than silently forcing identical timing.
5. Include current-universe selection bias, candle coverage, open-at-end handling, trade ledger, and per-regime/per-engine metrics.

## Decision

No valid matched comparison against current production `main` has been completed. The existing historical replay on the divergent experimental branch is not a substitute. EarlyEdge remains research-only; no merge or live activation is justified by the current ETHUSDT smoke results.
