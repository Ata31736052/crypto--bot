# EarlyEdge ETHUSDT 180-day single-symbol smoke results

Run: https://github.com/Ata31736052/crypto--bot/actions/runs/45576863985
Branch: `experiment/earlyedge-replacement-20261124`
Commit: `141ba28c70b64f96a11e78f9fe3c34983d48f009`
Artifact: `earlyedge-ethusdt-180d-smoke` (GitHub artifact ID `15004004015`; retention through 2026-12-09)

## Scope and data

- Binance Spot `ETHUSDT`, 15m decisions with 1h context.
- Window requested: 2026-05-29 08:30 UTC to 2026-11-25 08:30 UTC.
- 15m rows: 17,279; 1h rows: 4,319.
- No internal timestamp gaps were found in either downloaded series.
- Forming candles were excluded.
- The one-row shortfall versus 17,280/4,320 is consistent with the fractional start boundary and excluding an unfinished candle; it is not an observed internal gap.
- Execution: next 15m bar open, 1% equity risk, 1,000 USDT starting cash and max position/exposure, 0.1% fee per side, 0.05% slippage per side. WATCH is not traded. Stop wins same-bar stop/target collisions.
- Each engine was replayed separately; this is not a combined four-engine portfolio.

## Results

| Engine | ENTRY signals | WATCH signals | Closed trades | Win rate | Net return | Max DD | Profit factor | Fees |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Bottom reversal | 60 | 2,261 | 52 | 42.31% | -5.06% | 11.72% | 0.842 | 103.02 USDT |
| Breakout | 239 | 1,621 | 139 | 28.06% | -44.48% | 44.48% | 0.468 | 215.06 USDT |
| Range bounce | 510 | 3,076 | 145 | 35.86% | -30.62% | 31.05% | 0.646 | 257.95 USDT |
| Trend pullback | 204 | 3,680 | 102 | 38.24% | -18.39% | 22.05% | 0.699 | 182.78 USDT |

All results are net of modeled fees and slippage. No engine passed the profitability gate. Bottom reversal was least negative, not profitable. The high fee totals are material relative to the account size and trade count.

## Diagnostic notes

- Closed-trade counts can be lower than ENTRY counts because the replay permits only one open position per symbol; entries while a position is active are not queued as new trades.
- WATCH counts are candidate observations, not unique alerts or trades. The CSV is candle-level and can contain consecutive WATCH rows.
- Average trade MFE was approximately 0.92%-1.05% across engines, while average MAE was approximately -0.82% to -0.90%. This suggests entries frequently experienced adverse movement before the target; it does not by itself identify the cause.
- The four engines were evaluated independently on one symbol. There is no point-in-time multi-symbol universe, no regime-sliced performance report, and no matched production ApexSignal comparison in this run.
- These figures are a pipeline smoke-test result, not evidence that EarlyEdge is ready to replace ApexSignal.

## Decision and next gates

1. Do not merge or activate EarlyEdge in production based on this result.
2. Diagnose entry/exit expectancy, stop/target distances, fee sensitivity, and WATCH-to-ENTRY behavior before tuning any thresholds.
3. Add explicit regime attribution and data-coverage checks to the multi-symbol replay.
4. Build a point-in-time eligible universe (or clearly label survivorship bias), then compare EarlyEdge against production on identical symbols, timestamps, capital, costs, and execution assumptions.
5. Keep `main.py`, `.github/workflows/apex-signal.yml`, Telegram secrets/schedule, and the separate `crypto---bot` repository unchanged until review and explicit approval.
