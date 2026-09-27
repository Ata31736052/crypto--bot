# ============================================================
# Crypto Signal Bot
# Nobitex-listed USDT coins
# Market data: Binance
# Timeframe: 4H
# Telegram Alerts
# ============================================================

import os
import json
import time
import math
import logging
import tempfile
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import pandas as pd
import numpy as np


# ============================================================
# 1. CONFIG
# ============================================================

class Config:

    # Telegram
    telegram_token = os.getenv("TELEGRAM_TOKEN", "").strip()
    telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    # Binance
    spot_base = "https://data-api.binance.vision"
    futures_base = "https://fapi.binance.com"

    # Nobitex
    nobitex_base = "https://api.nobitex.ir"

    # Timeframe
    timeframe_main = "4h"
    kline_limit = 300

    # Signal thresholds
    min_score = 65
    strong_score = 78

    # Volume
    min_volume_ratio = 1.10
    min_24h_usdt_volume = 5_000_000

    # Indicators
    rsi_period = 14

    macd_fast = 12
    macd_slow = 26
    macd_signal = 9

    atr_period = 14

    # Risk
    account_size_usdt = 1000.0
    risk_pct = 1.0
    max_position_usdt = 1000.0

    # Stop Loss / Take Profit
    sl_atr_multiplier = 1.30
    tp1_rr = 1.50
    tp2_rr = 2.80
    max_sl_percent = 8.0

    # Deduplication
    state_file = "signals_state.json"
    dedup_hours = 8

    # Scanner
    max_workers = 10

    # Telegram
    send_no_signal_report = True

    # Iran timezone
    iran_tz = timezone(
        timedelta(hours=3, minutes=30)
    )


CFG = Config()


# ============================================================
# 2. LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger("crypto-bot")


# ============================================================
# 3. HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 CryptoSignalBot/1.0"
})


def http_get(
    url,
    params=None,
    timeout=15,
    retries=3
):

    last_error = None

    for attempt in range(retries):

        try:

            response = SESSION.get(
                url,
                params=params,
                timeout=timeout
            )

            if response.status_code == 429:

                wait = 2 + attempt * 2

                log.warning(
                    f"Rate limit from {url}. "
                    f"Waiting {wait}s..."
                )

                time.sleep(wait)
                continue

            response.raise_for_status()

            return response.json()

        except Exception as exc:

            last_error = exc

            if attempt < retries - 1:

                time.sleep(
                    1.5 * (attempt + 1)
                )

            else:

                log.error(
                    f"GET failed: {url} | {exc}"
                )

    return None# ============================================================
# 4. TELEGRAM
# ============================================================

