"""180-day TREND BUY comparison by BTC market regime (read-only research).

Uses the production trend_setup() rules, closed 1h/4h/1d candles, and next
1h open entry. Regime is derived from BTC daily + 4h daily_regime() states.
No Telegram messages or production configuration changes.
"""
from __future__ import annotations
import json, math, os
from datetime import datetime, timezone
import pandas as pd
import main

SYMBOLS = ("ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT","ADAUSDT","DOGEUSDT","LINKUSDT","AVAXUSDT","LTCUSDT","TRXUSDT","DOTUSDT","UNIUSDT","AAVEUSDT","BCHUSDT","SUIUSDT")
FETCH_SYMBOLS = ("BTCUSDT",) + SYMBOLS
FETCH_DAYS = 240       # warm-up for the 200-day EMA
EVALUATE_DAYS = 180
CANDLES = FETCH_DAYS * 24
HORIZONS = (6, 12, 24)
COSTS = (0.20, 0.30, 0.50)
DEFAULT_COST = float(os.getenv("BACKTEST_ROUND_TRIP_COST_PCT", "0.30"))
OUTPUT = os.getenv("TREND_REGIME_OUTPUT", "trend-buy-regime-report.json")
RAW_COLS = ["open_time","open","high","low","close","volume","close_time",
            "quote_volume","trades","taker_base","taker_quote","ignore"]
NUM_COLS = ["open","high","low","close","volume","quote_volume","trades","taker_base","taker_quote"]
HOUR_MS = 3_600_000

def finite(x):
    try: return math.isfinite(float(x))
    except (TypeError, ValueError): return False

def fetch_history(symbol):
    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    current_hour = now // HOUR_MS * HOUR_MS
    cursor = current_hour - CANDLES * HOUR_MS
    rows = []
    while cursor < current_hour and len(rows) < CANDLES:
        batch = main.get_json("/api/v3/klines", {"symbol":symbol,"interval":"1h",
            "limit":min(1000,CANDLES-len(rows)),"startTime":cursor,"endTime":current_hour-1})
        if not isinstance(batch,list) or not batch: break
        rows.extend(batch)
        nxt = int(batch[-1][0]) + HOUR_MS
        if nxt <= cursor: raise RuntimeError(f"pagination stalled: {symbol}")
        cursor = nxt
        if len(batch) < 1000: break
    if not rows: raise RuntimeError(f"no history: {symbol}")
    x = pd.DataFrame(rows, columns=RAW_COLS).drop_duplicates("open_time").sort_values("open_time")
    for c in ["open_time","close_time"] + NUM_COLS: x[c] = pd.to_numeric(x[c],errors="coerce")
    x = x[x.open_time < current_hour].tail(CANDLES).reset_index(drop=True)
    if len(x) < EVALUATE_DAYS*24: raise RuntimeError(f"short history {symbol}: {len(x)} candles")
    if not all(x[c].map(finite).all() for c in ["open","high","low","close","volume"]):
        raise RuntimeError(f"non-finite OHLCV: {symbol}")
    return x

def resample_indicators(raw, rule, interval_ms):
    x = raw.copy()
    x.index = pd.to_datetime(x.open_time, unit="ms", utc=True)
    agg = {"open":"first","high":"max","low":"min","close":"last","volume":"sum",
           "quote_volume":"sum","trades":"sum","taker_base":"sum","taker_quote":"sum"}
    y = x[NUM_COLS].resample(rule, label="left", closed="left").agg(agg).dropna(subset=["open","high","low","close"])
    y["open_time"] = y.index.astype("int64") // 1_000_000
    y["close_time"] = y["open_time"] + interval_ms - 1
    y = y.reset_index(drop=True)
    return main.add_indicators(y)

def latest_idx(times, stamp):
    import bisect
    return bisect.bisect_right(times, stamp) - 1

def regime_bucket(daily_state, h4_state):
    bullish_states = {"BULLISH","EARLY_BULLISH"}
    if daily_state in bullish_states and h4_state in bullish_states: return "BULLISH"
    if daily_state != "BEARISH" and h4_state != "BEARISH": return "NEUTRAL"
    return "EXCLUDED_BEARISH"

