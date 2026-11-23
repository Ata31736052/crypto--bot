# Hourly BTC-regime A/B replay (2026-11-23)

This branch is an isolated diagnostic harness. It does not modify `main.py`, send Telegram messages, place orders, or write live signal state.

## Compared engines

- **Baseline:** exact `main.py` from the current `main` branch; Bottom Hunter has the hard veto when BTC Daily and 4H are both bearish.
- **Experimental:** `main_experimental.py`, copied from PR #22 commit `53648e07ac28f57a456cb5fdb92c1cb2f275b347`; BTC becomes a 0-2 point context score while the asset-level severe-bearish veto, reversal evidence, volume, score, 1H confirmation, stop, and RR rules remain.

## Replay protocol

- Fixed 30-symbol cohort recovered from Actions run #28 (2026-10-23).
- Evaluation: 180 days ending 2026-11-23 00:00 UTC; 430-day warmup.
- Hourly decision schedule at minute 10 UTC.
- Only candles with `close_time < decision_time` are available to signal logic.
- Execution uses the first 1H candle open strictly after the decision timestamp.
- Independent $1,000 portfolios; 1% risk; max $1,000 position; 0.10% fee and 0.05% slippage per side; TP1 50% partial; stop-first on same-candle ambiguity.
- Up to 3 entries per scan, existing production dedup interval, 30-day max holding period.
- Results include overall portfolio performance and closed-trade outcome groups by BTC regime at entry.

## Interpretation

This is a bounded diagnostic cohort with survivorship/lookback-selection bias, not a full-universe production-equivalent backtest. A positive result is not sufficient by itself for production activation; inspect trade count, drawdown, MAE/MFE, and regime-specific outcomes. Do not merge this branch into production without separate review.
