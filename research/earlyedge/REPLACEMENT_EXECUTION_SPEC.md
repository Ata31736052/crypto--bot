# EarlyEdge replacement execution specification

Status: research specification only; NOT a live scanner and NOT approved for production.

## Target
Replace the current Daily + 4H signal engine only after matched validation. The current production `main.py`, production workflow, Telegram delivery, secrets, and scheduler remain unchanged until explicit approval.

## Operating behavior
- Binance Spot USDT symbols; BUY alerts only; never place exchange orders.
- Scan on a 30-minute cadence. Evaluate the latest fully closed 15-minute candle; use only completed 1-hour context. A 30-minute invocation must not create duplicate decisions for the same 15-minute close.
- **Iran operating window:** use the IANA timezone `Asia/Tehran`, not the runner's UTC clock. Start at 07:00 Iran time (inclusive) and stop at 23:00 (exclusive). No scan, symbol download, or Telegram alert may run from 23:00 through 06:59 Iran time.
- The live runner must call `research.earlyedge.iran_schedule.is_scan_window()` as its first operational guard, before exchange/network requests, symbol iteration, or notification setup; if false, log `outside_iran_scan_window` and exit successfully. Historical backtests must not apply this wall-clock guard.
- The external scheduler must trigger this repository only during 07:00-22:30 Iran time at 30-minute intervals. Do not add a recurring GitHub Actions schedule for live scanning: GitHub scheduled runs are UTC-based and can consume Actions even when a Python-level guard exits. Keep historical research runs manual/one-off.
- User manually reviews alerts. Therefore emit both WATCH (early setup, not an entry) and ENTRY (actionable candidate) with clear labels rather than suppressing every candidate behind a strict score threshold.
- Four independent setup families: bottom/reversal, breakout, trend pullback, and range-support bounce.
- No absolute BTC bearish veto, no arbitrary RSI ceiling such as 72, and no requirement that all engines agree. BTC/market regime is context and risk annotation, not a universal lockout.
- Every ENTRY must include reference entry, invalidation/stop, TP1, TP2, estimated reward/risk, nearest confirmed resistance/headroom, score/quality label, regime, and concise reasons. WATCH alerts must say explicitly that they are not entry instructions.
- Enforce closed-candle/no-lookahead rules, symbol-level deduplication, and a cooldown that does not hide a genuinely new setup from a different engine without recording the suppression reason.

## Backtest and acceptance gates
1. First make the signal builder computationally feasible for a full 180-day 15m dataset. The current prototype repeatedly recalculates full-history indicators and scans the entire 1h array for each 15m decision; benchmark and optimize this before launching a multi-symbol Actions run.
2. Test at least four liquid USDT pairs over the same 180-day UTC window, excluding BTC as a trade candidate but retaining BTC context. Record the exact fixed symbol list and explicitly label survivorship bias unless a point-in-time universe is available.
3. Run the current production ApexSignal adapter and EarlyEdge on the same symbols and dates. Report the native decision-cadence difference rather than pretending the strategies have identical entry timing.
4. Use 1,000 USDT initial capital, 1% risk, 1,000 USDT maximum position/exposure, 0.10% fee per side, and 0.05% slippage per side. Entries occur no earlier than the next available candle open; stop wins same-candle stop/target collisions.
5. Publish raw trade ledgers and per-symbol, per-engine, and per-regime results: net P&L, return, max drawdown, trade count, win rate, profit factor, fees, slippage, MAE, MFE, holding time, exposure, WATCH-to-ENTRY conversion, and signal delay.
6. Include tests for candle boundaries, no lookahead, deduplication, next-bar fills, stop/target collisions, costs, and portfolio limits. Benchmark runtime and memory.
7. No merge, production schedule change, Telegram activation, or replacement until all gates pass and the user explicitly approves cutover.

## GitHub Actions usage
- No recurring 5-minute Actions workflow.
- Historical multi-symbol research is manual/one-off only; do not add a recurring backtest schedule.
- The intended 30-minute live trigger is external scheduling after the correct repository target and credentials are separately verified. Do not assume the existing cron-job.org job for the other repository targets this repository.
