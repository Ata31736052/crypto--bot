import os
import sys
import json
import time
import logging
from dataclasses import dataclass, field
from typing import Optional
import requests
import pandas as pd
import numpy as np
from concurrent.futures import ThreadPoolExecutor

# ---------- Logging ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
    force=True,
)
log = logging.getLogger("swing_bot")

# ---------- Config ----------
@dataclass
class Config:
    telegram_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_TOKEN", ""))
    telegram_chat_id: str = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", ""))

    timeframe_main: str = "4h"
    kline_limit: int = 500

    min_score: int = 58
    strong_score: int = 78
    min_volume_ratio: float = 0.7
    min_24h_usdt_volume: float = 10_000_000

    atr_period: int = 14
    sl_atr_multiplier: float = 1.50
    max_sl_pct: float = 7.0
    tp1_rr: float = 1.60
    tp2_rr: float = 2.80

    rsi_overbought: float = 65
    rsi_oversold: float = 35

    divergence_lookback: int = 40
    divergence_min_gap: int = 3
    divergence_max_gap: int = 20

    vol_regime_threshold: float = 1.10
    max_concurrent_signals: int = 6
    state_file: str = "signals_state.json"
    spot_base: str = "https://data-api.binance.vision"
    fut_base: str = "https://fapi.binance.com"
    parallel_workers: int = 20
    dedup_hours: int = 6
    risk_usd: float = 10.0
    account_size: float = 1000.0
    risk_pct: float = 1.0

CFG = Config()

# ---------- HTTP ----------
session = requests.Session()

def http_get(url: str, params: dict = None, retries: int = 2, timeout: int = 6):
    for attempt in range(retries + 1):
        try:
            r = session.get(url, params=params, timeout=timeout)
            if r.status_code == 429:
                wait = (2 ** attempt) + np.random.uniform(0, 0.5)
                log.warning(f"Rate limited, sleeping {wait:.2f}s")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt < retries:
                time.sleep(1 + attempt)
            else:
                log.error(f"[HTTP FAIL] {url}: {e}")
    return None

# ---------- Telegram ----------
def send_telegram(text: str) -> bool:
    if not CFG.telegram_token or not CFG.telegram_chat_id:
        log.warning("Telegram credentials missing")
        return False
    url = f"https://api.telegram.org/bot{CFG.telegram_token}/sendMessage"
    payload = {
        "chat_id": CFG.telegram_chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        res = session.post(url, json=payload, timeout=10)
        if res.status_code == 429:
            retry_after = res.json().get("parameters", {}).get("retry_after", 5)
            time.sleep(retry_after + 1)
            return send_telegram(text)
        return res.status_code == 200
    except Exception as e:
        log.error(f"Telegram send error: {e}")
        return False

# ---------- State ----------
def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}

def save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.error(f"[SAVE] {path}: {e}")

def load_state() -> dict:
    return load_json(CFG.state_file, default={"signals": {}})

def cleanup_state(state: dict, days: int = 7) -> dict:
    cutoff = time.time() - days * 86400
    state["signals"] = {
        k: v for k, v in state.get("signals", {}).items() if v > cutoff
    }
    return state

# ---------- Utils ----------
def format_num(n) -> str:
    try:
        n = float(n)
        if n >= 1_000_000_000: return f"{n/1_000_000_000:.2f}B"
        if n >= 1_000_000:     return f"{n/1_000_000:.2f}M"
        if n >= 1_000:         return f"{n/1_000:.2f}K"
        return f"{n:.2f}"
    except Exception:
        return str(n)

# ---------- Market data ----------
def get_fear_greed_index():
    data = http_get("https://api.alternative.me/fng/?limit=1")
    try:
        if data and "data" in data:
            val = int(data["data"][0]["value"])
            cls = data["data"][0]["value_classification"]
            return val, cls
    except Exception:
        pass
    return 50, "Neutral"