def stats(rows, horizon, cost):
    vals = [r["gross_pct"][str(horizon)] - cost for r in rows]
    vals = [v for v in vals if math.isfinite(v)]
    if not vals: return {"count":0,"mean_net_pct":None,"median_net_pct":None,"win_rate_pct":None}
    return {"count":len(vals),"mean_net_pct":round(sum(vals)/len(vals),4),
            "median_net_pct":round(float(pd.Series(vals).median()),4),
            "win_rate_pct":round(sum(v>0 for v in vals)/len(vals)*100,2)}

def main_run():
    if not finite(DEFAULT_COST) or DEFAULT_COST < 0: raise ValueError("invalid cost")
    raw_by_symbol, errors = {}, []
    for sym in FETCH_SYMBOLS:
        try:
            raw_by_symbol[sym] = fetch_history(sym)
        except Exception as exc:
            errors.append({"symbol":sym,"stage":"fetch","error":str(exc)})
    if "BTCUSDT" not in raw_by_symbol:
        raise RuntimeError("BTC regime history unavailable; cannot run comparison")
    tradable_symbols = [s for s in SYMBOLS if s in raw_by_symbol]
    hourly = {s:main.add_indicators(x).reset_index(drop=True) for s,x in raw_by_symbol.items()}
    h4 = {s:resample_indicators(x,"4h",4*HOUR_MS) for s,x in raw_by_symbol.items()}
    daily = {s:resample_indicators(x,"1D",24*HOUR_MS) for s,x in raw_by_symbol.items()}
    btc4, btcd = h4["BTCUSDT"], daily["BTCUSDT"]
    btc4_times = btc4.close_time.astype("int64").tolist()
    btcd_times = btcd.close_time.astype("int64").tolist()
    eval_start = int(hourly["BTCUSDT"].iloc[-EVALUATE_DAYS*24].open_time)
    observations = []
    diagnostics = {"loop_candidates":0,"skip_before_eval":0,"skip_warmup":0,"skip_recent":0,"skip_cooldown":0,"skip_context":0,"evaluated_regime_bars":0,"trend_setup_calls":0,"trend_setup_accepted":0,"regime_bars":{"BULLISH":0,"NEUTRAL":0,"EXCLUDED_BEARISH":0},"setup_rejections":{},"timestamps":{}}
    for symbol in tradable_symbols:
        one, four, day = hourly[symbol], h4[symbol], daily[symbol]
        one_times = one.open_time.astype("int64").tolist()
        four_times = four.close_time.astype("int64").tolist()
        day_times = day.close_time.astype("int64").tolist()
        last_signal_time = -10**30
        if symbol == tradable_symbols[0]:
            diagnostics["timestamps"] = {"eval_start_ms":eval_start,"hourly_first_ms":int(one.iloc[0].open_time),
                "hourly_last_ms":int(one.iloc[-1].open_time),"h4_first_close_ms":int(four_times[0]),
                "h4_last_close_ms":int(four_times[-1]),"hourly_rows":len(one),"h4_rows":len(four)}
        for i, signal_time in enumerate(four_times):
            diagnostics["loop_candidates"] += 1
            if signal_time < eval_start:
                diagnostics["skip_before_eval"] += 1
                continue
            if i < 50:
                diagnostics["skip_warmup"] += 1
                continue
            if signal_time >= int(one.iloc[-25].open_time):
                diagnostics["skip_recent"] += 1
                continue
            if signal_time - last_signal_time < 8 * HOUR_MS:
                diagnostics["skip_cooldown"] += 1
                continue
            bi4, bid = latest_idx(btc4_times, signal_time), latest_idx(btcd_times, signal_time)
            si4, sid = latest_idx(four_times, signal_time), latest_idx(day_times, signal_time)
            h1i = latest_idx(one_times, signal_time)
            if min(bi4,bid,si4,sid,h1i) < 1:
                diagnostics["skip_context"] += 1
                continue
            btc_h_state = main.daily_regime(btc4.iloc[:bi4+1])
            btc_d_state = main.daily_regime(btcd.iloc[:bid+1])
            regime = regime_bucket(btc_d_state, btc_h_state)
            diagnostics["regime_bars"][regime] += 1
            if regime == "EXCLUDED_BEARISH": continue
            diagnostics["evaluated_regime_bars"] += 1
            d = day.iloc[:sid+1].tail(250)
            h = four.iloc[:si4+1].tail(250)
            h1 = one.iloc[:h1i+1].tail(250)
            daily_state = main.daily_regime(d)
            try:
                setup, reasons = main.trend_setup(d, h, h1, btc_d_state, btc_h_state, daily_state)
                diagnostics["trend_setup_calls"] += 1
                if not setup:
                    reason_key = str(reasons[0]) if reasons else "no reason returned"
                    diagnostics["setup_rejections"][reason_key] = diagnostics["setup_rejections"].get(reason_key,0)+1
                else:
                    diagnostics["trend_setup_accepted"] += 1
            except Exception as exc:
                errors.append({"symbol":symbol,"signal_time_ms":signal_time,"error":str(exc)})
                continue
            if not setup or setup.get("strategy") != "CONFIRMED TREND": continue
            # First hourly bar opening strictly after the completed 4h signal candle.
            entry_i = latest_idx(one_times, signal_time) + 1
            if entry_i < 0 or entry_i + max(HORIZONS) >= len(one): continue
            entry = float(one.iloc[entry_i].open)
            if not finite(entry) or entry <= 0: continue
            gross = {}
            for horizon in HORIZONS:
                exit_price = float(one.iloc[entry_i+horizon-1].close)
                gross[str(horizon)] = (exit_price / entry - 1) * 100
            observations.append({"symbol":symbol,"signal_time_ms":signal_time,
                "regime":regime,"btc_daily_regime":btc_d_state,"btc_4h_regime":btc_h_state,
                "asset_daily_regime":daily_state,"score":setup.get("score"),
                "entry_price":entry,"gross_pct":gross})
            last_signal_time = signal_time
    report = {
      "generated_at_utc":datetime.now(timezone.utc).isoformat(),
      "method":{"symbols":list(tradable_symbols),"evaluation_days":EVALUATE_DAYS,
        "fetch_days_including_warmup":FETCH_DAYS,"signal_strategy":"production main.trend_setup()",
        "regime":"BULLISH only when BTC Daily and 4H are BULLISH/EARLY_BULLISH; NEUTRAL when neither is BEARISH but both are not bullish",
        "bearish_regimes":"excluded from this specific neutral-vs-bullish comparison",
        "entry":"first 1h open after the closed 4h signal candle",
        "horizons_hours":list(HORIZONS),"cooldown_hours":8,
        "round_trip_cost_scenarios_pct":list(COSTS),
        "limitations":["Fifteen tradable symbols sampled; not a full-universe shared-account simulation",
          "Fixed-horizon mark-to-close outcomes, not stop/target trade simulation",
          "Historical regimes are descriptive and do not establish future performance"]},
      "observation_count":len(observations),"errors":errors,"diagnostics":diagnostics,
      "summary":{regime:{str(h):{f"cost_{cost:.2f}_pct":stats([r for r in observations if r["regime"]==regime],h,cost)
         for cost in COSTS} for h in HORIZONS} for regime in ("BULLISH","NEUTRAL")},
      "observations":observations}
    with open(OUTPUT,"w",encoding="utf-8") as f: json.dump(report,f,indent=2)
    print(json.dumps({"output":OUTPUT,"observation_count":len(observations),"errors":errors,"diagnostics":diagnostics,"summary":report["summary"]},indent=2))
    if not observations:
        print("INCONCLUSIVE: no production TREND BUY setups passed in the selected sample; inspect rejection diagnostics.")
    elif any(not any(r["regime"]==g for r in observations) for g in ("BULLISH","NEUTRAL")):
        print("INCONCLUSIVE: only one regime bucket has TREND BUY observations.")

if __name__ == "__main__": main_run()
