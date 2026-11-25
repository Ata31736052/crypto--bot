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

## Research adapter now added

A research-only adapter is now present at `research/earlyedge/apexsignal_historical_adapter.py`. It temporarily injects preloaded historical frames into the current-main `main.klines()` call path while invoking the unmodified `main.analyze_symbol()` strategy logic. It filters frames to candles with `close_time < decision_ms`, requires 1d/4h/1h data for the symbol plus 1d/4h BTC context, serializes the temporary module-global injection with a lock, and restores the original live fetcher in a `finally` block. No production `main.py` code was changed.

Three adapter tests cover candle cutoff/history cap, successful frame injection and restoration, and restoration after an exception. The research CI workflow now installs the production strategy dependencies before running the test suite. The push-triggered CI run on commit `42ca6b9c9ed97d63ec724d1bdd8abaf27d69b7c1` passed all 22 tests. The PR-triggered run is still being checked.

## Required safe approach

1. Keep the EarlyEdge branch isolated.
2. Use the research adapter to replay the exact current `main.py` strategy logic against pre-downloaded, closed 1d/4h/1h candles. Do not change the live workflow, Telegram behavior, schedule, or live defaults.
3. Verify by hash/diff that the strategy functions and constants used by production are unchanged; only the adapter is added on the experimental branch.
4. Replay current ApexSignal and EarlyEdge on the same symbols and 180-day interval, with transparent treatment of native schedule differences (4h decisions/1h execution vs 15m decisions). Match capital, risk sizing, fees, slippage, and conservative stop/target handling where possible; report any unavoidable non-comparability rather than silently forcing identical timing.
5. Include universe-selection bias, candle coverage, open-at-end handling, raw trade ledger, and per-regime/per-engine metrics.

## Decision

No valid matched comparison against current production `main` has been completed. The existing historical replay on the divergent experimental branch is not a substitute. EarlyEdge remains research-only; no merge or live activation is justified by the current ETHUSDT smoke results.
