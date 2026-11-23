#!/usr/bin/env python3
"""Hourly 180-day A/B replay: current main vs PR #22 BTC regime treatment.

Uses the recovered fixed 30-symbol cohort, completed candles only, and next-1H-open
entries. This bounded diagnostic sample has survivorship/lookback bias and is not
a full-universe production-equivalent performance claim. No Telegram or orders.
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
import main_experimental

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
    # 430-day warmup gives the 180-day evaluation window 250+ daily candles.
    fetch_start = start_ms - int(430 * 86400 * 1000)
    market = {}
    for i, s in enumerate(symbols, 1):
        print(f"Downloading {i}/{len(symbols)}: {s}", flush=True)
        market[s] = {
            "1d": fetch_klines(s, "1d", fetch_start, end_ms),
            "4h": fetch_klines(s, "4h", fetch_start, end_ms),
            "1h": fetch_klines(s, "1h", fetch_start, end_ms),
        }
    return market

def frame_at(df, decision_ms, n=250):
    # Strict close-time cutoff: a candle is usable only if it has closed.
    out = df[df["close_time"] < decision_ms].tail(n).copy().reset_index(drop=True)
    return out

def scan_times(start_ms, end_ms):
    """Hourly scan at minute 10 UTC; first decision is strictly after start_ms."""
    start = datetime.fromtimestamp(start_ms / 1000, timezone.utc).replace(minute=10, second=0, microsecond=0)
    if start.timestamp() * 1000 <= start_ms:
        start += timedelta(hours=1)
    while start.timestamp() * 1000 < end_ms:
        yield int(start.timestamp() * 1000)
        start += timedelta(hours=1)

def next_entry_index(h1, decision_ms):
    # The entry open must be strictly after the scan/decision timestamp.
    idx = np.flatnonzero(h1["open_time"].to_numpy(dtype=np.int64) > decision_ms)
    return int(idx[0]) if len(idx) else None

def candidate_at(engine, symbol, raw_frames, decision_ms, btc_d, btc_h):
    """Run the actual engine against point-in-time closed candles, then price at next 1H open."""
    frames = {tf: frame_at(raw_frames[tf], decision_ms) for tf in ("1d", "4h", "1h")}
    if min(len(frames["1d"]), len(frames["4h"]), len(frames["1h"])) < 210:
        return None
    entry_idx = next_entry_index(raw_frames["1h"], decision_ms)
    if entry_idx is None:
        return None
    entry_open = float(raw_frames["1h"].iloc[entry_idx]["open"])
    if not np.isfinite(entry_open) or entry_open <= 0:
        return None
    original_klines, original_build_trade = engine.klines, engine.build_trade

    def replay_klines(requested_symbol, interval, limit=250):
        if requested_symbol != symbol or interval not in frames:
            raise RuntimeError(f"unexpected historical kline request: {requested_symbol} {interval}")
        return frames[interval].copy()

    def replay_build_trade(h, strategy, entry_override=None):
        return original_build_trade(h, strategy, entry_override=entry_open)

    try:
        engine.klines, engine.build_trade = replay_klines, replay_build_trade
        result, _, err = engine.analyze_symbol(symbol, btc_d, btc_h)
    finally:
        engine.klines, engine.build_trade = original_klines, original_build_trade

    if err or not result:
        return None
    result["entry_index"] = entry_idx
    result["entry_time"] = int(raw_frames["1h"].iloc[entry_idx]["open_time"])
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
        h1 = market[p["symbol"]]["1h"]
        i = min(idx_by_symbol.get(p["symbol"], p["entry_index"]), len(h1) - 1)
        mark = float(h1.iloc[i]["close"]) if i >= 0 else p["entry_price"]
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
        h1 = market[p["symbol"]]["1h"]
        end_idxs = np.flatnonzero((h1["close_time"].to_numpy(dtype=np.int64) < decision_ms) &
                                  (np.arange(len(h1)) > p["last_checked_index"]))
        for ix in end_idxs:
            bar = h1.iloc[int(ix)]
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
            if int(ix) - p["entry_index"] >= MAX_HOLD_HOURS:
                close_piece(port, p, p["qty"], float(bar["close"]), "TIME", ts)
                break
        if p in port["positions"] and len(end_idxs):
            p["last_checked_index"] = int(end_idxs[-1])

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
    regime = regime_bucket(result.get("btc_d"), result.get("btc_h"))
    p = {
        "symbol": symbol, "strategy": result["setup"]["strategy"],
        "score": int(result["setup"]["score"]), "decision_time": decision_ms,
        "entry_time": int(result["entry_time"]), "entry_index": entry_idx,
        "last_checked_index": entry_idx - 1, "entry_price": entry, "entry_raw": raw_entry,
        "sl": float(trade["sl"]), "tp1": float(trade["tp1"]), "tp2": float(trade["tp2"]),
        "btc_daily": result.get("btc_d"), "btc_4h": result.get("btc_h"), "btc_regime": regime,
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
        h1 = market[p["symbol"]]["1h"]
        eligible = np.flatnonzero(
            (h1["close_time"].to_numpy(dtype=np.int64) < decision_ms) &
            (np.arange(len(h1)) >= p["entry_index"]) &
            (np.arange(len(h1)) > p["last_checked_index"])
        )
        for ix in eligible:
            bar = h1.iloc[int(ix)]
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
            if int(ix) - p["entry_index"] >= MAX_HOLD_HOURS:
                shadow_close_piece(p, p["qty"], close, "TIME", ts)
                break
        if p["status"] == "PENDING" and len(eligible):
            p["last_checked_index"] = int(eligible[-1])


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
        h1 = market[p["symbol"]]["1h"]
        eligible = np.flatnonzero(h1["close_time"].to_numpy(dtype=np.int64) < end_ms)
        if not len(eligible):
            continue
        bar = h1.iloc[int(eligible[-1])]
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
    """Rank only candidates that passed the full current production pipeline."""
    executable = []
    for candidate in candidates:
        trade = candidate.get("trade")
        if trade is None:
            port.setdefault("execution_rejections", 0)
            port["execution_rejections"] += 1
            continue
        row = dict(candidate)
        row["_execution_trade"] = trade
        executable.append(row)
    executable.sort(key=lambda x: (
        int(x["setup"]["score"]), float(x["_execution_trade"]["rr2"]),
        -float(x["_execution_trade"]["risk_pct"])
    ), reverse=True)
    selected, used = [], set()
    for row in executable:
        symbol = row["symbol"]
        if symbol in used:
            continue
        last = port["last_signal_ms"].get(symbol)
        if last is not None and decision_ms - last < main.DEDUP_HOURS * 3600 * 1000:
            continue
        selected.append(row)
        used.add(symbol)
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
                        "max_holding_days": 30}
    }

def regime_bucket(btc_d, btc_h):
    if btc_d == "BULLISH" and btc_h == "BULLISH":
        return "BULLISH"
    if btc_d == "BEARISH" and btc_h == "BEARISH":
        return "BEARISH"
    return "MIXED_NEUTRAL"


def regime_breakdown(port):
    buckets = {}
    for name in ("BULLISH", "MIXED_NEUTRAL", "BEARISH"):
        closed = [t for t in port["closed"] if t.get("btc_regime") == name]
        opened = [p for p in port["positions"] if p.get("btc_regime") == name]
        pnls = [float(t["final_pnl"]) for t in closed]
        buckets[name] = {
            "entries": len(closed) + len(opened),
            "closed_trades": len(closed),
            "open_at_end": len(opened),
            "win_rate_closed_pct": round(100 * sum(x > 0 for x in pnls) / len(pnls), 2) if pnls else None,
            "closed_net_pnl_usdt": round(sum(pnls), 4),
            "avg_mae_pct": round(float(np.mean([t["mae_pct"] for t in closed])), 4) if closed else None,
            "avg_mfe_pct": round(float(np.mean([t["mfe_pct"] for t in closed])), 4) if closed else None,
        }
    return buckets


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
    if not fixed_symbols_env:
        raise ValueError("BACKTEST_SYMBOLS must pin the exact comparison cohort")
    symbols = list(dict.fromkeys(item.strip().upper() for item in fixed_symbols_env.split(",") if item.strip()))
    if len(symbols) != MAX_SYMBOLS:
        raise ValueError(f"Expected exactly {MAX_SYMBOLS} fixed symbols; received {len(symbols)}")
    if "BTCUSDT" in symbols:
        raise ValueError("BTCUSDT is context-only and must not be in the directional cohort")

    data_symbols = symbols + ["BTCUSDT"]
    market = load_market(data_symbols, start_ms, end_ms)
    btc = market["BTCUSDT"]
    baseline = make_portfolio("main_current_btc_hard_veto")
    experimental = make_portfolio("pr22_btc_context_penalty")
    scans = list(scan_times(start_ms, end_ms))
    print(
        f"Replay start={datetime.fromtimestamp(start_ms/1000, timezone.utc).isoformat()} "
        f"end={datetime.fromtimestamp(end_ms/1000, timezone.utc).isoformat()} "
        f"symbols={len(symbols)} scans={len(scans)} cadence=1h",
        flush=True
    )

    for n, decision_ms in enumerate(scans, 1):
        advance_positions(baseline, market, decision_ms)
        advance_positions(experimental, market, decision_ms)
        btc_d_df = frame_at(btc["1d"], decision_ms)
        btc_h_df = frame_at(btc["4h"], decision_ms)
        if len(btc_d_df) < 210 or len(btc_h_df) < 210:
            continue
        btc_d = main.daily_regime(main.add_indicators(btc_d_df))
        btc_h = main.daily_regime(main.add_indicators(btc_h_df))

        baseline_candidates, exp_candidates = [], []
        idx_by_symbol = {}
        for sym in symbols:
            h1raw = market[sym]["1h"]
            closed = np.flatnonzero(h1raw["close_time"].to_numpy(dtype=np.int64) < decision_ms)
            if len(closed):
                idx_by_symbol[sym] = int(closed[-1])
            base = candidate_at(main, sym, market[sym], decision_ms, btc_d, btc_h)
            exp = candidate_at(main_experimental, sym, market[sym], decision_ms, btc_d, btc_h)
            if base:
                baseline_candidates.append(base)
            if exp:
                exp_candidates.append(exp)

        baseline["qualified_candidates"] += len(baseline_candidates)
        experimental["qualified_candidates"] += len(exp_candidates)
        for port, candidates in ((baseline, baseline_candidates), (experimental, exp_candidates)):
            picked = pick_scan_results(candidates, port, decision_ms)
            port["selected_before_cash"] += len(picked)
            for result in picked:
                enter(port, result, market, decision_ms)
            port["equity_curve"].append({"time": decision_ms, "equity": equity(port, market, idx_by_symbol)})

        if n % 100 == 0 or n == len(scans):
            print(
                f"scan {n}/{len(scans)} | main entries={baseline['signals']} "
                f"PR22 entries={experimental['signals']} | qualifying "
                f"{len(baseline_candidates)}/{len(exp_candidates)}",
                flush=True
            )

    idx_by_symbol = {}
    for sym in data_symbols:
        h1 = market[sym]["1h"]
        idx = np.flatnonzero(h1["close_time"].to_numpy(dtype=np.int64) < end_ms)
        if len(idx):
            idx_by_symbol[sym] = int(idx[-1])

    report = {
        "metadata": {
            "data_source": BASE,
            "evaluation_start_utc": datetime.fromtimestamp(start_ms/1000, timezone.utc).isoformat(),
            "evaluation_end_utc": datetime.fromtimestamp(end_ms/1000, timezone.utc).isoformat(),
            "period_days": DAYS,
            "scan_schedule": "every 1 hour at minute 10 UTC",
            "scan_count": len(scans),
            "symbols": symbols,
            "symbol_selection_mode": "fixed 30-symbol historical benchmark cohort recovered from the 2026-10-23 GitHub Actions log",
            "comparison": "main current BTC Daily+4H hard veto vs PR #22 BTC context score; each engine uses its own exact main.py logic",
            "entry": "next 1H candle open strictly after decision timestamp; all signal inputs are closed candles only",
            "portfolio": {"initial_cash_usdt": INITIAL_CASH, "risk_fraction": RISK_FRACTION, "max_position_usdt": MAX_POSITION,
                          "fee_per_side": FEE_RATE, "slippage_per_side": SLIPPAGE, "tp1_partial_exit_fraction": 0.5,
                          "same_candle_stop_first": True, "max_holding_days": 30, "max_signals_per_scan": main.MAX_SIGNALS},
            "important_limitations": [
                "Fixed historical cohort is a bounded diagnostic sample and has survivorship/lookback-selection bias.",
                "This is not full-universe production-equivalent performance.",
                "BTC regime buckets are assigned at entry; closed-trade statistics are grouped by entry regime.",
                "No Telegram calls, order placement, or live-state writes are performed."
            ]
        },
        "results": [
            {**summarize(baseline, market, idx_by_symbol, start_ms, end_ms), "btc_entry_regime_breakdown": regime_breakdown(baseline)},
            {**summarize(experimental, market, idx_by_symbol, start_ms, end_ms), "btc_entry_regime_breakdown": regime_breakdown(experimental)}
        ]
    }
    (OUT / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    for port in (baseline, experimental):
        with (OUT / f"{port['name']}_closed_trades.csv").open("w", newline="", encoding="utf-8") as fh:
            rows = [{k: v for k, v in t.items() if k not in ("qty",)} for t in port["closed"]]
            if rows:
                fields = list(dict.fromkeys(key for row in rows for key in row.keys()))
                writer = csv.DictWriter(fh, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main_run()
