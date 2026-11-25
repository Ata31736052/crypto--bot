"""Generate historical candidate diagnostics from the unmodified current-main ApexSignal.

This is a research-only replay of the production hourly scanner using completed
1h decisions and the latest completed 4h/1d context, not an execution/P&L backtest.
"""
import argparse
import csv
import json
import logging
from pathlib import Path

import pandas as pd

from research.earlyedge.apexsignal_historical_adapter import analyze_current_main_from_history


def read_frame(path):
    frame = pd.read_csv(path)
    # Binance klines call this taker_buy_quote; current main.py expects taker_quote.
    if "taker_buy_quote" in frame.columns and "taker_quote" not in frame.columns:
        frame = frame.rename(columns={"taker_buy_quote": "taker_quote"})
    for col in ("open_time", "close_time"):
        frame[col] = pd.to_numeric(frame[col], errors="raise").astype("int64")
    for col in ("open", "high", "low", "close", "volume", "quote_volume",
                "taker_buy_base", "taker_quote"):
        if col in frame.columns:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame


def load_symbol_frames(root, symbol):
    return {
        interval: read_frame(root / f"{symbol}_{interval}_{'365' if interval == '1d' else '180'}d.csv")
        for interval in ("1d", "4h", "1h")
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="data/apex-baseline")
    parser.add_argument("--symbol", default="ETHUSDT")
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--out-dir", default="reports/apex-baseline")
    args = parser.parse_args()

    logging.disable(logging.CRITICAL)
    root = Path(args.data_dir)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    symbol = args.symbol.upper()
    sf = load_symbol_frames(root, symbol)
    btc = load_symbol_frames(root, "BTCUSDT")
    h1 = sf["1h"]

    last_ms = int(h1["close_time"].iloc[-1])
    cutoff_ms = last_ms - args.days * 86_400_000
    # Mirror the production hourly scan schedule: evaluate after every closed
    # 1h candle. The adapter independently filters the latest closed 4h/1d bars.
    decisions = h1.loc[h1["close_time"] >= cutoff_ms, "close_time"].astype("int64").tolist()

    rows = []
    errors = {}
    warmup_skips = 0
    for i, close_ms in enumerate(decisions, 1):
        result, stats, error = analyze_current_main_from_history(
            symbol, sf, btc, int(close_ms) + 1
        )
        if error == "insufficient historical data":
            warmup_skips += 1
        elif error:
            errors[error] = errors.get(error, 0) + 1
        if result:
            setup = result["setup"]
            trade = result["trade"]
            rows.append({
                "symbol": symbol,
                "signal_close_time": int(close_ms),
                "strategy": setup["strategy"],
                "score": int(setup["score"]),
                "raw_score": setup.get("raw_score", ""),
                "reference_entry": float(trade["entry"]),
                "sl": float(trade["sl"]),
                "tp1": float(trade["tp1"]),
                "tp2": float(trade["tp2"]),
                "risk_pct": float(trade["risk_pct"]),
                "rr1": float(trade["rr1"]),
                "rr2": float(trade["rr2"]),
            })
        if i % 100 == 0:
            print(f"processed {i}/{len(decisions)} decision points; candidates={len(rows)}", flush=True)

    # Production signal_state_key is the symbol; after one alert, every strategy
    # for that symbol is suppressed for DEDUP_HOURS. Apply the same rule here.
    last_sent_ms = None
    sent_count_by_strategy = {}
    for row in sorted(rows, key=lambda x: x["signal_close_time"]):
        allowed = last_sent_ms is None or row["signal_close_time"] - last_sent_ms >= 8 * 3_600_000
        row["sent_after_8h_dedup"] = allowed
        if allowed:
            last_sent_ms = row["signal_close_time"]
            sent_count_by_strategy[row["strategy"]] = sent_count_by_strategy.get(row["strategy"], 0) + 1

    output_csv = out / f"{symbol}_apexsignal_candidates_{args.days}d.csv"
    fields = ["symbol", "signal_close_time", "strategy", "score", "raw_score",
              "reference_entry", "sl", "tp1", "tp2", "risk_pct", "rr1", "rr2",
              "sent_after_8h_dedup"]
    with output_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    report = {
        "symbol": symbol,
        "requested_days": args.days,
        "decision_schedule": "after every completed 1h candle; latest completed 4h/1d context",
        "decision_points": len(decisions),
        "warmup_skipped_decision_points": warmup_skips,
        "candidate_count_before_dedup": len(rows),
        "sent_candidate_count_after_production_8h_symbol_dedup": sum(sent_count_by_strategy.values()),
        "dedup_suppressed_count": len(rows) - sum(sent_count_by_strategy.values()),
        "by_strategy": {},
        "errors": errors,
        "candidate_csv": str(output_csv),
        "execution_backtest": False,
        "note": "Signal-generation smoke test only. Production 8h per-symbol dedup is applied. No fill, stop/target, fees, slippage, or portfolio P&L simulated.",
    }
    for strategy in sorted({r["strategy"] for r in rows}):
        subset = [r for r in rows if r["strategy"] == strategy]
        report["by_strategy"][strategy] = {
            "candidate_count_before_dedup": len(subset),
            "sent_count_after_dedup": sent_count_by_strategy.get(strategy, 0),
            "score_min": min(r["score"] for r in subset),
            "score_median": sorted(r["score"] for r in subset)[len(subset)//2],
            "score_max": max(r["score"] for r in subset),
        }
    (out / f"{symbol}_apexsignal_candidates_{args.days}d.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
