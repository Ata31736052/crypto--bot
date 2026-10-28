#!/usr/bin/env python3
"""Reproducible 180-day experimental MTF-confirmation replay.

Scope: fixed 30-symbol Binance Spot USDT diagnostic cohort (see workflow).
Signals share a 15-minute decision schedule at :05/:20/:35/:50 UTC; only fully closed candles
are visible. Every arm enters at the first 15m open strictly after decision and
uses 15m bars for exits, equity marks, and MFE/MAE. No live orders or Telegram.
"""
import csv
import json
import logging
import math
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

import main

logging.getLogger().setLevel(logging.CRITICAL)
BASE = "https://data-api.binance.vision"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "ApexSignal-Historical-AB-Replay/1.0"})
DAYS = int(os.getenv("BACKTEST_DAYS", "180"))
MAX_SYMBOLS = int(os.getenv("BACKTEST_MAX_SYMBOLS", "30"))
INITIAL_CASH = 1000.0
RISK_FRACTION = 0.01
MAX_POSITION = 1000.0
FEE_RATE = 0.001
SLIPPAGE = 0.0005
MAX_HOLD_HOURS = 24 * 30
MAX_HOLD_MS = MAX_HOLD_HOURS * 3600 * 1000
EXECUTION_TF = "15m"
OUT = Path("backtest-artifacts")
COLS = ["open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_base", "taker_quote", "ignore"]
NUMERIC = ["open", "high", "low", "close", "volume", "quote_volume",
           "trades", "taker_base", "taker_quote"]

def get_json(path, params=None):
    last = None
    for attempt in range(5):
        try:
            r = SESSION.get(BASE + path, params=params or {}, timeout=25)
            if r.status_code in (418, 429, 500, 502, 503, 504):
                r.raise_for_status()
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            last = exc
            time.sleep(0.75 * (attempt + 1))
    raise RuntimeError(f"Binance request failed {path}: {last}")