def get_scan_coins() -> list:
    coins = [
        "BTC","ETH","SOL","BNB","XRP","TON","ADA","DOGE",
        "AVAX","LINK","DOT","LTC","BCH","ETC","XLM","UNI",
        "FIL","TRX","ATOM","NEAR","AAVE","SUI","APT","ARB",
        "OP","SEI","INJ","TIA","STX","ALGO","PEPE","WIF",
        "BONK","FLOKI","SHIB","MEME","NOT","ORDI","BOME",
        "POL","RENDER","ICP","KAS","IMX","GRT","HBAR",
        "ENA","PENDLE","JUP","PYTH","W","MANTA","ALT",
        "STRK","AXL","AEVO","REZ","BB","IO","ZK",
        "LISTA","DOGS","CATI","HMSTR","EIGEN","SCR",
        "PNUT","ACT","GOAT","CHZ","SAND","MANA","GALA",
        "ENJ","AXS","THETA","FTM","SNX","CRV","MKR","COMP",
    ]
    uniq = sorted(set(coins))

    tickers = http_get(CFG.spot_base + "/api/v3/ticker/24hr")
    vols = {}
    if tickers and isinstance(tickers, list):
        for t in tickers:
            s = t.get("symbol")
            if s and s.endswith("USDT"):
                try:
                    vols[s] = float(t.get("quoteVolume", 0) or 0)
                except Exception:
                    pass

    result = []
    for c in uniq:
        sym = c + "USDT"
        if vols.get(sym, 0) >= CFG.min_24h_usdt_volume:
            result.append({"coin": c, "symbol": sym})
    return result

KLINE_COLS = [
    "open_time","open","high","low","close","volume",
    "close_time","quote_vol","trades","taker_buy_base","taker_buy_quote","ignore",
]

def get_klines(symbol: str, interval: str = "4h", limit: int = 500) -> Optional[pd.DataFrame]:
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    data = http_get(CFG.spot_base + "/api/v3/klines", params=params)
    if not data or len(data) < 100:
        return None
    df = pd.DataFrame(data, columns=KLINE_COLS)
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    # حذف کندل جاری (ناقص) برای جلوگیری از repaint
    return df.iloc[:-1].reset_index(drop=True)

def fetch_futures_metrics_batch(symbols: list, top_n: int = 25) -> dict:
    """
    فقط برای top_n نماد اول metrics می‌گیریم تا rate limit نخوریم.
    """
    symbols = symbols[:top_n]
    metrics = {
        sym: {"funding_rate": 0.0, "open_interest": 0.0, "oi_change": 0.0}
        for sym in symbols
    }

    # funding rates
    pi = http_get(CFG.fut_base + "/fapi/v1/premiumIndex", timeout=5)
    if isinstance(pi, list):
        for item in pi:
            s = item.get("symbol")
            if s in metrics:
                try:
                    metrics[s]["funding_rate"] = float(item.get("lastFundingRate", 0) or 0)
                except Exception:
                    pass

    def fetch_single_oi(sym):
        res = {"symbol": sym, "oi": 0.0, "oi_change": 0.0}
        oi = http_get(CFG.fut_base + "/fapi/v1/openInterest",
                      params={"symbol": sym}, timeout=3)
        if oi and isinstance(oi, dict):
            try:
                res["oi"] = float(oi.get("openInterest", 0) or 0)
            except Exception:
                pass
        hist = http_get(CFG.fut_base + "/futures/data/openInterestHist",
                        params={"symbol": sym, "period": "4h", "limit": 2},
                        timeout=3)
        if hist and isinstance(hist, list) and len(hist) >= 2:
            try:
                p = float(hist[-2].get("sumOpenInterest", 0) or 0)
                c = float(hist[-1].get("sumOpenInterest", 0) or 0)
                if p > 0:
                    res["oi_change"] = ((c - p) / p) * 100
            except Exception:
                pass
        return res

    with ThreadPoolExecutor(max_workers=10) as ex:
        for r in ex.map(fetch_single_oi, symbols):
            s = r["symbol"]
            if s in metrics:
                metrics[s]["open_interest"] = r["oi"]
                metrics[s]["oi_change"] = r["oi_change"]
    return metrics

