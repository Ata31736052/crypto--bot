# ApexSignal operational QA report — 2026-11-02

## Scope

Repository: `Ata31736052/crypto--bot` only. The separate `crypto---bot` repository was not inspected or changed as part of this review.

This is an operational code/workflow review. It is not a certification of signal quality, profitability, or suitability for financial decisions. No live production scan was manually dispatched during this review.

## Current source and test parity

The current `main.py` blob is `1e9242db13c818a8cfbe017e0669b73a9b047287`, and the current `.github/workflows/apex-signal.yml` blob is `0bb9d128eb0148bd1a9aefa1805d60f8c3d01d6e`.

Both blobs exactly match the versions at commit `e4ce413a53f5100c2b37ae36fd2cce1a3cbc737d`, which was tested by Actions run [41407996176](https://github.com/Ata31736052/crypto--bot/actions/runs/41407996176).

- Regression job: success.
- Python 3.11 dependency installation: success.
- `python -m py_compile main.py`: success.
- `python -c "import main; main.self_test()"`: success.
- Production scan and read-only audit jobs were skipped in that push-triggered run, as expected.
- The subsequent README-only commit did not change either executable blob above. The workflow path filters intentionally do not trigger a run for README/docs-only changes.

## Latest observed production execution

The latest production scan visible during this review was Actions run [41396372174](https://github.com/Ata31736052/crypto--bot/actions/runs/41396372174), started 2026-11-02 05:50 UTC, on commit `0c45a1878b0f868b3d8676dab47a7a3b170d790c).

Observed in its job log:

- Production job: completed successfully.
- Python compilation and self-tests: completed successfully.
- Binance universe: 32 eligible USDT Spot symbols in that run.
- Final scan summary: 32 scanned, 2 final candidates, 2 Telegram sends.
- Signal state was saved and the Git push completed successfully in that run.
- No application `SCAN ERROR`, traceback, or Telegram send failure appeared in the reviewed log excerpts.
- GitHub runner/action deprecation warnings appeared; these were runner/tooling warnings, not Python scanner exceptions.

Important: this production execution predates the state-persistence error-reporting correction merged in `e4ce413`. It proves the earlier push succeeded on that occasion, not that the corrected failure path has been exercised by a scheduled production run.

## Confirmed operational safeguards

The reviewed source/workflow includes bounded Binance retries, fail-closed liquidity-universe handling, exclusion of the still-forming candle, fresh quote/spread/drift validation before any Telegram send, trade-level rebuilding from the fresh ask, bounded stop/risk-reward checks, atomic local state writes, Python compilation, and self-tests. The workflow is Telegram-only; no exchange order-placement code was identified in the reviewed execution path.

The `audit` dispatch mode is configured with empty Telegram credentials and a separate `audit_signals_state.json` path. The send function returns without sending when credentials are empty. This mode is intended for read-only market diagnostics, not production delivery.

## Outstanding configuration discrepancy

The current `exchange_symbols()` function filters for tradable USDT Spot symbols and excludes leveraged-token suffixes, but it does not explicitly exclude `BTCUSDT`. The latest reviewed production log also included `BTCUSDT` in the score-audit output. The prior project requirement was to exclude BTC from the directional candidate universe while still using BTC for market-regime context.

This was not changed in this QA pass because it affects candidate-universe behavior. It should be treated as an unresolved configuration mismatch, not silently assumed to be fixed.

## Scope limitations and final disposition

- No manual live scan was run in this review. The most recent real production execution is the pre-correction run listed above.
- The regression workflow passed against the exact current executable source and workflow blobs.
- The production run after the state-persistence correction remains unverified until the next scheduled production execution completes.
- Open experimental/draft PRs remain separate from `main`; they were not merged or included in production.
- No strategy thresholds, scoring rules, schedule, risk settings, or signal delivery behavior were changed during this QA review.

Conclusion: code compilation and self-tests pass, and the last observed production run completed successfully. The full current production workflow has not yet been observed after the persistence correction. One outstanding BTC-universe mismatch is recorded above. Do not describe the bot as fully verified end-to-end after the correction until a scheduled production run confirms it.
