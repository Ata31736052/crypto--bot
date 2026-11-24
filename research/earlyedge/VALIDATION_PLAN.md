# EarlyEdge replacement validation plan

This directory is an isolated research area. Nothing here changes the production scanner or its scheduled workflow.

## Target and boundary
- Target for eventual replacement: `Ata31736052/crypto--bot` (ApexSignal).
- The separate `Ata31736052/crypto---bot` repository is out of scope.
- Production `main.py`, `.github/workflows/apex-signal.yml`, Telegram secrets, and production schedule must remain untouched until a separately reviewed and approved cutover.

## Proposed EarlyEdge behavior
- Binance Spot, long-only BUY alerts; no exchange order placement.
- Four independent setups: bottom reversal, breakout, trend pullback, and range-support bounce.
- A low-commitment WATCH state may be emitted before a confirmed ENTRY. WATCH must never be counted as a trade.
- Scan cadence target: 5 minutes. Signal decisions must use completed candles only; 5m/15m/1h roles and confirmation timing must be explicit and tested.
- Regime-aware means each engine may operate only where its setup is meaningful. It does not mean forcing trades in every market condition.

## Required backtest protocol
1. Use Binance Spot OHLCV with UTC timestamps and pagination; document missing candles and API failures.
2. Use a point-in-time eligible symbol universe where possible. If only today's symbols are available, explicitly label survivorship bias and do not call the result an unbiased 180-day portfolio backtest.
3. Build indicators only from candles whose close time is at or before the decision timestamp.
4. Generate decisions after candle close; execute no earlier than the next tradable bar/open plus modeled slippage.
5. Apply fees on entry and exit, slippage on both sides, and reject impossible fills.
6. Prevent overlapping positions per symbol; cap concurrent positions and total deployed capital. Position sizing must use the agreed 1,000 USDT account and 1% risk, with a 1,000 USDT absolute position cap.
7. Resolve same-bar stop/target ambiguity conservatively (stop first) unless finer-grained data resolves the path.
8. Keep WATCH alerts separate from ENTRY trades and report conversion rate, lead time, and false-watch rate.
9. Report each engine and each regime separately: trades, net P&L, return, max drawdown, win rate, profit factor, fees, slippage, MAE, MFE, holding time, and exposure.
10. Compare against the current production strategy using identical symbols, timestamps, costs, capital, sizing, and execution assumptions. Report data coverage and any non-comparable rows.

## Acceptance gates before replacement
- Unit tests for indicators, candle-close boundaries, no-lookahead, signal deduplication, fills, fees, stop/target ordering, position caps, and state recovery.
- Reproducible 180-day report plus raw trade ledger and run metadata.
- No replacement based only on a positive aggregate return: inspect per-engine/per-regime results and drawdown.
- No merge, production schedule change, or Telegram activation until results are reviewed and explicitly approved.

## Current status
- Experimental branch created from `main`.
- This plan is documentation only; no production code or workflow has been changed.
- A full historical test has not yet been run. Do not describe this branch as a validated replacement.