# ---------- Indicators ----------
def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    close = df["close"]

    df["ema9"]   = close.ewm(span=9,   adjust=False).mean()
    df["ema21"]  = close.ewm(span=21,  adjust=False).mean()
    df["ema50"]  = close.ewm(span=50,  adjust=False).mean()
    df["ema200"] = close.ewm(span=200, adjust=False).mean()

    # RSI
    d = close.diff()
    gain = d.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-d.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))

    # MACD
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df["macd"] = ema12 - ema26
    df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    # ATR
    high, low = df["high"], df["low"]
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low  - close.shift()).abs(),
    ], axis=1).max(axis=1)
    df["atr"] = tr.rolling(CFG.atr_period).mean()
    df["atr_avg50"] = df["atr"].rolling(50).mean()

    # Volume
    df["vol_avg20"] = df["volume"].rolling(20).mean()
    df["vol_ratio"] = df["volume"] / df["vol_avg20"]

    return df

# ---------- Divergence ----------
def _find_extrema(series: pd.Series, order: int, mode: str) -> list:
    vals = series.values
    idxs = []
    for i in range(order, len(vals) - order):
        w = vals[i - order:i + order + 1]
        if not np.isfinite(vals[i]):
            continue
        if mode == "min" and vals[i] == w.min():
            idxs.append(i)
        elif mode == "max" and vals[i] == w.max():
            idxs.append(i)
    return idxs

def bull_div(df: pd.DataFrame) -> bool:
    try:
        r = df.tail(CFG.divergence_lookback).reset_index(drop=True)
        if len(r) < 20:
            return False
        idx = _find_extrema(r["low"], 2, "min")
        if len(idx) < 2:
            return False
        i1, i2 = idx[-2], idx[-1]
        gap = i2 - i1
        if not (CFG.divergence_min_gap <= gap <= CFG.divergence_max_gap):
            return False
        return (r.loc[i2, "low"] < r.loc[i1, "low"]
                and r.loc[i2, "rsi"] > r.loc[i1, "rsi"] + 2.5)
    except Exception:
        return False

def bear_div(df: pd.DataFrame) -> bool:
    try:
        r = df.tail(CFG.divergence_lookback).reset_index(drop=True)
        if len(r) < 20:
            return False
        idx = _find_extrema(r["high"], 2, "max")
        if len(idx) < 2:
            return False
        i1, i2 = idx[-2], idx[-1]
        gap = i2 - i1
        if not (CFG.divergence_min_gap <= gap <= CFG.divergence_max_gap):
            return False
        return (r.loc[i2, "high"] > r.loc[i1, "high"]
                and r.loc[i2, "rsi"] < r.loc[i1, "rsi"] - 2.5)
    except Exception:
        return False