def send_telegram(message):

    if not CFG.telegram_token:
        log.error("TELEGRAM_TOKEN is missing")
        return False

    if not CFG.telegram_chat_id:
        log.error("TELEGRAM_CHAT_ID is missing")
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{CFG.telegram_token}/sendMessage"
    )

    payload = {
        "chat_id": CFG.telegram_chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    for attempt in range(3):

        try:

            response = SESSION.post(
                url,
                json=payload,
                timeout=15
            )

            if response.status_code == 429:

                wait = 5 + attempt * 5

                log.warning(
                    f"Telegram rate limit. "
                    f"Waiting {wait}s"
                )

                time.sleep(wait)
                continue

            response.raise_for_status()

            data = response.json()

            if data.get("ok"):
                return True

            log.error(
                f"Telegram error: {data}"
            )

        except Exception as exc:

            log.error(
                f"Telegram send error: {exc}"
            )

            time.sleep(2)

    return False


# ============================================================
# 5. STATE
# ============================================================

def load_state():

    if not os.path.exists(CFG.state_file):
        return {}

    try:

        with open(
            CFG.state_file,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

            if isinstance(data, dict):
                return data

    except Exception as exc:

        log.warning(
            f"Could not load state: {exc}"
        )

    return {}


def save_state(state):

    try:

        directory = os.path.dirname(
            os.path.abspath(
                CFG.state_file
            )
        )

        fd, temp_path = tempfile.mkstemp(
            dir=directory,
            prefix="signals_state_",
            suffix=".tmp"
        )

        with os.fdopen(
            fd,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                state,
                f,
                ensure_ascii=False,
                indent=2
            )

        os.replace(
            temp_path,
            CFG.state_file
        )

        return True

    except Exception as exc:

        log.error(
            f"Could not save state: {exc}"
        )

        return False


def cleanup_state(state):

    now = time.time()

    max_age = CFG.dedup_hours * 3600

    cleaned = {}

    for key, value in state.items():

        try:

            timestamp = float(
                value.get(
                    "timestamp",
                    0
                )
            )

            if now - timestamp < max_age:
                cleaned[key] = value

        except Exception:
            continue

    return cleaned


def signal_already_sent(
    state,
    symbol,
    direction,
    candle_time
):

    key = (
        f"{symbol}_"
        f"{direction}_"
        f"{candle_time}"
    )

    return key in state


def mark_signal_sent(
    state,
    symbol,
    direction,
    candle_time
):

    key = (
        f"{symbol}_"
        f"{direction}_"
        f"{candle_time}"
    )

    state[key] = {
        "timestamp": time.time(),
        "symbol": symbol,
        "direction": direction,
        "candle_time": candle_time
    }


# ============================================================
# 6. NOBITEX COINS
# ============================================================

def get_scan_coins():
def get_scan_coins():

    """
    انتخاب ارزها فقط از بازارهای فعال USDT نوبیتکس.

    قیمت، کندل و حجم:
    Binance
    """

    # --------------------------------------------------------
    # Nobitex API
    # --------------------------------------------------------

    nobitex_urls = [
        "https://apiv2.nobitex.ir/market/stats",
        "https://api.nobitex.ir/market/stats"
    ]

    data = None

    for url in nobitex_urls:

        log.info(
            f"Trying Nobitex API: {url}"
        )

        data = http_get(
            url,
            timeout=20,
            retries=2
        )

        if isinstance(data, dict):

            if data.get("status") == "ok":

                log.info(
                    f"Nobitex API connected: {url}"
                )

                break

            data = None

    if data is None:

        log.error(
            "All Nobitex API endpoints failed"
        )

        return []

    stats = data.get(
        "stats",
        {}
    )


    if not isinstance(stats, dict):

        log.error(
            "Nobitex stats is not a dictionary"
        )

        return []

    nobitex_coins = set()

    for market, info in stats.items():

        try:

            if not isinstance(
                market,
                str
            ):
                continue

            market = market.lower()

            if not market.endswith(
                "-usdt"
            ):
                continue

            if not isinstance(
                info,
                dict
            ):
                continue

            if info.get(
                "isClosed"
            ) is True:
                continue

            base_coin = (
                market
                .split("-")[0]
                .upper()
            )

            if not base_coin:
                continue

            nobitex_coins.add(
                base_coin
            )

        except Exception:
            continue

    log.info(
        f"Nobitex active USDT markets: "
        f"{len(nobitex_coins)}"
    )

    if not nobitex_coins:

        log.error(
            "No active Nobitex USDT markets found"
        )

        return []

    # --------------------------------------------------------
    # Binance Exchange Info
    # --------------------------------------------------------

    exchange_info = http_get(
        "https://api.binance.com/api/v3/exchangeInfo",
        timeout=20
    )

    if not isinstance(
        exchange_info,
        dict
    ):

        log.error(
            "Binance exchangeInfo unavailable"
        )

        return []

    binance_symbols = set()

    for item in exchange_info.get(
        "symbols",
        []
    ):

        try:

            symbol = item.get(
                "symbol"
            )

            status = item.get(
                "status"
            )

            quote_asset = item.get(
                "quoteAsset"
            )

            if not symbol:
                continue

            if status != "TRADING":
                continue

            if quote_asset != "USDT":
                continue

            binance_symbols.add(
                symbol.upper()
            )

        except Exception:
            continue

    # --------------------------------------------------------
    # Nobitex + Binance
    # --------------------------------------------------------

    result = []

    for coin in sorted(
        nobitex_coins
    ):

        symbol = coin + "USDT"

        if symbol not in binance_symbols:
            continue

        result.append({
            "coin": coin,
            "symbol": symbol
        })

    log.info(
        f"Nobitex ∩ Binance USDT markets: "
        f"{len(result)}"
    )

    if not result:
        return []

    # --------------------------------------------------------
    # Binance 24H Volume
    # --------------------------------------------------------

    tickers = http_get(
        CFG.spot_base +
        "/api/v3/ticker/24hr",
        timeout=20
    )

    if not isinstance(
        tickers,
        list
    ):

        log.error(
            "Binance 24h ticker unavailable"
        )

        return []

    volumes = {}

    for ticker in tickers:

        try:

            symbol = ticker.get(
                "symbol"
            )

            if not symbol:
                continue

            symbol = symbol.upper()

            if not symbol.endswith(
                "USDT"
            ):
                continue

            quote_volume = float(
                ticker.get(
                    "quoteVolume",
                    0
                ) or 0
            )

            volumes[symbol] = quote_volume

        except Exception:
            continue

    final_result = []

    for item in result:

        symbol = item["symbol"]

        volume = volumes.get(
            symbol,
            0.0
        )

        if volume < CFG.min_24h_usdt_volume:
            continue

        item["volume_24h"] = volume

        final_result.append(item)

    final_result.sort(
def get_scan_coins():

    """
    انتخاب ارزها فقط از بازارهای فعال USDT نوبیتکس.
    قیمت، کندل و حجم از Binance.
    """

    # --------------------------------------------------------
    # Nobitex API
    # --------------------------------------------------------

    nobitex_urls = [
        "https://apiv2.nobitex.ir/market/stats",
        "https://api.nobitex.ir/market/stats"
    ]

    data = None

    for url in nobitex_urls:

        log.info(
            f"Trying Nobitex API: {url}"
        )

        data = http_get(
            url,
            timeout=20,
            retries=2
        )

        if isinstance(data, dict):

            if data.get("status") == "ok":

                log.info(
                    f"Nobitex API connected: {url}"
                )

                break

            data = None

    if data is None:

        log.error(
            "All Nobitex API endpoints failed"
        )

        return []

    stats = data.get(
        "stats",
        {}
    )

    if not isinstance(stats, dict):

        log.error(
            "Nobitex stats is not a dictionary"
        )

        return []

    nobitex_coins = set()

    for market, info in stats.items():

        try:

            if not isinstance(
                market,
                str
            ):
                continue

            market = market.lower()

            if not market.endswith(
                "-usdt"
            ):
                continue

            if not isinstance(
                info,
                dict
            ):
                continue

            if info.get(
                "isClosed"
            ) is True:
                continue

            base_coin = (
                market
                .split("-")[0]
                .upper()
            )

            if base_coin:
                nobitex_coins.add(
                    base_coin
                )

        except Exception:
            continue

    log.info(
        f"Nobitex active USDT markets: "
        f"{len(nobitex_coins)}"
    )

    if not nobitex_coins:

        log.error(
            "No active Nobitex USDT markets found"
        )

        return []

    # --------------------------------------------------------
    # Binance Exchange Info
    # --------------------------------------------------------

    exchange_info = http_get(
        "https://api.binance.com/api/v3/exchangeInfo",
        timeout=20
    )

    if not isinstance(
        exchange_info,
        dict
    ):

        log.error(
            "Binance exchangeInfo unavailable"
        )

        return []

    binance_symbols = set()

    for item in exchange_info.get(
        "symbols",
        []
    ):

        try:

            symbol = item.get(
                "symbol"
            )

            status = item.get(
                "status"
            )

            quote_asset = item.get(
                "quoteAsset"
            )

            if not symbol:
                continue

            if status != "TRADING":
                continue

            if quote_asset != "USDT":
                continue

            binance_symbols.add(
                symbol.upper()
            )

        except Exception:
            continue

    # --------------------------------------------------------
    # Nobitex + Binance
    # --------------------------------------------------------

    result = []

    for coin in sorted(
        nobitex_coins
    ):

        symbol = coin + "USDT"

        if symbol not in binance_symbols:
            continue

        result.append({
            "coin": coin,
            "symbol": symbol
        })

    log.info(
        f"Nobitex ∩ Binance USDT markets: "
        f"{len(result)}"
    )

    if not result:
        return []

    # --------------------------------------------------------
    # Binance 24H Volume
    # --------------------------------------------------------

    tickers = http_get(
        CFG.spot_base +
        "/api/v3/ticker/24hr",
        timeout=20
    )

    if not isinstance(
        tickers,
        list
    ):

        log.error(
            "Binance 24h ticker unavailable"
        )

        return []

    volumes = {}

    for ticker in tickers:

        try:

            symbol = ticker.get(
                "symbol"
            )

            if not symbol:
                continue

            symbol = symbol.upper()

            if not symbol.endswith(
                "USDT"
            ):
                continue

            quote_volume = float(
                ticker.get(
                    "quoteVolume",
                    0
                ) or 0
            )

            volumes[symbol] = quote_volume

        except Exception:
            continue

    final_result = []

    for item in result:

        symbol = item["symbol"]

        volume = volumes.get(
            symbol,
            0.0
        )

        if volume < CFG.min_24h_usdt_volume:
            continue

        item["volume_24h"] = volume

        final_result.append(item)

    final_result.sort(
        key=lambda x:
        x["volume_24h"],
        reverse=True
    )

    log.info(
        f"Final scan coins: "
        f"{len(final_result)}"
    )

    if final_result:

        preview = ", ".join(
            x["coin"]
            for x in final_result[:30]
        )

        log.info(
            f"Scan preview: {preview}"
        )

    return final_result# ============================================================
# 7. BINANCE KLINES
# ============================================================

def get_klines(symbol):

    data = http_get(
        CFG.spot_base + "/api/v3/klines",
        params={
            "symbol": symbol,
            "interval": CFG.timeframe_main,
            "limit": CFG.kline_limit
        },
        timeout=15
    )

    if not isinstance(data, list):
        return None

    if len(data) < 100:
        return None

    columns = [
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "close_time",
        "quote_volume",
        "trades",
        "taker_base",
        "taker_quote",
        "ignore"
    ]

    try:

        df = pd.DataFrame(
            data,
            columns=columns
        )

        numeric_columns = [
            "open",
            "high",
            "low",
            "close",
            "volume",
            "quote_volume"
        ]

        for col in numeric_columns:

            df[col] = pd.to_numeric(
                df[col],
                errors="coerce"
            )

        df["open_time"] = pd.to_numeric(
            df["open_time"],
            errors="coerce"
        )

        now_ms = int(
            time.time() * 1000
        )

        # حذف کندل فعلی که هنوز بسته نشده
        df = df[
            df["close_time"] <= now_ms
        ].copy()

        df.dropna(
            subset=[
                "open",
                "high",
                "low",
                "close",
                "volume"
            ],
            inplace=True
        )

        if len(df) < 100:
            return None

        return df.reset_index(
            drop=True
        )

    except Exception as exc:

        log.error(
            f"Kline parsing error "
            f"{symbol}: {exc}"
        )

        return None


# ============================================================
# 8. INDICATORS
# ============================================================

def calculate_rsi(
    series,
    period=14
):

    delta = series.diff()

    gain = delta.clip(
        lower=0
    )

    loss = -delta.clip(
        upper=0
    )

    avg_gain = gain.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()

    rs = avg_gain / avg_loss.replace(
        0,
        np.nan
    )

    rsi = 100 - (
        100 / (1 + rs)
    )

    return rsi.fillna(50)


def calculate_atr(
    df,
    period=14
):

    high = df["high"]
    low = df["low"]
    close = df["close"]

    previous_close = close.shift(1)

    tr1 = high - low

    tr2 = (
        high - previous_close
    ).abs()

    tr3 = (
        low - previous_close
    ).abs()

    tr = pd.concat(
        [tr1, tr2, tr3],
        axis=1
    ).max(axis=1)

    return tr.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()


def add_indicators(df):

    df = df.copy()

    close = df["close"]

    # EMA
    df["ema9"] = close.ewm(
        span=9,
        adjust=False
    ).mean()

    df["ema21"] = close.ewm(
        span=21,
        adjust=False
    ).mean()

    df["ema50"] = close.ewm(
        span=50,
        adjust=False
    ).mean()

    df["ema200"] = close.ewm(
        span=200,
        adjust=False
    ).mean()

    # RSI
    df["rsi"] = calculate_rsi(
        close,
        CFG.rsi_period
    )

    # MACD
    ema_fast = close.ewm(
        span=CFG.macd_fast,
        adjust=False
    ).mean()

    ema_slow = close.ewm(
        span=CFG.macd_slow,
        adjust=False
    ).mean()

    df["macd"] = (
        ema_fast - ema_slow
    )

    df["macd_signal"] = (
        df["macd"].ewm(
            span=CFG.macd_signal,
            adjust=False
        ).mean()
    )

    df["macd_hist"] = (
        df["macd"] -
        df["macd_signal"]
    )

    # ATR
    df["atr"] = calculate_atr(
        df,
        CFG.atr_period
    )

    # Volume
    df["volume_ma20"] = (
        df["volume"]
        .rolling(20)
        .mean()
    )

    df["volume_ratio"] = (
        df["volume"] /
        df["volume_ma20"]
    )

    return df


# ============================================================
# 9. BTC CONTEXT
# ============================================================

def get_btc_context():

    df = get_klines(
        "BTCUSDT"
    )

    if df is None:

        return {
            "trend": "UNKNOWN",
            "bullish": False,
            "bearish": False
        }

    df = add_indicators(df)

    row = df.iloc[-1]

    close = float(
        row["close"]
    )

    ema50 = float(
        row["ema50"]
    )

    ema200 = float(
        row["ema200"]
    )

    bullish = (
        close > ema50
        and ema50 > ema200
    )

    bearish = (
        close < ema50
        and ema50 < ema200
    )

    if bullish:

        trend = "BULLISH"

    elif bearish:

        trend = "BEARISH"

    else:

        trend = "NEUTRAL"

    return {
        "trend": trend,
        "bullish": bullish,
        "bearish": bearish
    }


# ============================================================
# 10. RSI DIVERGENCE
# ============================================================

def detect_rsi_divergence(df):

    if len(df) < 30:
        return "NONE"

    prices = df["close"].values
    rsi = df["rsi"].values

    lookback = min(
        40,
        len(df) - 5
    )

    recent_prices = (
        prices[-lookback:]
    )

    recent_rsi = (
        rsi[-lookback:]
    )

    half = lookback // 2

    # --------------------------------------------------------
    # Bullish divergence
    # --------------------------------------------------------

    first_price = np.min(
        recent_prices[:half]
    )

    second_price = np.min(
        recent_prices[half:]
    )

    first_rsi = recent_rsi[
        np.argmin(
            recent_prices[:half]
        )
    ]

    second_rsi = recent_rsi[
        half + np.argmin(
            recent_prices[half:]
        )
    ]

    if (
        second_price < first_price
        and second_rsi > first_rsi
    ):

        return "BULLISH"

    # --------------------------------------------------------
    # Bearish divergence
    # --------------------------------------------------------

    first_price_high = np.max(
        recent_prices[:half]
    )

    second_price_high = np.max(
        recent_prices[half:]
    )

    first_rsi_high = recent_rsi[
        np.argmax(
            recent_prices[:half]
        )
    ]

    second_rsi_high = recent_rsi[
        half + np.argmax(
            recent_prices[half:]
        )
    ]

    if (
        second_price_high > first_price_high
        and second_rsi_high < first_rsi_high
    ):

        return "BEARISH"

    return "NONE"


# ============================================================
# 11. SAFE NUMBER
# ============================================================

def safe_float(
    value,
    default=0.0
):

    try:

        value = float(value)

        if not math.isfinite(value):
            return default

        return value

    except Exception:

        return default# ============================================================
# 12. ANALYZE COIN
# ============================================================

def analyze_coin(
    item,
    btc_context
):

    symbol = item["symbol"]
    coin = item["coin"]

    df = get_klines(symbol)

    if df is None:
        return None

    df = add_indicators(df)

    if len(df) < 100:
        return None

    row = df.iloc[-1]
    prev = df.iloc[-2]

    close = safe_float(
        row["close"]
    )

    ema9 = safe_float(
        row["ema9"]
    )

    ema21 = safe_float(
        row["ema21"]
    )

    ema50 = safe_float(
        row["ema50"]
    )

    ema200 = safe_float(
        row["ema200"]
    )

    rsi = safe_float(
        row["rsi"]
    )

    macd = safe_float(
        row["macd"]
    )

    macd_signal = safe_float(
        row["macd_signal"]
    )

    macd_hist = safe_float(
        row["macd_hist"]
    )

    prev_macd = safe_float(
        prev["macd"]
    )

    prev_macd_signal = safe_float(
        prev["macd_signal"]
    )

    atr = safe_float(
        row["atr"]
    )

    volume_ratio = safe_float(
        row["volume_ratio"],
        1.0
    )

    if close <= 0 or atr <= 0:
        return None

    # --------------------------------------------------------
    # Direction
    # --------------------------------------------------------

    bullish_structure = (
        ema9 > ema21
        and ema21 > ema50
    )

    bearish_structure = (
        ema9 < ema21
        and ema21 < ema50
    )

    if bullish_structure:

        direction = "LONG"

    elif bearish_structure:

        direction = "SHORT"

    else:

        return None

    # --------------------------------------------------------
    # Score
    # --------------------------------------------------------

    score = 0
    reasons = []

    # ========================================================
    # EMA structure
    # ========================================================

    if direction == "LONG":

        if close > ema9:

            score += 12

            reasons.append(
                "قیمت بالای EMA9"
            )

        if ema9 > ema21:

            score += 10

            reasons.append(
                "EMA9 > EMA21"
            )

        if ema21 > ema50:

            score += 8

            reasons.append(
                "EMA21 > EMA50"
            )

        if close > ema200:

            score += 8

            reasons.append(
                "قیمت بالای EMA200"
            )

    else:

        if close < ema9:

            score += 12

            reasons.append(
                "قیمت زیر EMA9"
            )

        if ema9 < ema21:

            score += 10

            reasons.append(
                "EMA9 < EMA21"
            )

        if ema21 < ema50:

            score += 8

            reasons.append(
                "EMA21 < EMA50"
            )

        if close < ema200:

            score += 8

            reasons.append(
                "قیمت زیر EMA200"
            )

    # ========================================================
    # RSI
    # ========================================================

    if direction == "LONG":

        if 45 <= rsi <= 65:

            score += 12

            reasons.append(
                "RSI مناسب خرید"
            )

        elif 35 <= rsi < 45:

            score += 7

            reasons.append(
                "RSI پایین و قابل بررسی"
            )

        elif rsi < 30:

            score += 9

            reasons.append(
                "RSI اشباع فروش"
            )

        elif rsi > 70:

            score -= 5

    else:

        if 35 <= rsi <= 55:

            score += 12

            reasons.append(
                "RSI مناسب فروش"
            )

        elif 55 < rsi <= 65:

            score += 7

        elif rsi > 70:

            score += 9

            reasons.append(
                "RSI اشباع خرید"
            )

        elif rsi < 30:

            score -= 5

    # ========================================================
    # MACD
    # ========================================================

    if direction == "LONG":

        if macd > macd_signal:

            score += 12

            reasons.append(
                "MACD مثبت"
            )

        if macd_hist > 0:

            score += 5

        if (
            prev_macd <= prev_macd_signal
            and macd > macd_signal
        ):

            score += 6

            reasons.append(
                "کراس صعودی MACD"
            )

    else:

        if macd < macd_signal:

            score += 12

            reasons.append(
                "MACD منفی"
            )

        if macd_hist < 0:

            score += 5

        if (
            prev_macd >= prev_macd_signal
            and macd < macd_signal
        ):

            score += 6

            reasons.append(
                "کراس نزولی MACD"
            )

    # ========================================================
    # Volume
    # ========================================================

    if volume_ratio >= CFG.min_volume_ratio:

        score += 10

        reasons.append(
            f"حجم {volume_ratio:.2f}x"
        )

    elif volume_ratio >= 0.90:

        score += 4

    # ========================================================
    # BTC Context
    # ========================================================

    if direction == "LONG":

        if btc_context["bullish"]:

            score += 8

            reasons.append(
                "روند BTC صعودی"
            )

        elif btc_context["bearish"]:

            score -= 8

    else:

        if btc_context["bearish"]:

            score += 8

            reasons.append(
                "روند BTC نزولی"
            )

        elif btc_context["bullish"]:

            score -= 8

    # ========================================================
    # RSI Divergence
    # ========================================================

    divergence = detect_rsi_divergence(df)

    if (
        direction == "LONG"
        and divergence == "BULLISH"
    ):

        score += 8

        reasons.append(
            "واگرایی مثبت RSI"
        )

    elif (
        direction == "SHORT"
        and divergence == "BEARISH"
    ):

        score += 8

        reasons.append(
            "واگرایی منفی RSI"
        )

    # ========================================================
    # Final Score
    # ========================================================

    score = max(
        0,
        min(100, score)
    )

    if score < CFG.min_score:

        return None

    # ========================================================
    # Stop Loss
    # ========================================================

    if direction == "LONG":

        sl = (
            close -
            atr * CFG.sl_atr_multiplier
        )

        risk_distance = (
            close - sl
        )

    else:

        sl = (
            close +
            atr * CFG.sl_atr_multiplier
        )

        risk_distance = (
            sl - close
        )

    if risk_distance <= 0:
        return None

    sl_percent = (
        risk_distance /
        close
    ) * 100

    if sl_percent > CFG.max_sl_percent:

        return None

    # ========================================================
    # Take Profit
    # ========================================================

    if direction == "LONG":

        tp1 = (
            close +
            risk_distance * CFG.tp1_rr
        )

        tp2 = (
            close +
            risk_distance * CFG.tp2_rr
        )

    else:

        tp1 = (
            close -
            risk_distance * CFG.tp1_rr
        )

        tp2 = (
            close -
            risk_distance * CFG.tp2_rr
        )

    # ========================================================
    # Position Size
    # ========================================================

    risk_usdt = (
        CFG.account_size_usdt *
        CFG.risk_pct /
        100
    )

    position_usdt = (
        risk_usdt /
        (sl_percent / 100)
    )

    position_usdt = min(
        position_usdt,
        CFG.max_position_usdt,
        CFG.account_size_usdt
    )

    if position_usdt <= 0:
        return None

    quantity = (
        position_usdt /
        close
    )

    # ========================================================
    # Candle Time
    # ========================================================

    candle_time = int(
        row["open_time"]
    )

    # ========================================================
    # Return
    # ========================================================

    return {

        "coin": coin,
        "symbol": symbol,

        "direction": direction,

        "score": int(score),

        "entry": close,

        "sl": sl,

        "tp1": tp1,

        "tp2": tp2,

        "sl_percent": sl_percent,

        "risk_usdt": risk_usdt,

        "position_usdt": position_usdt,

        "quantity": quantity,

        "rsi": rsi,

        "macd": macd,

        "macd_signal": macd_signal,

        "macd_hist": macd_hist,

        "atr": atr,

        "volume_ratio": volume_ratio,

        "divergence": divergence,

        "btc_trend": btc_context["trend"],

        "candle_time": candle_time,

        "reasons": reasons
    }# ============================================================
# 13. FORMAT SIGNAL
# ============================================================

def format_signal(signal):

    direction = signal["direction"]

    if direction == "LONG":
        title = "🟢 <b>LONG SIGNAL</b>"
    else:
        title = "🔴 <b>SHORT SIGNAL</b>"

    score = signal["score"]

    if score >= CFG.strong_score:
        quality = "🔥 STRONG"
    else:
        quality = "⚡ NORMAL"

    candle_dt = datetime.fromtimestamp(
        signal["candle_time"] / 1000,
        tz=CFG.iran_tz
    )

    reasons = signal["reasons"][:7]

    reasons_text = "\n".join(
        f"• {x}"
        for x in reasons
    )

    message = f"""
{title}
<b>{signal["coin"]}/USDT</b>
کیفیت: <b>{quality}</b>

📊 Score: <b>{score}/100</b>

💰 Entry:
<b>{signal["entry"]:.8g}</b>

🛑 Stop Loss:
<b>{signal["sl"]:.8g}</b>
({signal["sl_percent"]:.2f}%)

🎯 TP1:
<b>{signal["tp1"]:.8g}</b>

🎯 TP2:
<b>{signal["tp2"]:.8g}</b>

📈 RSI:
<b>{signal["rsi"]:.2f}</b>

📊 Volume:
<b>{signal["volume_ratio"]:.2f}x</b>

📉 BTC Trend:
<b>{signal["btc_trend"]}</b>

🔄 RSI Divergence:
<b>{signal["divergence"]}</b>

💵 Risk:
<b>${signal["risk_usdt"]:.2f}</b>

📦 Position:
<b>${signal["position_usdt"]:.2f}</b>

🔢 Quantity:
<b>{signal["quantity"]:.8g}</b>

<b>دلایل:</b>
{reasons_text}

⏰ Candle:
{candle_dt.strftime("%Y-%m-%d %H:%M")}

⚠️ این پیام صرفاً سیگنال و محاسبه ریسک است؛ معامله خودکار انجام نمی‌شود.
""".strip()

    return message


# ============================================================
# 14. SUMMARY
# ============================================================

def format_no_signal_summary(
    total_coins,
    analyzed,
    signals,
    btc_context
):

    now = datetime.now(
        CFG.iran_tz
    )

    message = f"""
📊 <b>Crypto 4H Scan Report</b>

⏰ {now.strftime("%Y-%m-%d %H:%M")}

🪙 Nobitex USDT coins:
<b>{total_coins}</b>

🔎 Analyzed:
<b>{analyzed}</b>

🚨 New signals:
<b>{signals}</b>

₿ BTC Trend:
<b>{btc_context["trend"]}</b>

ℹ️ در این اسکن سیگنال جدیدی با امتیاز حداقل {CFG.min_score} پیدا نشد.
""".strip()

    return message


# ============================================================
# 15. MAIN
# ============================================================

def main():

    log.info(
        "======================================"
    )

    log.info(
        "Starting Crypto Signal Bot"
    )

    log.info(
        f"Timeframe: {CFG.timeframe_main}"
    )

    log.info(
        "======================================"
    )

    # --------------------------------------------------------
    # Telegram configuration
    # --------------------------------------------------------

    if not CFG.telegram_token:

        log.error(
            "TELEGRAM_TOKEN is not configured"
        )

        return

    if not CFG.telegram_chat_id:

        log.error(
            "TELEGRAM_CHAT_ID is not configured"
        )

        return

    # --------------------------------------------------------
    # Load state
    # --------------------------------------------------------

    state = load_state()

    state = cleanup_state(
        state
    )

    # --------------------------------------------------------
    # Get Nobitex coins
    # --------------------------------------------------------

    coins = get_scan_coins()

    if not coins:

        log.error(
            "No coins available for scanning"
        )

        send_telegram(
            "⚠️ <b>Crypto Bot</b>\n\n"
            "لیست ارزهای قابل اسکن از نوبیتکس دریافت نشد."
        )

        return

    # --------------------------------------------------------
    # BTC context
    # --------------------------------------------------------

    btc_context = get_btc_context()

    log.info(
        f"BTC trend: "
        f"{btc_context['trend']}"
    )

    # --------------------------------------------------------
    # Analyze coins
    # --------------------------------------------------------

    results = []

    analyzed = 0

    with ThreadPoolExecutor(
        max_workers=CFG.max_workers
    ) as executor:

        futures = {
            executor.submit(
                analyze_coin,
                item,
                btc_context
            ): item
            for item in coins
        }

        for future in as_completed(
            futures
        ):

            item = futures[future]

            try:

                result = future.result()

                analyzed += 1

                if result is not None:

                    results.append(
                        result
                    )

            except Exception as exc:

                log.error(
                    f"Analysis error "
                    f"{item.get('symbol')}: "
                    f"{exc}"
                )

    # --------------------------------------------------------
    # Sort by score
    # --------------------------------------------------------

    results.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    log.info(
        f"Analysis finished. "
        f"Candidates: {len(results)}"
    )

    # --------------------------------------------------------
    # Send signals
    # --------------------------------------------------------

    sent_count = 0

    for signal in results:

        # حداکثر ۶ سیگنال جدید در هر اجرا
        if sent_count >= 6:
            break

        if signal_already_sent(
            state,
            signal["symbol"],
            signal["direction"],
            signal["candle_time"]
        ):

            log.info(
                f"Duplicate ignored: "
                f"{signal['symbol']} "
                f"{signal['direction']}"
            )

            continue

        message = format_signal(
            signal
        )

        success = send_telegram(
            message
        )

        if success:

            mark_signal_sent(
                state,
                signal["symbol"],
                signal["direction"],
                signal["candle_time"]
            )

            sent_count += 1

            log.info(
                f"Signal sent: "
                f"{signal['symbol']} "
                f"{signal['direction']} "
                f"score={signal['score']}"
            )

        else:

            log.error(
                f"Signal NOT sent: "
                f"{signal['symbol']}"
            )

    # --------------------------------------------------------
    # Save state
    # --------------------------------------------------------

    save_state(
        state
    )

    # --------------------------------------------------------
    # No signal report
    # --------------------------------------------------------

    if (
        sent_count == 0
        and CFG.send_no_signal_report
    ):

        summary = format_no_signal_summary(
            len(coins),
            analyzed,
            sent_count,
            btc_context
        )

        send_telegram(
            summary
        )

    # --------------------------------------------------------
    # Final log
    # --------------------------------------------------------

    log.info(
        "======================================"
    )

    log.info(
        f"Coins: {len(coins)}"
    )

    log.info(
        f"Analyzed: {analyzed}"
    )

    log.info(
        f"Candidates: {len(results)}"
    )

    log.info(
        f"New signals sent: {sent_count}"
    )

    log.info(
        "Bot finished successfully"
    )

    log.info(
        "======================================"
    )


# ============================================================
# 16. RUN
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        log.info(
            "Bot stopped by user."
        )

    except Exception as exc:

        log.exception(
            f"Fatal error: {exc}"
        )

        try:

            send_telegram(
                "🚨 <b>Crypto Bot Error</b>\n\n"
                f"<code>{str(exc)[:1000]}</code>"
            )

        except Exception:

            pass

        raise
