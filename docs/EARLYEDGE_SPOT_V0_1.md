# EarlyEdge Spot Bot v0.1 — isolated prototype

## Intent
Prototype replacement candidate for the existing Daily + 4H setup engine. Binance Spot, BUY-only, signal-only (no order placement), evaluating closed 15m candles with 1h context.

## Four independent engines
1. Bottom / Reversal
2. Breakout
3. Trend Pullback
4. Range

Each engine can return an early warning and/or an entry candidate. A warning is not an entry. Candidates carry score and diagnostics; the caller remains responsible for live quote validation, deduplication, Telegram delivery, and persistent state.

## Free GitHub Actions protection
- No scheduled workflow is added.
- No five-minute GitHub Actions loop is added.
- No workflow is automatically dispatched by this prototype.
- No new dependency is introduced beyond pandas/numpy already used by ApexSignal.
- Production workflow, main.py, secrets, and Telegram behavior remain unchanged.

A five-minute scan cadence, if later approved, should run in a persistent external scheduler/worker and not as a new GitHub Actions job every five minutes. This prototype is not live and does not replace production until integrated, validated on equivalent historical data, and explicitly approved.

## Validation still required
- Unit tests and compile.
- Historical comparison against the existing strategy using identical symbols, candle-close timing, costs, stop/target handling, and no lookahead.
- Regime-by-regime reporting for trend, reversal, breakout, and range periods.
- Verify no duplicate alerts and live quote/market-data failure behavior before production integration.