# ---------- Analyzer ----------
def analyze_coin(df: pd.DataFrame, symbol: str,
                 fng_val: int = 50,
                 btc_bullish: bool = True) -> Optional[dict]:
    try:
        df = add_indicators(df)
        last = df.iloc[-1]

        close      = float(last["close"])
        atr        = float(last["atr"])
        atr_avg50  = float(last["atr_avg50"]) if np.isfinite(last["atr_avg50"]) else atr
        rsi        = float(last["rsi"])
        vr         = float(last["vol_ratio"])
        macd_hist  = float(last["macd_hist"])
        e9, e21    = float(last["ema9"]), float(last["ema21"])

        if not all(np.isfinite([close, atr, rsi, vr, macd_hist])) or atr <= 0:
            return None
        if vr < CFG.min_volume_ratio:
            return None

        # جهت
        if e9 > e21:
            direction = "BUY"
        elif e9 < e21:
            direction = "SELL"
        else:
            return None

        regime = "📈 Trending" if (atr / atr_avg50) >= CFG.vol_regime_threshold else "🔄 Ranging"

        # ⚠️ فیلتر RSI فقط در حالت رنج اعمال می‌شود
        if regime == "🔄 Ranging":
            if direction == "BUY" and rsi > CFG.rsi_overbought:
                return None
            if direction == "SELL" and rsi < CFG.rsi_oversold:
                return None

        # امتیازدهی — از 0 شروع می‌کنیم
        score = 0
        reasons = []

        score += 15
        reasons.append(f"• تقاطع {'صعودی' if direction=='BUY' else 'نزولی'} EMA 9/21")

        # RSI safe
        if direction == "BUY" and rsi < CFG.rsi_overbought:
            score += 10
            reasons.append(f"• RSI امن ({rsi:.1f})")
        elif direction == "SELL" and rsi > CFG.rsi_oversold:
            score += 10
            reasons.append(f"• RSI امن ({rsi:.1f})")

        # MACD
        if direction == "BUY" and macd_hist > 0:
            score += 15
            reasons.append("• مومنتوم صعودی (MACD)")
        elif direction == "SELL" and macd_hist < 0:
            score += 15
            reasons.append("• مومنتوم نزولی (MACD)")

        # هم‌جهتی با BTC
        if btc_bullish and direction == "BUY":
            score += 10
            reasons.append("• هم‌جهت با BTC (بالای EMA200)")
        elif not btc_bullish and direction == "SELL":
            score += 10
            reasons.append("• هم‌جهت با BTC (زیر EMA200)")
        elif btc_bullish and direction == "SELL":
            score -= 10
            reasons.append("• ⚠️ مخالف جهت BTC")

        # واگرایی
        if direction == "BUY" and bull_div(df):
            score += 20
            reasons.append("• واگرایی صعودی")
        elif direction == "SELL" and bear_div(df):
            score += 20
            reasons.append("• واگرایی نزولی")

        # حجم
        if vr >= 1.2:
            score += 10
            reasons.append(f"• حجم قوی ({vr:.2f}x)")
        elif vr >= 1.0:
            score += 5
            reasons.append(f"• حجم نرمال ({vr:.2f}x)")

        score = max(0, min(100, score))

        if score < CFG.min_score:
            return None

        # SL / TP
        if direction == "BUY":
            sl = close - atr * CFG.sl_atr_multiplier
            risk = close - sl
            tp1 = close + risk * CFG.tp1_rr
            tp2 = close + risk * CFG.tp2_rr
        else:
            sl = close + atr * CFG.sl_atr_multiplier
            risk = sl - close
            tp1 = close - risk * CFG.tp1_rr
            tp2 = close - risk * CFG.tp2_rr

        sl_pct = ((sl - close) / close) * 100
        if abs(sl_pct) > CFG.max_sl_pct:
            return None

        return {
            "symbol": symbol,
            "direction": direction,
            "score": score,
            "close": close,
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "sl_pct": sl_pct,
            "tp1_pct": ((tp1 - close) / close) * 100,
            "tp2_pct": ((tp2 - close) / close) * 100,
            "rsi": rsi,
            "volume_ratio": vr,
            "regime": regime,
            "reasons": reasons,
            "strong": score >= CFG.strong_score,
        }
    except Exception as e:
        log.debug(f"analyze_coin error {symbol}: {e}")
        return None

# ---------- Dedup ----------
def filter_dups(signals: list, state: dict):
    now = time.time()
    sent = state.setdefault("signals", {})
    out = []
    for sig in signals:
        key = f"{sig['symbol']}_{sig['direction']}"
        if (now - sent.get(key, 0)) < CFG.dedup_hours * 3600:
            continue
        out.append(sig)
        sent[key] = now
    return out, state

