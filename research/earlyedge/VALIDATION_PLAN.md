# EarlyEdge replacement validation plan

This directory is an isolated research area. Nothing here changes the production scanner or its scheduled workflow.

## Target and boundary
- Target for eventual replacement: `Ata31736052/crypto--bot` (ApexSignal).
- The separate `Ata31736052/crypto---bot` repository is out of scope.
- Production `main.py`, `.github/workflows/apex-signal.yml`, Telegram secrets, and production schedule must remain untouched until a separately reviewed and approved cutover.

## Proposed EarlyEdge behavior
- Binance Spot, long-only BUY alerts; no exchange order placement.
- Four independent research engines: bottom reversal, breakout, trend pullback, and range-support bounce.
- A low-commitment WATCH state may be emitted before a confirmed ENTRY. WATCH is not a trade and is ignored by the trade replay.
- Intended scan cadence: 5 minutes. Decisions in this research prototype are made at the close of completed 15m candles, with 1h context filtered by `close_time <= decision_close_time`.
- Regime-aware means each engine operates only where its setup is meaningful. It does not mean forcing trades in every market condition.

## Research pipeline currently implemented
1. Download Binance Spot OHLCV with pagination: `download_binance.py`.
2. Convert 15m + 1h kline CSVs into separate engine signal CSVs: `build_engine_csv.py`.
3. Signal rules and indicators are in `strategy.py`; they use bounded indicator warm-up windows and completed higher-timeframe candles only.
4. Replay one engine/symbol CSV using `backtest.py`. ENTRY fills at next bar open; WATCH is ignored; fees and slippage are modeled; stop wins same-bar stop/target collisions.

Example commands (run from repository root):
```bash
python -m research.earlyedge.download_binance --symbol ETHUSDT --interval 15m --days 180
python -m research.earlyedge.download_binance --symbol ETHUSDT --interval 1h --days 180
python -m research.earlyedge.build_engine_csv --15m data/earlyedge/ETHUSDT_15m_180d.csv --1h data/earlyedge/ETHUSDT_1h_180d.csv
python -m research.earlyedge.backtest data/earlyedge/signals/bottom_reversal.csv --cash 1000 --risk 1 --max-position 1000 --fee 0.1 --slippage 0.05\npython -m research.earlyedge.portfolio_backtest ETHUSDT=data/earlyedge/signals/bottom_reversal.csv SOLUSDT=data/earlyedge/SOLUSDT/signals/bottom_reversal.csv --cash 1000 --risk 1 --max-position 1000 --max-exposure 1000 --fee 0.1 --slippage 0.05
```

## Required backtest protocol before any strategy conclusion
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
- Research code is on isolated branch `experiment/earlyedge-replacement-20261124` and draft PR #24.
- Four engine rules, Binance historical downloader, per-engine signal CSV builder, single-symbol replay, shared-capital multi-symbol portfolio replay, and automated tests are present.
- The CI workflow compiles the research package and runs unit tests.
- A real 180-day multi-symbol portfolio backtest has NOT yet been run. The shared-capital portfolio runner is implemented, but no historical dataset has yet been downloaded and replayed. Current-day symbol lists would also introduce survivorship bias.
- No production code, production workflow, Telegram secret, or live schedule has been changed. No merge or live activation is approved.
