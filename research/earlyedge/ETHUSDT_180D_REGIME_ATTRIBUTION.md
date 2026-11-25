# EarlyEdge ETHUSDT 180-day market-regime attribution

Source: the raw per-engine trade ledgers and 1h candles in Actions run [45576863985](https://github.com/Ata31736052/crypto--bot/actions/runs/45576863985).
This is a post-hoc diagnostic for the same single-symbol smoke test, not an independent validation sample.

## Regime definition

Each trade is assigned using the latest 1h candle with `close_time <= signal_time`, consistent with the strategy's completed-candle rule:
- **BULL:** 1h close > EMA21 > EMA50.
- **BEAR:** 1h close < EMA21 < EMA50.
- **MIXED_RANGE:** all other warmed-up observations.
- EMA calculations use the same SMA-seeded recursive EMA method as `strategy.py`.
- No trades in the warm-up regime.

The P&L and fees below are from the original 0.10% per-side fee / 0.05% per-side slippage replay. `Gross` is before commission but after modeled slippage.

## Results by engine and regime

| Engine | Regime | Trades | Win rate | Gross P&L | Fees | Net P&L | Net PF | Stops / Targets |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Bottom reversal | Bull | 9 | 55.6% | +36.60 | 18.01 | +18.58 | 1.44 | 4 / 5 |
| Bottom reversal | Mixed/range | 43 | 39.5% | +15.86 | 85.00 | -69.14 | 0.75 | 26 / 17 |
| Breakout | Bull | 139 | 28.1% | -229.70 | 215.06 | -444.76 | 0.47 | 100 / 39 |
| Range bounce | Bull | 37 | 40.5% | +18.06 | 66.11 | -48.05 | 0.77 | 22 / 15 |
| Range bounce | Bear | 60 | 40.0% | +32.37 | 105.35 | -72.98 | 0.78 | 36 / 24 |
| Range bounce | Mixed/range | 48 | 27.1% | -98.71 | 86.49 | -185.20 | 0.44 | 35 / 13 |
| Trend pullback | Bull | 102 | 38.2% | -1.15 | 182.78 | -183.93 | 0.70 | 63 / 39 |

## Interpretation

1. **Breakout is not failing because of the market-regime gate alone.** All 139 entries were classified bullish, yet the engine lost 229.70 USDT before commission (slippage already included). This points to weak breakout follow-through / entry timing / stop placement as the first diagnostic target.
2. **Trend pullback also operated only in bullish regime**, but was nearly flat before commission (-1.15 USDT) and materially negative after fees. It needs a cost-aware expectancy improvement, not simply a broader regime gate.
3. **Bottom reversal's positive net result in bullish conditions is based on only 9 trades.** That is too small a sample to justify a live rule. Its 43 mixed/range trades lost 69.14 USDT net.
4. **Range bounce lost in all three regimes.** Its mixed/range bucket was the weakest, but even bull and bear buckets were negative net. This engine should not be broadened or activated as-is.
5. These buckets are descriptive and share the same 180-day sample used to inspect the engines. They do not establish causality, statistical significance, or out-of-sample edge.

## Next diagnostic priorities

- Breakout: measure distance from prior range high, candle body/wick, close location, ATR expansion, and 1h trend strength; inspect stop-outs shortly after entry.
- Trend pullback: separate shallow vs deep pullbacks and test whether entry occurs after an exhausted impulse; evaluate target realization and time-to-stop/target.
- Bottom reversal: collect a larger sample across multiple symbols and a held-out period before considering it further.
- Range bounce: inspect support quality and trend leakage; current evidence says the engine is not ready.
- Keep all changes research-only. Do not tune and evaluate on the same 180-day sample as if it were an unbiased test. Use a development window and a later untouched holdout.

## Limitations

- Only ETHUSDT, one 180-day window, four independently replayed engines.
- Current classification uses the 1h EMA21/EMA50 state at signal time; it does not claim to be a full market-wide regime label (e.g. BTC-led market regime).
- No matched ApexSignal baseline was generated in this run.