# ---------- Message ----------
def build_msg(sig: dict, fng_val: int) -> str:
    emoji = "🟢" if sig["direction"] == "BUY" else "🔴"
    tag = "#" + sig["symbol"].replace("USDT", "")

    risk_usd = CFG.risk_usd
    pr = abs(sig["close"] - sig["sl"])
    units = (risk_usd / pr) if pr > 0 else 0
    notional = units * sig["close"]

    L = [
        f"{emoji} <b>سیگنال نوسانی ({CFG.timeframe_main})</b>",
        "",
        f"نماد: <b>{tag}</b>",
        f"جهت: <b>{sig['direction']}</b>",
        f"ورود: <code>{sig['close']:.4f}</code>",
        "",
        f"🛑 SL: <code>{sig['sl']:.4f}</code> ({sig['sl_pct']:+.2f}%)",
        f"🎯 TP1: <code>{sig['tp1']:.4f}</code> ({sig['tp1_pct']:+.2f}%)",
        f"🎯 TP2: <code>{sig['tp2']:.4f}</code> ({sig['tp2_pct']:+.2f}%)",
        "",
        f"پیشنهاد حجم (سرمایه ${CFG.account_size:.0f}، ریسک {CFG.risk_pct}%):",
        f"🔹 <code>{units:.4f}</code> واحد | ~$<code>{notional:.2f}</code>",
        "",
        f"📊 امتیاز: <b>{sig['score']}/100</b> | RSI: {sig['rsi']:.1f}",
        f"📦 حجم: {sig['volume_ratio']:.2f}x",
    ]

    fr = sig.get("funding_rate", 0)
    if fr != 0.0:
        L.append(f"⚡ فاندینگ: <code>{fr*100:.4f}%</code>")

    oi = sig.get("open_interest", 0)
    if oi > 0:
        L.append(f"💼 OI: <code>{format_num(oi)}</code> | {sig.get('oi_change', 0):+.1f}%")

    L.extend([
        f"🌊 رژیم: {sig['regime']}",
        f"😱 F&G: {fng_val}",
        "",
        "دلایل تایید:",
    ])
    L.extend(sig["reasons"])
    L.append("—" * 20)
    return "\n".join(L)

# ---------- Main ----------
def main():
    log.info("=" * 50)
    log.info("BOT STARTED - FLEXIBLE SWING")

    send_telegram("🤖 ربات نوسان‌گیر روشن شد...")

    fng_val, fng_cls = get_fear_greed_index()
    log.info(f"Fear & Greed: {fng_val} ({fng_cls})")

    # BTC context
    btc_bullish = True
    btc_df = get_klines("BTCUSDT", CFG.timeframe_main, 200)
    if btc_df is not None and len(btc_df) > 50:
        btc_df = add_indicators(btc_df)
        btc_last = btc_df.iloc[-1]
        if np.isfinite(btc_last["ema200"]) and btc_last["close"] < btc_last["ema200"]:
            btc_bullish = False
    log.info(f"BTC Bullish: {btc_bullish}")

    coins = get_scan_coins()
    if not coins:
        log.warning("No coins to scan")
        return
    log.info(f"Scanning {len(coins)} coins...")

    # 1) آنالیز همه کوین‌ها
    signals = []

    def process_coin(item):
        symbol = item["symbol"]
        df = get_klines(symbol, CFG.timeframe_main, CFG.kline_limit)
        if df is None or len(df) < 100:
            return None
        return analyze_coin(df, symbol, fng_val, btc_bullish)

    with ThreadPoolExecutor(max_workers=CFG.parallel_workers) as ex:
        for r in ex.map(process_coin, coins):
            if r is not None:
                signals.append(r)

    # 2) sort و انتخاب top N *قبل از* fetch futures metrics
    signals.sort(key=lambda x: x["score"], reverse=True)
    signals = signals[:CFG.max_concurrent_signals]

    # 3) فقط برای سیگنال‌های نهایی metrics می‌گیریم
    if signals:
        symbols = [s["symbol"] for s in signals]
        fut_metrics = fetch_futures_metrics_batch(symbols, top_n=len(symbols))
        for sig in signals:
            m = fut_metrics.get(sig["symbol"], {})
            sig["funding_rate"]  = m.get("funding_rate", 0.0)
            sig["open_interest"] = m.get("open_interest", 0.0)
            sig["oi_change"]     = m.get("oi_change", 0.0)

    # 4) dedup و ذخیره state
    state = load_state()
    state = cleanup_state(state, days=7)
    fresh_signals, state = filter_dups(signals, state)
    save_json(CFG.state_file, state)

    log.info(f"Signals found: {len(signals)}, fresh after dedup: {len(fresh_signals)}")

    if not fresh_signals:
        send_telegram("ℹ️ اسکن تمام شد. سیگنال تازه‌ای پیدا نشد.")
        return
        header = (
    f"🤖 <b>گزارش نوسان‌گیری ({CFG.timeframe_main})</b>\n"
    f"📅 شاخص ترس و طمع: <b>{fng_val} ({fng_cls})</b>\n"
    f"🔍 سیگنال‌های تاییدشده: <b>{len(fresh_signals)}</b>"
            )
  
