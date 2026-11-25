# EarlyEdge ETHUSDT 180-day cost attribution

Source: raw per-engine trade ledgers from [Actions run 45576863985](https://github.com/Ata31736052/crypto--bot/actions/runs/45576863985), artifact `earlyedge-ethusdt-180d-smoke`.
This analysis uses the recorded trade quantities and slipped entry/exit prices. Thus `gross_pnl` here is before commission but AFTER the modeled 0.05% slippage on each side. It is a fixed-ledger counterfactual: it does not resize positions or rerun cash compounding when the fee rate changes.

## Decomposition

| Engine | Trades | P&L before commission (slippage included) | Commission | Net P&L at 0.10%/side | Gross profit factor | Approx. break-even fee per side |
|---|---:|---:|---:|---:|---:|---:|
| Bottom reversal | 52 | +52.46 USDT | 103.02 USDT | -50.55 USDT | 1.201 | 0.0509% |
| Breakout | 139 | -229.70 USDT | 215.06 USDT | -444.76 USDT | 0.664 | No non-negative fee can make it profitable |
| Range bounce | 145 | -48.28 USDT | 257.95 USDT | -306.23 USDT | 0.931 | No non-negative fee can make it profitable |
| Trend pullback | 102 | -1.15 USDT | 182.78 USDT | -183.93 USDT | 0.998 | No non-negative fee can make it profitable |

The bottom-reversal engine is the only engine with positive aggregate P&L before commission, but it needs commission at or below roughly 0.0509% per side to break even under the recorded trade sizes and fills. At the tested 0.10% per side, it loses. The other three engines are already negative before commission, so fee reduction alone cannot rescue them.

## Fixed-ledger fee sensitivity

The table below subtracts recorded commission scaled linearly from the recorded gross P&L. Quantities and fills are held constant; this is diagnostic only, not a full portfolio replay.

| Fee per side | Bottom reversal | Breakout | Range bounce | Trend pullback |
|---:|---:|---:|---:|---:|
| 0.000% | +52.46 | -229.70 | -48.28 | -1.15 |
| 0.025% | +26.71 | -283.46 | -112.77 | -46.84 |
| 0.050% | +0.95 | -337.23 | -177.26 | -92.54 |
| 0.075% | -24.80 | -390.99 | -241.74 | -138.23 |
| 0.100% | -50.55 | -444.76 | -306.23 | -183.93 |
| 0.150% | -102.06 | -552.28 | -435.21 | -275.31 |
| 0.200% | -153.57 | -659.81 | -564.18 | -366.70 |

## What this tells us

1. Do not simply lower fees in the simulator or change strategy thresholds to make the headline result look better. Use the actual Binance fee tier applicable to the account and explicitly model any maker/taker mix only when execution assumptions justify it.
2. Bottom reversal deserves deeper diagnostics, but it is not yet viable at the tested cost assumptions.
3. Breakout needs entry/false-breakout analysis; its losses exist before commission.
4. Range bounce and trend pullback need regime and stop/target/holding-time analysis; reducing commission is insufficient.
5. Next research work should attribute trade outcomes by market regime and setup quality, then run any proposed change on a held-out period. No threshold tuning or production activation follows from this diagnostic alone.

## Method limitation

This is a ledger-level sensitivity calculation based on the trade quantities from the 0.10% fee replay. A full replay at each fee tier could slightly change risk-based sizing through the evolving cash/equity balance. No such multi-fee replay is claimed here.
