"""Read-only historical evaluation for the diagnostic early-trend detector.

This is descriptive signal-timing research, not a portfolio simulator. It uses
closed 1h candles, enters at the next candle's open, evaluates fixed forward
horizons, and subtracts configurable round-trip fees + slippage. No Telegram
credentials are read and no signals are sent.
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone

import main

SYMBOLS = ("BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT")
CANDLES = 1000
HORIZONS = (6, 12, 24)
# Default round-trip cost: 0.20% fees + 0.10% slippage total.
ROUND_TRIP_COST_PCT = float(os.getenv("BACKTEST_ROUND_TRIP_COST_PCT", "0.30"))
OUTPUT = os.getenv("BACKTEST_OUTPUT", "early-trend-historical-report.json")


def finite_num(value):
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def main_run():
    if not finite_num(ROUND_TRIP_COST_PCT) or ROUND_TRIP_COST_PCT < 0:
        raise ValueError("BACKTEST_ROUND_TRIP_COST_PCT must be finite and >= 0")

    observations = []
    errors = []
    for symbol in SYMBOLS:
        try:
            raw = main.klines(symbol, "1h", CANDLES)
            if raw is None or len(raw) < 260:
                errors.append({"symbol": symbol, "error": "insufficient closed-candle history"})
                continue
            frame = main.add_indicators(raw).reset_index(drop=True)
            # Require complete OHLC values and reject malformed rows before evaluation.
            for col in ("open", "high", "low", "close"):
                frame[col] = frame[col].astype(float)
            if not all(frame[c].map(finite_num).all() for c in ("open", "high", "low", "close")):
                errors.append({"symbol": symbol, "error": "non-finite OHLC values"})
                continue

            # One evaluation event per symbol per 24 candles, avoiding dense overlapping
            # observations. Entry is next bar open; outcomes use only bars after entry.
            last_event_idx = -10_000
            for idx in range(205, len(frame) - max(HORIZONS) - 1):
                if idx - last_event_idx < max(HORIZONS):
                    continue
                hist = frame.iloc[: idx + 1]
                result = main.early_trend_detector(hist)
                state = result.get("state", "UNKNOWN")
                baseline = bool(
                    finite_num(frame.iloc[idx].close)
                    and finite_num(frame.iloc[idx].ema21)
                    and finite_num(frame.iloc[idx].ema21_slope)
                    and finite_num(frame.iloc[idx].pdi)
                    and finite_num(frame.iloc[idx].mdi)
                    and frame.iloc[idx].close > frame.iloc[idx].ema21
                    and frame.iloc[idx].ema21_slope > 0
                    and frame.iloc[idx].pdi > frame.iloc[idx].mdi
                )
                groups = []
                if state in ("EARLY_SETUP", "CONFIRMED_TREND", "LATE_ENTRY_WARNING"):
                    groups.append(state)
                if baseline:
                    groups.append("EMA21_BASELINE")
                if not groups:
                    continue

                entry_idx = idx + 1
                entry = float(frame.iloc[entry_idx].open)
                if not math.isfinite(entry) or entry <= 0:
                    continue
                record = {
                    "symbol": symbol,
                    "signal_close_time_ms": int(frame.iloc[idx].close_time),
                    "entry_time_ms": int(frame.iloc[entry_idx].open_time),
                    "state": state,
                    "points": int(result.get("points", 0)),
                    "groups": groups,
                    "entry_price": entry,
                    "outcomes": {},
                }
                for horizon in HORIZONS:
                    window = frame.iloc[entry_idx : entry_idx + horizon]
                    exit_price = float(window.iloc[-1].close)
                    gross_pct = (exit_price / entry - 1.0) * 100.0
                    net_pct = gross_pct - ROUND_TRIP_COST_PCT
                    low_pct = (float(window.low.min()) / entry - 1.0) * 100.0
                    high_pct = (float(window.high.max()) / entry - 1.0) * 100.0
                    record["outcomes"][str(horizon)] = {
                        "gross_return_pct": gross_pct,
                        "net_return_pct": net_pct,
                        "max_adverse_pct": low_pct,
                        "max_favorable_pct": high_pct,
                    }
                observations.append(record)
                last_event_idx = idx
        except Exception as exc:
            errors.append({"symbol": symbol, "error": f"{type(exc).__name__}: {exc}"})

    summary = {}
    group_names = ("EARLY_SETUP", "CONFIRMED_TREND", "LATE_ENTRY_WARNING", "EMA21_BASELINE")
    for group in group_names:
        members = [r for r in observations if group in r["groups"]]
        by_horizon = {}
        for horizon in HORIZONS:
            vals = [r["outcomes"][str(horizon)]["net_return_pct"] for r in members]
            vals = [v for v in vals if math.isfinite(v)]
            by_horizon[str(horizon)] = {
                "count": len(vals),
                "mean_net_return_pct": round(sum(vals) / len(vals), 4) if vals else None,
                "median_net_return_pct": round(sorted(vals)[len(vals) // 2], 4) if vals else None,
                "positive_rate_pct": round(sum(v > 0 for v in vals) / len(vals) * 100, 2) if vals else None,
                "mean_max_adverse_pct": round(sum(r["outcomes"][str(horizon)]["max_adverse_pct"] for r in members) / len(members), 4) if members else None,
                "mean_max_favorable_pct": round(sum(r["outcomes"][str(horizon)]["max_favorable_pct"] for r in members) / len(members), 4) if members else None,
            }
        summary[group] = by_horizon

    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "method": {
            "symbols": list(SYMBOLS),
            "interval": "1h",
            "requested_candles_per_symbol": CANDLES,
            "entry": "next closed candle open after detector candle",
            "horizons_bars": list(HORIZONS),
            "round_trip_cost_pct": ROUND_TRIP_COST_PCT,
            "cost_assumption": "0.20% fees + 0.10% slippage round trip by default",
            "cooldown_bars": max(HORIZONS),
            "forming_candle": "excluded by main.klines",
            "limitations": [
                "Descriptive forward-return comparison, not a portfolio or trade-execution simulation",
                "Overlapping observations reduced with a 24-bar per-symbol cooldown",
                "Only the selected liquid benchmark symbols are evaluated",
                "Historical results do not establish future performance",
            ],
        },
        "summary": summary,
        "observation_count": len(observations),
        "errors": errors,
        "observations": observations,
    }
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(json.dumps({
        "output": OUTPUT,
        "observation_count": len(observations),
        "errors": errors,
        "summary": summary,
        "round_trip_cost_pct": ROUND_TRIP_COST_PCT,
    }, indent=2))
    if not observations:
        raise RuntimeError("No valid historical observations; refusing a misleading empty pass")


if __name__ == "__main__":
    main_run()