def universe():
    info = get_json("/api/v3/exchangeInfo")
    allowed = {x["symbol"] for x in info.get("symbols", [])
               if x.get("status") == "TRADING" and x.get("quoteAsset") == "USDT"
               and x.get("isSpotTradingAllowed") is not False
               and not any(z in x.get("symbol", "") for z in ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT"))}
    ticks = get_json("/api/v3/ticker/24hr")
    # BTC is context only, never a directional BUY candidate. Exclude obvious
    # stablecoin-to-USDT pairs from the directional universe; keep BTC history
    # available separately for regime context.
    non_directional = {"BTCUSDT", "USDCUSDT", "USD1USDT", "RLUSDUSDT", "USDTUSDT"}
    ranked = []
    for t in ticks if isinstance(ticks, list) else []:
        s = t.get("symbol", "")
        try:
            qv = float(t.get("quoteVolume", 0) or 0)
        except (TypeError, ValueError):
            continue
        if s in allowed and s not in non_directional and qv >= main.MIN_24H_USDT_VOLUME:
            ranked.append((s, qv))
    ranked.sort(key=lambda x: x[1], reverse=True)
    chosen = [s for s, _ in ranked[:MAX_SYMBOLS]]
    if len(chosen) < 5:
        raise RuntimeError(f"Too few current liquid pairs for replay: {len(chosen)}")
    return chosen, [{"symbol": s, "current_24h_quote_volume": q} for s, q in ranked if s in chosen]

def fetch_klines(symbol, interval, start_ms, end_ms):
    rows, cursor = [], start_ms
    while cursor < end_ms:
        batch = get_json("/api/v3/klines", {
            "symbol": symbol, "interval": interval, "startTime": cursor,
            "endTime": end_ms, "limit": 1000
        })
        if not batch:
            break
        rows.extend(batch)
        next_cursor = int(batch[-1][0]) + 1
        if next_cursor <= cursor:
            raise RuntimeError(f"Pagination stalled for {symbol} {interval}")
        cursor = next_cursor
        if len(batch) < 1000:
            break
        time.sleep(0.05)
    if not rows:
        return pd.DataFrame(columns=COLS)
    df = pd.DataFrame(rows, columns=COLS)
    df["open_time"] = pd.to_numeric(df["open_time"], errors="coerce").astype("int64")
    df["close_time"] = pd.to_numeric(df["close_time"], errors="coerce").astype("int64")
    for c in NUMERIC:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)

def load_market(symbols, start_ms, end_ms):
    # Daily indicators need a long warmup; intraday confirmation only needs enough
    # history for EMA200/rolling indicators. Avoid downloading 430 days of 15m bars.
    fetch_start = start_ms - int(430 * 86400 * 1000)
    mtf_fetch_start = start_ms - int(60 * 86400 * 1000)
    market = {}
    for i, s in enumerate(symbols, 1):
        print(f"Downloading {i}/{len(symbols)}: {s}", flush=True)
        market[s] = {
            "1d": fetch_klines(s, "1d", fetch_start, end_ms),
            "4h": fetch_klines(s, "4h", fetch_start, end_ms),
            "1h": fetch_klines(s, "1h", fetch_start, end_ms),
            "30m": fetch_klines(s, "30m", mtf_fetch_start, end_ms),
            "15m": fetch_klines(s, "15m", mtf_fetch_start, end_ms),
        }
    return market

def frame_at(df, decision_ms, n=250):
    # Strict close-time cutoff: a candle is usable only if it has closed.
    # close_time is monotonic because fetch_klines sorts by open_time. Binary
    # search avoids filtering every historical row on each of 17,280 scans.
    close_times = df["close_time"].to_numpy(dtype=np.int64)
    stop = int(np.searchsorted(close_times, decision_ms, side="left"))
    start = max(0, stop - n)
    return df.iloc[start:stop].copy().reset_index(drop=True)

def scan_times(start_ms, end_ms):
    # Scan every 15 minutes, five minutes after each quarter-hour boundary.
    # At :05/:20/:35/:50, only fully closed 15m and 30m candles are visible.
    # All confirmation modes share the same decision schedule.
    start = datetime.fromtimestamp(start_ms / 1000, timezone.utc).replace(second=0, microsecond=0)
    valid_minutes = {5, 20, 35, 50}
    while start.minute not in valid_minutes or start.timestamp() * 1000 < start_ms:
        start += timedelta(minutes=1)
    while start.timestamp() * 1000 < end_ms:
        yield int(start.timestamp() * 1000)
        start += timedelta(minutes=15)

def next_entry_index(h1, decision_ms):
    # The entry open must be strictly after the scan/decision timestamp.
    # side="right" preserves the strict > decision_ms rule.
    open_times = h1["open_time"].to_numpy(dtype=np.int64)
    idx = int(np.searchsorted(open_times, decision_ms, side="right"))
    return idx if idx < len(h1) else None

def candidate_at(symbol, raw_frames, decision_ms, btc_d, btc_h, gate, confirmation_mode="1h"):
    d = frame_at(raw_frames["1d"], decision_ms)
    h = frame_at(raw_frames["4h"], decision_ms)
    h1 = frame_at(raw_frames["1h"], decision_ms)
    if min(len(d), len(h), len(h1)) < 210:
        return None
    execution = raw_frames[EXECUTION_TF]
    entry_idx = next_entry_index(execution, decision_ms)
    if entry_idx is None:
        return None
    entry_open = float(execution.iloc[entry_idx]["open"])
    if not np.isfinite(entry_open) or entry_open <= 0:
        return None
    # Signal qualification is identical in both variants and uses only closed candles.
    # The experimental resistance gate is evaluated later at the actual next-open entry.
    result, _, err = main.analyze_symbol(
        symbol, btc_d, btc_h,
        historical_frames={
            "1d": d, "4h": h, "1h": h1,
            "15m": frame_at(raw_frames["15m"], decision_ms),
            "30m": frame_at(raw_frames["30m"], decision_ms),
        },
        enable_resistance_gate=False,
        confirmation_mode=confirmation_mode
    )
    if err or not result:
        return None
    result["entry_index"] = entry_idx
    result["entry_time"] = int(execution.iloc[entry_idx]["open_time"])
    result["decision_time"] = decision_ms
    result["entry_open_raw"] = entry_open
    return result

def trade_signal_key(result):
    return (result["symbol"], result["setup"]["strategy"])

def make_portfolio(name, apply_resistance_gate=False, exclude_confirmed_trend=False):
    return {"name": name, "cash": INITIAL_CASH, "positions": [], "closed": [],
            "last_signal_ms": {}, "equity_curve": [], "signals": 0, "rejected_cash": 0,
            "fees_paid": 0.0, "qualified_candidates": 0, "selected_before_cash": 0,
            "resistance_gate_rejections": 0, "apply_resistance_gate": apply_resistance_gate,
            "exclude_confirmed_trend": exclude_confirmed_trend,
            "excluded_trend_candidates": 0}

def equity(port, market, idx_by_symbol):
    total = port["cash"]
    for p in port["positions"]:
        bars = market[p["symbol"]][EXECUTION_TF]
        latest = min(idx_by_symbol.get(p["symbol"], p["entry_index"] - 1), len(bars) - 1)
        # A newly entered position has no completed post-entry 15m bar at this
        # scan timestamp. Avoid a pre-entry close or future-candle leakage.
        mark = float(bars.iloc[latest]["close"]) if latest >= p["entry_index"] else p["entry_price"]
        total += p["qty"] * mark * (1 - SLIPPAGE) * (1 - FEE_RATE)
    return total

def close_piece(port, p, qty, raw_price, reason, timestamp):
    if qty <= 0:
        return
    fill = raw_price * (1 - SLIPPAGE)
    proceeds = qty * fill
    fee = proceeds * FEE_RATE
    port["cash"] += proceeds - fee
    port["fees_paid"] += fee
    p["realized_pnl"] += proceeds - fee - qty * p["entry_price"]
    p["qty"] -= qty
    if reason == "TP1":
        p["tp1_done"] = True
    if p["qty"] <= p["initial_qty"] * 1e-8:
        p["qty"] = 0.0
        p["exit_time"] = timestamp
        p["exit_reason"] = reason
        p["final_pnl"] = p["realized_pnl"] - p["entry_fee"]
        port["closed"].append(p.copy())
        port["positions"].remove(p)

def advance_positions(port, market, decision_ms):
    for p in list(port["positions"]):
        bars = market[p["symbol"]][EXECUTION_TF]
        close_times = bars["close_time"].to_numpy(dtype=np.int64)
        stop = int(np.searchsorted(close_times, decision_ms, side="left"))
        start = max(int(p["entry_index"]), int(p["last_checked_index"]) + 1)
        # Process exactly the same eligible closed candles, without scanning the
        # entire execution history at every decision.
        for ix in range(start, stop):
            bar = bars.iloc[ix]
            hi, lo = float(bar["high"]), float(bar["low"])
            p["mfe_pct"] = max(p["mfe_pct"], (hi / p["entry_price"] - 1) * 100)
            p["mae_pct"] = min(p["mae_pct"], (lo / p["entry_price"] - 1) * 100)
            ts = int(bar["close_time"])
            # Conservative ambiguity rule: if stop and a target both fit in one
            # candle, assume the stop occurred first.
            if lo <= p["sl"]:
                close_piece(port, p, p["qty"], p["sl"], "SL", ts)
            else:
                if not p["tp1_done"] and hi >= p["tp1"]:
                    close_piece(port, p, p["qty"] * 0.5, p["tp1"], "TP1", ts)
                    if p["qty"] > 0 and hi >= p["tp2"]:
                        close_piece(port, p, p["qty"], p["tp2"], "TP2", ts)
                elif p["tp1_done"] and hi >= p["tp2"]:
                    close_piece(port, p, p["qty"], p["tp2"], "TP2", ts)
            if p not in port["positions"]:
                break
            p["last_checked_index"] = int(ix)
            # Bound stale positions to 30 days; close at this bar's close.
            if ts - int(p["entry_time"]) >= MAX_HOLD_MS:
                close_piece(port, p, p["qty"], float(bar["close"]), "TIME", ts)
                break
        if p in port["positions"] and stop > start:
            p["last_checked_index"] = stop - 1

def enter(port, result, market, decision_ms):
    symbol = result["symbol"]
    entry_idx = result["entry_index"]
    raw_entry = result["entry_open_raw"]
    strategy = result["setup"]["strategy"]
    # Trade levels are precomputed at the actual next-open price before ranking
    # and portfolio limits. The same candidate data is copied per variant.
    trade = result.get("_execution_trade")
    if trade is None:
        port.setdefault("execution_rejections", 0)
        port["execution_rejections"] += 1
        return
    entry = raw_entry * (1 + SLIPPAGE)
    stop_distance = max(entry - float(trade["sl"]), 1e-12)
    eq = max(0.0, port["cash"] + sum(p["qty"] * p["entry_price"] for p in port["positions"]))
    desired = min(MAX_POSITION, eq * RISK_FRACTION / (stop_distance / entry))
    notional = min(desired, port["cash"] / (1 + FEE_RATE))
    if notional < 10:
        port["rejected_cash"] += 1
        return
    qty = notional / entry
    entry_fee = notional * FEE_RATE
    port["cash"] -= notional + entry_fee
    port["fees_paid"] += entry_fee
    p = {
        "symbol": symbol, "strategy": result["setup"]["strategy"],
        "score": int(result["setup"]["score"]), "decision_time": decision_ms,
        "entry_time": int(result["entry_time"]), "entry_index": entry_idx,
        "last_checked_index": entry_idx - 1, "entry_price": entry, "entry_raw": raw_entry,
        "sl": float(trade["sl"]), "tp1": float(trade["tp1"]), "tp2": float(trade["tp2"]),
        "initial_qty": qty, "qty": qty, "notional": notional,
        "tp1_done": False, "realized_pnl": 0.0, "entry_fee": entry_fee,
        "mfe_pct": 0.0, "mae_pct": 0.0, "exit_time": None, "exit_reason": None,
        "final_pnl": None
    }
    port["positions"].append(p)
    port["signals"] += 1
    port["last_signal_ms"][symbol] = decision_ms


def advance_shadow_ledger(ledger, market, decision_ms):
    """Advance rejected candidates independently; never touches portfolio cash/ranking."""
    for p in ledger:
        if p["status"] != "PENDING":
            continue
        bars = market[p["symbol"]][EXECUTION_TF]
        close_times = bars["close_time"].to_numpy(dtype=np.int64)
        stop = int(np.searchsorted(close_times, decision_ms, side="left"))
        start = max(int(p["entry_index"]), int(p["last_checked_index"]) + 1)
        for ix in range(start, stop):
            bar = bars.iloc[ix]
            hi, lo, close = float(bar["high"]), float(bar["low"]), float(bar["close"])
            p["mfe_pct"] = max(p["mfe_pct"], (hi / p["entry_price"] - 1) * 100)
            p["mae_pct"] = min(p["mae_pct"], (lo / p["entry_price"] - 1) * 100)
            risk_per_unit = max(p["entry_price"] - p["sl"], 1e-12)
            p["mfe_r"] = max(p["mfe_r"], (hi - p["entry_price"]) / risk_per_unit)
            p["mae_r"] = min(p["mae_r"], (lo - p["entry_price"]) / risk_per_unit)
            ts = int(bar["close_time"])
            if lo <= p["sl"]:
                shadow_close_piece(p, p["qty"], p["sl"], "SL", ts)
            else:
                if not p["tp1_done"] and hi >= p["tp1"]:
                    shadow_close_piece(p, p["qty"] * 0.5, p["tp1"], "TP1", ts)
                    if p["qty"] > 0 and hi >= p["tp2"]:
                        shadow_close_piece(p, p["qty"], p["tp2"], "TP2", ts)
                elif p["tp1_done"] and hi >= p["tp2"]:
                    shadow_close_piece(p, p["qty"], p["tp2"], "TP2", ts)
            if p["status"] != "PENDING":
                break
            p["last_checked_index"] = int(ix)
            if ts - int(p["entry_time"]) >= MAX_HOLD_MS:
                shadow_close_piece(p, p["qty"], close, "TIME", ts)
                break
        if p["status"] == "PENDING" and stop > start:
            p["last_checked_index"] = stop - 1


def shadow_close_piece(p, qty, raw_price, reason, timestamp):
    if qty <= 0:
        return
    fill = raw_price * (1 - SLIPPAGE)
    proceeds = qty * fill
    fee = proceeds * FEE_RATE
    p["fees_usdt"] += fee
    p["realized_pnl_usdt"] += qty * (fill - p["entry_price"]) - fee
    p["qty"] -= qty
    if reason == "TP1":
        p["tp1_done"] = True
        p["tp1_hit"] = True
    if reason == "TP2":
        p["tp2_hit"] = True
    if p["qty"] <= p["initial_qty"] * 1e-8:
        p["qty"] = 0.0
        p["status"] = reason
        p["exit_time"] = timestamp
        p["net_pnl_usdt"] = p["realized_pnl_usdt"] - p["entry_fee_usdt"]
        p["net_pnl_pct_notional"] = 100 * p["net_pnl_usdt"] / max(p["notional_usdt"], 1e-12)
        p["net_pnl_r"] = p["net_pnl_usdt"] / max(p["risk_budget_usdt"], 1e-12)


def mark_shadow_open_at_end(ledger, market, end_ms):
    for p in ledger:
        if p["status"] != "PENDING":
            continue
        bars = market[p["symbol"]][EXECUTION_TF]
        close_times = bars["close_time"].to_numpy(dtype=np.int64)
        stop = int(np.searchsorted(close_times, end_ms, side="left"))
        if stop == 0:
            continue
        bar = bars.iloc[stop - 1]
        # Mark remaining quantity to market using the same conservative exit costs.
        fill = float(bar["close"]) * (1 - SLIPPAGE)
        proceeds = p["qty"] * fill
        fee = proceeds * FEE_RATE
        p["fees_usdt"] += fee
        p["net_pnl_usdt"] = p["realized_pnl_usdt"] + p["qty"] * (fill - p["entry_price"]) - fee - p["entry_fee_usdt"]
        p["net_pnl_pct_notional"] = 100 * p["net_pnl_usdt"] / max(p["notional_usdt"], 1e-12)
        p["net_pnl_r"] = p["net_pnl_usdt"] / max(p["risk_budget_usdt"], 1e-12)
        p["status"] = "OPEN_AT_END_MARKED_TO_MARKET"
        p["exit_time"] = int(bar["close_time"])


def pick_scan_results(candidates, port, decision_ms, shadow_ledger=None):
    # Evaluate executable levels/gate before ranking and before the per-scan cap.
    # Otherwise rejected candidates consume signal slots and are repeatedly counted,
    # while sorting still uses close-price trade levels instead of next-open levels.
    use_gate = port["apply_resistance_gate"]
    executable = []
    for candidate in candidates:
        if port["exclude_confirmed_trend"] and candidate["setup"]["strategy"] == "CONFIRMED TREND":
            port["excluded_trend_candidates"] += 1
            continue
        raw_entry = float(candidate["entry_open_raw"])
        strategy = candidate["setup"]["strategy"]
        trade, trade_error = main.build_trade(
            candidate["h"], strategy, entry_override=raw_entry,
            enable_resistance_gate=use_gate
        )
        if trade is None:
            if use_gate and trade_error and "insufficient room below confirmed 4H resistance" in trade_error:
                port["resistance_gate_rejections"] += 1
                # Only the experimental gate arm records shadows; shadow trades never
                # enter the real portfolio, consume slots, or alter cash/equity.
                if shadow_ledger is not None:
                    shadow_trade, shadow_error = main.build_trade(
                        candidate["h"], strategy, entry_override=raw_entry,
                        enable_resistance_gate=False
                    )
                    if shadow_trade is not None:
                        entry_idx = int(candidate["entry_index"])
                        shadow_ledger.append({
                            "symbol": candidate["symbol"], "strategy": strategy,
                            "score": int(candidate["setup"]["score"]),
                            "decision_time": int(decision_ms),
                            "entry_time": int(candidate["entry_time"]),
                            "entry_index": entry_idx, "last_checked_index": entry_idx - 1,
                            "entry_raw": raw_entry,
                            "entry_price": raw_entry * (1 + SLIPPAGE),
                            "sl": float(shadow_trade["sl"]), "tp1": float(shadow_trade["tp1"]),
                            "tp2": float(shadow_trade["tp2"]),
                            "resistance": shadow_trade.get("resistance"),
                            "rejection_reason": trade_error,
                            "status": "PENDING", "exit_time": None,
                            "initial_qty": 0.0, "qty": 0.0, "notional_usdt": 0.0,
                            "risk_budget_usdt": INITIAL_CASH * RISK_FRACTION,
                            "entry_fee_usdt": 0.0, "fees_usdt": 0.0,
                            "realized_pnl_usdt": 0.0, "net_pnl_usdt": None,
                            "net_pnl_pct_notional": None, "net_pnl_r": None,
                            "tp1_done": False, "tp1_hit": False, "tp2_hit": False,
                            "mfe_pct": 0.0, "mae_pct": 0.0,
                            "mfe_r": 0.0, "mae_r": 0.0
                        })
                        p = shadow_ledger[-1]
                        risk_distance = max(p["entry_price"] - p["sl"], 1e-12)
                        p["notional_usdt"] = min(MAX_POSITION, p["risk_budget_usdt"] * p["entry_price"] / risk_distance)
                        p["initial_qty"] = p["notional_usdt"] / p["entry_price"]
                        p["qty"] = p["initial_qty"]
                        p["entry_fee_usdt"] = p["notional_usdt"] * FEE_RATE
                        p["fees_usdt"] = p["entry_fee_usdt"]
            else:
                port.setdefault("execution_rejections", 0)
                port["execution_rejections"] += 1
            continue
        row = dict(candidate)
        row["_execution_trade"] = trade
        executable.append(row)

    executable.sort(key=lambda x: (
        int(x["setup"]["score"]),
        float(x["_execution_trade"]["rr2"]),
        -float(x["_execution_trade"]["risk_pct"])
    ), reverse=True)
    selected = []
    used = set()
    for r in executable:
        sym = r["symbol"]
        if sym in used:
            continue
        last = port["last_signal_ms"].get(sym)
        if last is not None and decision_ms - last < main.DEDUP_HOURS * 3600 * 1000:
            continue
        selected.append(r)
        used.add(sym)
        if len(selected) >= main.MAX_SIGNALS:
            break
    return selected

def summarize(port, market, idx_by_symbol, period_start, period_end):
    eq = equity(port, market, idx_by_symbol)
    curve = port["equity_curve"] + [{"time": period_end, "equity": eq}]
    peak, max_dd = INITIAL_CASH, 0.0
    for point in curve:
        val = float(point["equity"])
        peak = max(peak, val)
        if peak > 0:
            max_dd = max(max_dd, (peak - val) / peak * 100)
    closed = port["closed"]
    pnls = [float(x["final_pnl"]) for x in closed]
    wins = sum(x > 0 for x in pnls)
    return {
        "variant": port["name"], "period_days": DAYS, "initial_equity_usdt": INITIAL_CASH,
        "ending_equity_mark_to_market_usdt": round(eq, 4),
        "net_return_pct_mark_to_market": round((eq / INITIAL_CASH - 1) * 100, 4),
        "net_pnl_usdt_mark_to_market": round(eq - INITIAL_CASH, 4),
        "max_drawdown_pct_sampled_equity": round(max_dd, 4),
        "qualified_candidates_before_portfolio_limits": port["qualified_candidates"],
        "selected_before_cash_check": port["selected_before_cash"],
        "resistance_gate_rejections": port["resistance_gate_rejections"],
        "excluded_confirmed_trend_candidates": port["excluded_trend_candidates"],
        "signals_entered": port["signals"], "closed_trades": len(closed),
        "open_positions_at_end": len(port["positions"]), "cash_rejected_entries": port["rejected_cash"],
        "win_rate_closed_pct": round(wins / len(closed) * 100, 2) if closed else None,
        "avg_closed_trade_pnl_usdt": round(float(np.mean(pnls)), 4) if pnls else None,
        "fees_paid_usdt": round(port["fees_paid"], 4),
        "avg_mae_pct_closed": round(float(np.mean([x["mae_pct"] for x in closed])), 4) if closed else None,
        "avg_mfe_pct_closed": round(float(np.mean([x["mfe_pct"] for x in closed])), 4) if closed else None,
        "assumptions": {"risk_fraction": RISK_FRACTION, "max_position_usdt": MAX_POSITION,
                        "fee_per_side": FEE_RATE, "slippage_per_side": SLIPPAGE,
                        "tp1_partial_exit_fraction": 0.5, "same_candle_stop_first": True,
                        "max_holding_days": 30, "execution_timeframe": EXECUTION_TF}
    }

def main_run():
    OUT.mkdir(parents=True, exist_ok=True)
    end_utc = os.getenv("BACKTEST_END_UTC", "").strip()
    if end_utc:
        parsed_end = datetime.fromisoformat(end_utc.replace("Z", "+00:00"))
        if parsed_end.tzinfo is None:
            raise ValueError("BACKTEST_END_UTC must include a timezone, preferably UTC")
        end_ms = int(parsed_end.astimezone(timezone.utc).timestamp() * 1000)
    else:
        end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - DAYS * 86400 * 1000

    fixed_symbols_env = os.getenv("BACKTEST_SYMBOLS", "").strip()
    if fixed_symbols_env:
        symbols = list(dict.fromkeys(
            item.strip().upper() for item in fixed_symbols_env.split(",") if item.strip()
        ))
        if not symbols:
            raise ValueError("BACKTEST_SYMBOLS was set but contained no symbols")
        universe_rows = [{"symbol": symbol, "selection": "fixed BACKTEST_SYMBOLS"} for symbol in symbols]
    else:
        symbols, universe_rows = universe()
    data_symbols = list(symbols)
    if "BTCUSDT" not in data_symbols:
        data_symbols.append("BTCUSDT")
    market = load_market(data_symbols, start_ms, end_ms)
    confirmation_mode = os.getenv("BACKTEST_CONFIRMATION_MODE", "1h").strip().lower()
    if confirmation_mode not in {"1h", "30m", "15m+30m"}:
        raise ValueError("BACKTEST_CONFIRMATION_MODE must be one of: 1h, 30m, 15m+30m")
    btc = market.get("BTCUSDT")
    if not btc:
        raise RuntimeError("BTCUSDT history is required for point-in-time regime context")
    baseline = make_portfolio("baseline_no_resistance_gate")
    experimental = make_portfolio("experimental_resistance_gate", apply_resistance_gate=True)
    trend_free = make_portfolio("experimental_gate_no_confirmed_trend",
                                apply_resistance_gate=True, exclude_confirmed_trend=True)
    shadow_ledger = []
    scans = list(scan_times(start_ms, end_ms))
    print(f"Replay start={datetime.fromtimestamp(start_ms/1000, timezone.utc).isoformat()} "
          f"end={datetime.fromtimestamp(end_ms/1000, timezone.utc).isoformat()} "
          f"symbols={len(symbols)} scans={len(scans)}", flush=True)
    for n, decision_ms in enumerate(scans, 1):
        # Advance isolated shadow outcomes and real portfolios using closed candles only.
        advance_shadow_ledger(shadow_ledger, market, decision_ms)
        advance_positions(baseline, market, decision_ms)
        advance_positions(experimental, market, decision_ms)
        advance_positions(trend_free, market, decision_ms)
        btc_d_df = frame_at(btc["1d"], decision_ms)
        btc_h_df = frame_at(btc["4h"], decision_ms)
        if len(btc_d_df) < 210 or len(btc_h_df) < 210:
            continue
        btc_d = main.daily_regime(main.add_indicators(btc_d_df))
        btc_h = main.daily_regime(main.add_indicators(btc_h_df))
        baseline_candidates, exp_candidates = [], []
        idx_by_symbol = {}
        for sym in symbols:
            execution_raw = market[sym][EXECUTION_TF]
            close_times = execution_raw["close_time"].to_numpy(dtype=np.int64)
            stop = int(np.searchsorted(close_times, decision_ms, side="left"))
            if stop:
                idx_by_symbol[sym] = stop - 1
            base = candidate_at(sym, market[sym], decision_ms, btc_d, btc_h, False, confirmation_mode)
            if not base:
                continue
            # Both variants receive exactly the same closed-candle signal candidates.
            # Only execution-time levels/gate differ at the next 1H open.
            baseline_candidates.append(base)
            exp_candidates.append(base)
        for port in (baseline, experimental, trend_free):
            port["qualified_candidates"] += len(baseline_candidates)
        for port, candidates in ((baseline, baseline_candidates),
                                 (experimental, exp_candidates), (trend_free, exp_candidates)):
            picked = pick_scan_results(candidates, port, decision_ms, shadow_ledger if port is experimental else None)
            port["selected_before_cash"] += len(picked)
            for r in picked:
                enter(port, r, market, decision_ms)
            eq = equity(port, market, idx_by_symbol)
            port["equity_curve"].append({"time": decision_ms, "equity": eq})
        if n % 30 == 0 or n == len(scans):
            print(f"scan {n}/{len(scans)} | baseline signals={baseline['signals']} "
                  f"experimental signals={experimental['signals']} "
                  f"gate-no-trend signals={trend_free['signals']}", flush=True)
    # Mark remaining positions to market using each symbol's latest completed 15m candle.
    idx_by_symbol = {}
    for sym in data_symbols:
        bars = market[sym][EXECUTION_TF]
        stop = int(np.searchsorted(bars["close_time"].to_numpy(dtype=np.int64), end_ms, side="left"))
        if stop:
            idx_by_symbol[sym] = stop - 1
    # Process every completed 15m candle after the final scheduled scan before end-marking.
    # This keeps TP/SL outcomes and MFE/MAE complete through the replay boundary.
    advance_shadow_ledger(shadow_ledger, market, end_ms)
    mark_shadow_open_at_end(shadow_ledger, market, end_ms)
    shadow_summary = {
        "rejected_candidates_tracked": len(shadow_ledger),
        "final_status_counts": {status: sum(p["status"] == status for p in shadow_ledger) for status in sorted({p["status"] for p in shadow_ledger})},
        "hit_sl_count": sum(p["status"] == "SL" for p in shadow_ledger),
        "hit_tp1_event_count": sum(bool(p.get("tp1_hit")) for p in shadow_ledger),
        "hit_tp2_event_count": sum(bool(p.get("tp2_hit")) for p in shadow_ledger),
        "time_exit_count": sum(p["status"] == "TIME" for p in shadow_ledger),
        "open_at_end_count": sum(p["status"] == "OPEN_AT_END_MARKED_TO_MARKET" for p in shadow_ledger),
        "net_pnl_usdt_sum_including_end_marks": round(sum(float(p["net_pnl_usdt"] or 0) for p in shadow_ledger), 4),
        "net_pnl_usdt_mean": round(float(np.mean([p["net_pnl_usdt"] for p in shadow_ledger if p["net_pnl_usdt"] is not None])), 4) if any(p["net_pnl_usdt"] is not None for p in shadow_ledger) else None,
        "avg_mfe_pct": round(float(np.mean([p["mfe_pct"] for p in shadow_ledger])), 4) if shadow_ledger else None,
        "avg_mae_pct": round(float(np.mean([p["mae_pct"] for p in shadow_ledger])), 4) if shadow_ledger else None,
        "avg_mfe_r": round(float(np.mean([p["mfe_r"] for p in shadow_ledger])), 4) if shadow_ledger else None,
        "avg_mae_r": round(float(np.mean([p["mae_r"] for p in shadow_ledger])), 4) if shadow_ledger else None,
        "note": "Independent fixed-risk hypothetical trades; no shadow position affects real portfolio cash, ranking, selection or equity."
    }
    with (OUT / "resistance_rejected_shadow_ledger.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        fields = list(shadow_ledger[0].keys()) if shadow_ledger else [
            "symbol", "strategy", "score", "decision_time", "entry_time", "entry_index",
            "entry_raw", "entry_price", "sl", "tp1", "tp2", "rejection_reason",
            "status", "tp1_hit", "tp2_hit", "mfe_pct", "mae_pct",
            "net_pnl_usdt", "net_pnl_pct_notional", "net_pnl_r"
        ]
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(shadow_ledger)
    report = {
        "shadow_ledger": shadow_summary,
        "metadata": {
            "data_source": BASE, "evaluation_start_utc": datetime.fromtimestamp(start_ms/1000, timezone.utc).isoformat(),
            "evaluation_end_utc": datetime.fromtimestamp(end_ms/1000, timezone.utc).isoformat(),
            "scan_schedule": "every 15 minutes at :05/:20/:35/:50 UTC (same schedule across confirmation modes)",
            "execution_timeframe": EXECUTION_TF,
            "entry_rule": "first 15m candle open_time strictly greater than decision_time",
            "exit_rule": "closed 15m candles only; same-candle SL-first ambiguity rule",
            "confirmation_mode": confirmation_mode,
            "symbols": symbols,
            "symbol_selection_mode": "fixed BACKTEST_SYMBOLS benchmark cohort" if fixed_symbols_env else "current 24h quote-volume selection",
            "evaluation_end_mode": "fixed BACKTEST_END_UTC" if end_utc else "wall-clock current UTC",
            "universe_definition": (
                "fixed BACKTEST_SYMBOLS benchmark cohort"
                if fixed_symbols_env else
                "top current 24h quote-volume directional Spot USDT pairs above production floor; excludes BTCUSDT (context only) and obvious stablecoin-to-USDT pairs; max 30"
            ),
            "important_limitations": [
                "Current-universe selection creates survivorship and lookback-selection bias.",
                "This is a bounded diagnostic sample, not full-universe production-equivalent performance.",
                "Signals are capped at three per scan and use the current production dedup interval.",
                "Open positions at the end are marked to market, not force-closed."
            ]
        },
        "results": [
            summarize(baseline, market, idx_by_symbol, start_ms, end_ms),
            summarize(experimental, market, idx_by_symbol, start_ms, end_ms),
            summarize(trend_free, market, idx_by_symbol, start_ms, end_ms)
        ]
    }
    (OUT / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    for port in (baseline, experimental, trend_free):
        with (OUT / f"{port['name']}_closed_trades.csv").open("w", newline="", encoding="utf-8") as fh:
            rows = [{k: v for k, v in t.items() if k not in ("qty",)} for t in port["closed"]]
            if rows:
                # Trade dictionaries can gain diagnostic keys during partial exits;
                # include the union so later rows cannot break the artifact export.
                fieldnames = list(dict.fromkeys(key for row in rows for key in row.keys()))
                w = csv.DictWriter(fh, fieldnames=fieldnames)
                w.writeheader()
                w.writerows(rows)
    print(json.dumps(report, indent=2), flush=True)

if __name__ == "__main__":
    main_run()
