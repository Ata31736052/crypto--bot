# ==============================
# Crypto Signal Bot
# Nobitex-listed coins
# Market data: Binance
# Main timeframe: 4H
# ==============================

import os
import json
import time
import logging
import requests
import pandas as pd
import numpy as np

from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed


# =========================================================
# CONFIG
# =========================================================

class Config:

    telegram_token = os.getenv("TELEGRAM_TOKEN", "").strip()
    telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    binance_base = "https://data-api.binance.vision"

    nobitex_urls = [
        "https://apiv2.nobitex.ir/market/stats",
        "https://api.nobitex.ir/market/stats",
    ]

    # Main analysis timeframe
    timeframe = "4h"

    # Binance candles
    kline_limit = 300

    # Signal filters
    min_score = 75
    strong_score = 84

    # Volume
    min_volume_ratio = 0.80
    strong_volume_ratio = 1.10

    # Minimum 24h USDT volume
    min_24h_usdt_volume = 5_000_000

    # RSI
    rsi_period = 14

    # MACD
    macd_fast = 12
    macd_slow = 26
    macd_signal = 9

    # ATR
    atr_period = 14

    # Risk management
    account_size_usdt = 1000.0
    risk_percent = 1.0
    max_position_usdt = 1000.0

    # Stop / Take Profit
    sl_atr_multiplier = 1.40
    tp1_rr = 1.80
    tp2_rr = 3.00

    # Maximum allowed stop loss
    max_sl_percent = 6.0

    # Maximum distance from EMA21
    max_distance_from_ema21 = 4.0

    # Maximum signals per run
    max_signals_per_run = 3

    # State
    state_file = "signals_state.json"
    dedup_hours = 8

    # Threads
    max_workers = 10

    # Send report even when there are no signals
    send_no_signal_report = True

    # Iran timezone
    iran_timezone = timezone(
        timedelta(hours=3, minutes=30)
    )


CFG = Config()


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("crypto_bot")


# =========================================================
# HTTP SESSION
# =========================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 CryptoSignalBot/1.0"
})


# =========================================================
# HELPERS
# =========================================================

def safe_float(value, default=0.0):

    try:
        if value is None:
            return default

        return float(value)

    except Exception:
        return default


def http_get(url, params=None, timeout=20):

    for attempt in range(3):

        try:

            response = SESSION.get(
                url,
                params=params,
                timeout=timeout
            )

            if response.status_code == 429:

                wait_time = 2 + attempt * 2

                logger.warning(
                    "HTTP 429. Waiting %s seconds...",
                    wait_time
                )

                time.sleep(wait_time)

                continue

            response.raise_for_status()

            return response.json()

        except Exception as e:

            logger.warning(
                "HTTP error attempt %s/3: %s",
                attempt + 1,
                e
            )

            if attempt < 2:
                time.sleep(2)

    return None


# =========================================================
# TELEGRAM
# =========================================================

def send_telegram(message):

    if not CFG.telegram_token:
        logger.error("TELEGRAM_TOKEN is missing")
        return False

    if not CFG.telegram_chat_id:
        logger.error("TELEGRAM_CHAT_ID is missing")
        return False

    url = (
        "https://api.telegram.org/bot"
        + CFG.telegram_token
        + "/sendMessage"
    )

    payload = {
        "chat_id": CFG.telegram_chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    try:

        response = SESSION.post(
            url,
            json=payload,
            timeout=20
        )

        response.raise_for_status()

        data = response.json()

        if data.get("ok"):

            logger.info("Telegram message sent")
            return True

        logger.error(
            "Telegram error: %s",
            data
        )

        return False

    except Exception as e:

        logger.error(
            "Telegram send failed: %s",
            e
        )

        return False# =========================================================
# STATE MANAGEMENT
# =========================================================

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

        return {}

    except Exception as e:

        logger.warning(
            "Could not load state: %s",
            e
        )

        return {}


def save_state(state):

    try:

        with open(
            CFG.state_file,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                state,
                f,
                ensure_ascii=False,
                indent=2
            )

        return True

    except Exception as e:

        logger.error(
            "Could not save state: %s",
            e
        )

        return False


# =========================================================
# NOBITEX COINS
# =========================================================

def get_nobitex_coins():

    for url in CFG.nobitex_urls:

        logger.info(
            "Trying Nobitex API: %s",
            url
        )

        data = http_get(url)

        if not data:
            continue

        try:

            stats = data.get("stats", {})

            if not isinstance(stats, dict):
                continue

            coins = set()

            for market, info in stats.items():

                market_lower = str(
                    market
                ).lower()

                if not market_lower.endswith("-usdt"):
                    continue

                if not isinstance(info, dict):
                    continue

                is_closed = info.get(
                    "isClosed",
                    False
                )

                if is_closed:
                    continue

                base = market_lower.replace(
                    "-usdt",
                    ""
                ).upper()

                if base:
                    coins.add(base)

            if coins:

                logger.info(
                    "Nobitex API connected: %s",
                    url
                )

                logger.info(
                    "Nobitex active USDT markets: %s",
                    len(coins)
                )

                return coins

        except Exception as e:

            logger.warning(
                "Nobitex parsing error: %s",
                e
            )

    logger.error(
        "Could not get Nobitex coin list"
    )

    return set()


# =========================================================
# BINANCE SYMBOLS
# =========================================================

def get_binance_symbols():

    url = (
        CFG.binance_base
        + "/api/v3/exchangeInfo"
    )

    data = http_get(url)

    if not data:
        return set()

    symbols = set()

    try:

        for item in data.get(
            "symbols",
            []
        ):

            if item.get("status") != "TRADING":
                continue

            if item.get("quoteAsset") != "USDT":
                continue

            base = item.get(
                "baseAsset"
            )

            if base:
                symbols.add(
                    str(base).upper()
                )

    except Exception as e:

        logger.error(
            "Binance symbol parsing error: %s",
            e
        )

    logger.info(
        "Binance active USDT symbols: %s",
        len(symbols)
    )

    return symbols


# =========================================================
# BINANCE 24H VOLUME
# =========================================================

def get_binance_volumes():

    url = (
        CFG.binance_base
        + "/api/v3/ticker/24hr"
    )

    data = http_get(url)

    if not data:
        return {}

    volumes = {}

    try:

        for item in data:

            symbol = str(
                item.get("symbol", "")
            ).upper()

            if not symbol.endswith("USDT"):
                continue

            quote_volume = safe_float(
                item.get("quoteVolume")
            )

            base = symbol[:-4]

            volumes[base] = quote_volume

    except Exception as e:

        logger.error(
            "Binance volume parsing error: %s",
            e
        )

    return volumes


# =========================================================
# FINAL SCAN LIST
# =========================================================

def get_scan_coins():

    nobitex_coins = get_nobitex_coins()

    if not nobitex_coins:
        return []

    binance_coins = get_binance_symbols()

    if not binance_coins:
        return []

    volumes = get_binance_volumes()

    common = (
        nobitex_coins
        & binance_coins
    )

    logger.info(
        "Nobitex + Binance markets: %s",
        len(common)
    )

    final_coins = []

    for coin in common:

        volume = safe_float(
            volumes.get(coin, 0)
        )

        if volume < CFG.min_24h_usdt_volume:
            continue

        final_coins.append(coin)

    final_coins.sort(
        key=lambda x: volumes.get(x, 0),
        reverse=True
    )

    logger.info(
        "Final scan list after liquidity filter: %s",
        len(final_coins)
    )

    if final_coins:

        logger.info(
            "Scan preview: %s",
            ", ".join(final_coins[:30])
        )

    return final_coins# =========================================================
# BINANCE KLINES
# =========================================================

def get_klines(symbol, interval=None, limit=None):

    if interval is None:
        interval = CFG.timeframe

    if limit is None:
        limit = CFG.kline_limit

    url = (
        CFG.binance_base
        + "/api/v3/klines"
    )

    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": limit
    }

    data = http_get(
        url,
        params=params
    )

    if not data:
        return None

    try:

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
            "taker_buy_base",
            "taker_buy_quote",
            "ignore"
        ]

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

        df["close_time"] = pd.to_numeric(
            df["close_time"],
            errors="coerce"
        )

        # Remove current unfinished candle
        now_ms = int(
            time.time() * 1000
        )

        df = df[
            df["close_time"] <= now_ms
        ].copy()

        if len(df) < 100:
            return None

        return df.reset_index(
            drop=True
        )

    except Exception as e:

        logger.warning(
            "Kline parsing failed for %s: %s",
            symbol,
            e
        )

        return None


# =========================================================
# RSI
# =========================================================

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


# =========================================================
# ATR
# =========================================================

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

    true_range = pd.concat(
        [tr1, tr2, tr3],
        axis=1
    ).max(axis=1)

    atr = true_range.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()

    return atr


# =========================================================
# INDICATORS
# =========================================================

def add_indicators(df):

    df = df.copy()

    # EMA
    df["ema9"] = (
        df["close"]
        .ewm(span=9, adjust=False)
        .mean()
    )

    df["ema21"] = (
        df["close"]
        .ewm(span=21, adjust=False)
        .mean()
    )

    df["ema50"] = (
        df["close"]
        .ewm(span=50, adjust=False)
        .mean()
    )

    df["ema200"] = (
        df["close"]
        .ewm(span=200, adjust=False)
        .mean()
    )

    # RSI
    df["rsi"] = calculate_rsi(
        df["close"],
        CFG.rsi_period
    )

    # MACD
    ema_fast = (
        df["close"]
        .ewm(
            span=CFG.macd_fast,
            adjust=False
        )
        .mean()
    )

    ema_slow = (
        df["close"]
        .ewm(
            span=CFG.macd_slow,
            adjust=False
        )
        .mean()
    )

    df["macd"] = (
        ema_fast - ema_slow
    )

    df["macd_signal"] = (
        df["macd"]
        .ewm(
            span=CFG.macd_signal,
            adjust=False
        )
        .mean()
    )

    df["macd_hist"] = (
        df["macd"]
        - df["macd_signal"]
    )

    # ATR
    df["atr"] = calculate_atr(
        df,
        CFG.atr_period
    )

    # Volume MA
    df["volume_ma20"] = (
        df["volume"]
        .rolling(20)
        .mean()
    )

    df["volume_ratio"] = (
        df["volume"]
        / df["volume_ma20"]
    )

    # Candle body ratio
    candle_range = (
        df["high"]
        - df["low"]
    )

    df["body_ratio"] = (
        (df["close"] - df["open"]).abs()
        / candle_range.replace(
            0,
            np.nan
        )
    )

    return df


# =========================================================
# BTC TREND
# =========================================================

def get_btc_trend(interval):

    df = get_klines(
        "BTCUSDT",
        interval,
        300
    )

    if df is None:
        return "UNKNOWN"

    df = add_indicators(df)

    last = df.iloc[-1]

    close = safe_float(
        last["close"]
    )

    ema50 = safe_float(
        last["ema50"]
    )

    ema200 = safe_float(
        last["ema200"]
    )

    if (
        close > ema50
        and ema50 > ema200
    ):
        return "BULLISH"

    if (
        close < ema50
        and ema50 < ema200
    ):
        return "BEARISH"

    return "NEUTRAL"


# =========================================================
# BTC MULTI-TIMEFRAME CONTEXT
# =========================================================

def get_btc_context():

    btc_4h = get_btc_trend("4h")
    btc_1d = get_btc_trend("1d")

    logger.info(
        "BTC trend 4H: %s",
        btc_4h
    )

    logger.info(
        "BTC trend Daily: %s",
        btc_1d
    )

    if (
        btc_4h == "BULLISH"
        and btc_1d != "BEARISH"
    ):
        combined = "BULLISH"

    elif (
        btc_4h == "BEARISH"
        and btc_1d != "BULLISH"
    ):
        combined = "BEARISH"

    else:
        combined = "NEUTRAL"

    return {
        "4h": btc_4h,
        "1d": btc_1d,
        "combined": combined
    }


# =========================================================
# RSI DIVERGENCE
# =========================================================

def detect_rsi_divergence(df):

    if len(df) < 60:
        return "NONE"

    recent = df.tail(60).copy()

    half = len(recent) // 2

    first = recent.iloc[:half]
    second = recent.iloc[half:]

    price_low_1 = first["low"].min()
    price_low_2 = second["low"].min()

    rsi_low_1 = first["rsi"].min()
    rsi_low_2 = second["rsi"].min()

    price_high_1 = first["high"].max()
    price_high_2 = second["high"].max()

    rsi_high_1 = first["rsi"].max()
    rsi_high_2 = second["rsi"].max()

    # Bullish divergence:
    # price makes lower low,
    # RSI makes higher low.
    if (
        price_low_2 < price_low_1
        and rsi_low_2 > rsi_low_1
    ):
        return "BULLISH"

    # Bearish divergence:
    # price makes higher high,
    # RSI makes lower high.
    if (
        price_high_2 > price_high_1
        and rsi_high_2 < rsi_high_1
    ):
        return "BEARISH"

    return "NONE"# =========================================================
# COIN ANALYSIS
# =========================================================

def analyze_coin(symbol, btc_context):

    try:

        # ---------------------------------------------
        # Get 4H candles
        # ---------------------------------------------

        df = get_klines(
            symbol,
            CFG.timeframe,
            CFG.kline_limit
        )

        if df is None:
            return None

        df = add_indicators(df)

        if len(df) < 220:
            return None

        last = df.iloc[-1]
        previous = df.iloc[-2]

        # ---------------------------------------------
        # Current values
        # ---------------------------------------------

        close = safe_float(last["close"])
        open_price = safe_float(last["open"])
        high = safe_float(last["high"])
        low = safe_float(last["low"])

        ema9 = safe_float(last["ema9"])
        ema21 = safe_float(last["ema21"])
        ema50 = safe_float(last["ema50"])
        ema200 = safe_float(last["ema200"])

        rsi = safe_float(last["rsi"])

        macd = safe_float(last["macd"])
        macd_signal = safe_float(
            last["macd_signal"]
        )
        macd_hist = safe_float(
            last["macd_hist"]
        )

        previous_macd = safe_float(
            previous["macd"]
        )
        previous_signal = safe_float(
            previous["macd_signal"]
        )

        atr = safe_float(last["atr"])

        volume_ratio = safe_float(
            last["volume_ratio"]
        )

        body_ratio = safe_float(
            last["body_ratio"]
        )

        # ---------------------------------------------
        # Basic validation
        # ---------------------------------------------

        if close <= 0:
            return None

        if atr <= 0:
            return None

        if ema21 <= 0:
            return None

        # ---------------------------------------------
        # Determine direction
        # ---------------------------------------------

        long_structure = (
            ema9 > ema21
            and ema21 > ema50
            and ema50 > ema200
        )

        short_structure = (
            ema9 < ema21
            and ema21 < ema50
            and ema50 < ema200
        )

        if long_structure:

            direction = "LONG"

        elif short_structure:

            direction = "SHORT"

        else:

            return None

        # ---------------------------------------------
        # Distance from EMA21
        # ---------------------------------------------

        distance_ema21 = (
            abs(close - ema21)
            / ema21
            * 100
        )

        if (
            distance_ema21
            > CFG.max_distance_from_ema21
        ):
            return None

        # ---------------------------------------------
        # RSI divergence
        # ---------------------------------------------

        divergence = detect_rsi_divergence(
            df
        )

        # ---------------------------------------------
        # Hard volume filter
        # ---------------------------------------------

        if (
            volume_ratio
            < CFG.min_volume_ratio
        ):
            return None

        # ---------------------------------------------
        # Hard RSI filter
        # ---------------------------------------------

        if direction == "LONG":

            if rsi > 65:
                return None

        else:

            if rsi < 35:
                return None

        # ---------------------------------------------
        # Reject opposite divergence
        # ---------------------------------------------

        if (
            direction == "LONG"
            and divergence == "BEARISH"
        ):
            return None

        if (
            direction == "SHORT"
            and divergence == "BULLISH"
        ):
            return None

        # ---------------------------------------------
        # Daily BTC filter
        # ---------------------------------------------

        if (
            direction == "LONG"
            and btc_context["1d"] == "BEARISH"
        ):
            return None

        if (
            direction == "SHORT"
            and btc_context["1d"] == "BULLISH"
        ):
            return None

        # ---------------------------------------------
        # Score
        # ---------------------------------------------

        score = 0
        reasons = []

        # =============================================
        # EMA STRUCTURE
        # =============================================

        if direction == "LONG":

            score += 18
            reasons.append(
                "روند EMA صعودی"
            )

            if close > ema9:
                score += 6

            if ema9 > ema21:
                score += 5

            if ema21 > ema50:
                score += 5

        else:

            score += 18
            reasons.append(
                "روند EMA نزولی"
            )

            if close < ema9:
                score += 6

            if ema9 < ema21:
                score += 5

            if ema21 < ema50:
                score += 5

        # =============================================
        # RSI
        # =============================================

        if direction == "LONG":

            if 45 <= rsi <= 58:

                score += 12

                reasons.append(
                    "RSI مناسب خرید"
                )

            elif 58 < rsi <= 62:

                score += 7

                reasons.append(
                    "RSI قابل قبول"
                )

            else:

                score += 3

        else:

            if 42 <= rsi <= 55:

                score += 12

                reasons.append(
                    "RSI مناسب فروش"
                )

            elif 38 <= rsi < 42:

                score += 7

                reasons.append(
                    "RSI قابل قبول"
                )

            else:

                score += 3

        # =============================================
        # MACD
        # =============================================

        if direction == "LONG":

            if macd > macd_signal:
                score += 8
                reasons.append(
                    "MACD مثبت"
                )

            if macd_hist > 0:
                score += 4

            if (
                previous_macd
                <= previous_signal
                and macd
                > macd_signal
            ):
                score += 3
                reasons.append(
                    "کراس صعودی MACD"
                )

        else:

            if macd < macd_signal:
                score += 8
                reasons.append(
                    "MACD منفی"
                )

            if macd_hist < 0:
                score += 4

            if (
                previous_macd
                >= previous_signal
                and macd
                < macd_signal
            ):
                score += 3
                reasons.append(
                    "کراس نزولی MACD"
                )

        # =============================================
        # VOLUME
        # =============================================

        if (
            volume_ratio
            >= CFG.strong_volume_ratio
        ):

            score += 12

            reasons.append(
                "حجم تأییدکننده"
            )

        elif volume_ratio >= 0.95:

            score += 7

        else:

            score += 2

        # =============================================
        # BTC 4H
        # =============================================

        if direction == "LONG":

            if btc_context["4h"] == "BULLISH":

                score += 8

                reasons.append(
                    "BTC 4H صعودی"
                )

            elif btc_context["4h"] == "BEARISH":

                score -= 8

        else:

            if btc_context["4h"] == "BEARISH":

                score += 8

                reasons.append(
                    "BTC 4H نزولی"
                )

            elif btc_context["4h"] == "BULLISH":

                score -= 8

        # =============================================
        # BTC DAILY
        # =============================================

        if direction == "LONG":

            if btc_context["1d"] == "BULLISH":

                score += 6

                reasons.append(
                    "BTC Daily صعودی"
                )

        else:

            if btc_context["1d"] == "BEARISH":

                score += 6

                reasons.append(
                    "BTC Daily نزولی"
                )

        # =============================================
        # RSI DIVERGENCE
        # =============================================

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

        # =============================================
        # CANDLE QUALITY
        # =============================================

        if body_ratio >= 0.55:

            score += 5

            reasons.append(
                "کندل قدرتمند"
            )

        # ---------------------------------------------
        # Score boundary
        # ---------------------------------------------

        score = max(
            0,
            min(
                100,
                int(score)
            )
        )

        if score < CFG.min_score:
            return None

        # ---------------------------------------------
        # Stop Loss
        # ---------------------------------------------

        stop_distance = (
            atr
            * CFG.sl_atr_multiplier
        )

        if direction == "LONG":

            stop_loss = (
                close
                - stop_distance
            )

        else:

            stop_loss = (
                close
                + stop_distance
            )

        if stop_loss <= 0:
            return None

        sl_percent = (
            abs(close - stop_loss)
            / close
            * 100
        )

        # ---------------------------------------------
        # Maximum SL filter
        # ---------------------------------------------

        if (
            sl_percent
            > CFG.max_sl_percent
        ):
            return None

        # ---------------------------------------------
        # Take Profits
        # ---------------------------------------------

        if direction == "LONG":

            tp1 = (
                close
                + stop_distance
                * CFG.tp1_rr
            )

            tp2 = (
                close
                + stop_distance
                * CFG.tp2_rr
            )

        else:

            tp1 = (
                close
                - stop_distance
                * CFG.tp1_rr
            )

            tp2 = (
                close
                - stop_distance
                * CFG.tp2_rr
            )

        # ---------------------------------------------
        # Risk calculation
        # ---------------------------------------------

        risk_usdt = (
            CFG.account_size_usdt
            * CFG.risk_percent
            / 100
        )

        if sl_percent <= 0:
            return None

        position_usdt = (
            risk_usdt
            / (sl_percent / 100)
        )

        position_usdt = min(
            position_usdt,
            CFG.max_position_usdt,
            CFG.account_size_usdt
        )

        quantity = (
            position_usdt
            / close
        )

        # ---------------------------------------------
        # Signal quality
        # ---------------------------------------------

        if score >= CFG.strong_score:

            quality = "STRONG"

        else:

            quality = "NORMAL"

        # ---------------------------------------------
        # Result
        # ---------------------------------------------

        return {
            "symbol": symbol,
            "direction": direction,
            "score": score,
            "quality": quality,
            "entry": close,
            "stop_loss": stop_loss,
            "tp1": tp1,
            "tp2": tp2,
            "sl_percent": sl_percent,
            "rsi": rsi,
            "volume_ratio": volume_ratio,
            "divergence": divergence,
            "btc_4h": btc_context["4h"],
            "btc_1d": btc_context["1d"],
            "distance_ema21": distance_ema21,
            "risk_usdt": risk_usdt,
            "position_usdt": position_usdt,
            "quantity": quantity,
            "reasons": reasons
        }

    except Exception as e:

        logger.warning(
            "Analysis failed for %s: %s",
            symbol,
            e
        )

        return None# =========================================================
# FORMAT SIGNAL MESSAGE
# =========================================================

def format_signal(signal):

    direction_icon = (
        "🟢" if signal["direction"] == "LONG"
        else "🔴"
    )

    quality_icon = (
        "🔥" if signal["quality"] == "STRONG"
        else "⚡"
    )

    divergence_text = signal["divergence"]

    if divergence_text == "NONE":
        divergence_text = "ندارد"

    elif divergence_text == "BULLISH":
        divergence_text = "مثبت"

    elif divergence_text == "BEARISH":
        divergence_text = "منفی"

    reasons = signal.get(
        "reasons",
        []
    )

    reasons_text = "\n".join(
        f"• {reason}"
        for reason in reasons
    )

    return f"""
{direction_icon} <b>{signal["symbol"]}/USDT {signal["direction"]}</b>
{quality_icon} <b>{signal["quality"]}</b> | امتیاز: <b>{signal["score"]}/100</b>

💰 <b>ورود:</b> {signal["entry"]:.8g}

🛑 <b>حد ضرر:</b> {signal["stop_loss"]:.8g}
📉 <b>فاصله SL:</b> {signal["sl_percent"]:.2f}%

🎯 <b>TP1:</b> {signal["tp1"]:.8g}
🎯 <b>TP2:</b> {signal["tp2"]:.8g}

📊 <b>RSI:</b> {signal["rsi"]:.2f}
📦 <b>Volume:</b> {signal["volume_ratio"]:.2f}x

📈 <b>BTC 4H:</b> {signal["btc_4h"]}
📅 <b>BTC Daily:</b> {signal["btc_1d"]}

🔄 <b>واگرایی RSI:</b> {divergence_text}

💵 <b>ریسک:</b> ${signal["risk_usdt"]:.2f}
💼 <b>حجم پوزیشن:</b> ${signal["position_usdt"]:.2f}
🪙 <b>Quantity:</b> {signal["quantity"]:.8g}

<b>دلایل:</b>
{reasons_text}

⚠️ این پیام فقط سیگنال و محاسبه ریسک است و معامله خودکار انجام نمی‌شود.
""".strip()


# =========================================================
# FORMAT SCAN REPORT
# =========================================================

def format_scan_report(
    total_coins,
    analyzed,
    candidates,
    sent,
    btc_context
):

    return f"""
📊 <b>گزارش اسکن Crypto Signal Bot</b>

⏱ تایم‌فریم اصلی: <b>4H</b>

🪙 ارزهای قابل اسکن: <b>{total_coins}</b>
🔎 بررسی‌شده: <b>{analyzed}</b>
🎯 کاندیداها: <b>{candidates}</b>
📨 سیگنال‌های جدید: <b>{sent}</b>

₿ <b>BTC 4H:</b> {btc_context["4h"]}
📅 <b>BTC Daily:</b> {btc_context["1d"]}
📌 <b>BTC وضعیت ترکیبی:</b> {btc_context["combined"]}

⚙️ حداقل امتیاز: <b>{CFG.min_score}</b>
🔥 امتیاز STRONG: <b>{CFG.strong_score}</b>

🛡 ریسک هر سیگنال: <b>{CFG.risk_percent}%</b>
🎯 حداقل RR: <b>{CFG.tp1_rr}:1</b>

ℹ️ اسکن بعدی طبق زمان‌بندی GitHub Actions انجام می‌شود.
""".strip()


# =========================================================
# DEDUPLICATION
# =========================================================

def cleanup_state(state):

    now = time.time()

    max_age = (
        CFG.dedup_hours
        * 3600
    )

    cleaned = {}

    for key, timestamp in state.items():

        try:

            timestamp = float(timestamp)

            if (
                now - timestamp
                < max_age
            ):
                cleaned[key] = timestamp

        except Exception:
            continue

    return cleaned


def signal_key(signal):

    symbol = signal["symbol"]
    direction = signal["direction"]

    # Current closed 4H candle
    candle_time = int(
        time.time()
        // (4 * 3600)
    )

    return (
        f"{symbol}_"
        f"{direction}_"
        f"{candle_time}"
    )


# =========================================================
# MAIN
# =========================================================

def main():

    logger.info(
        "======================================"
    )

    logger.info(
        "Starting Crypto Signal Bot"
    )

    logger.info(
        "Timeframe: %s",
        CFG.timeframe
    )

    logger.info(
        "======================================"
    )

    # ---------------------------------------------
    # Load state
    # ---------------------------------------------

    state = load_state()

    state = cleanup_state(
        state
    )

    # ---------------------------------------------
    # Get coins
    # ---------------------------------------------

    coins = get_scan_coins()

    if not coins:

        logger.error(
            "No coins available for scanning."
        )

        if CFG.send_no_signal_report:

            send_telegram(
                "⚠️ <b>Crypto Signal Bot</b>\n\n"
                "لیست ارزهای قابل اسکن دریافت نشد."
            )

        return

    # ---------------------------------------------
    # BTC context
    # ---------------------------------------------

    btc_context = get_btc_context()

    # ---------------------------------------------
    # Analyze coins
    # ---------------------------------------------

    results = []

    logger.info(
        "Starting analysis of %s coins...",
        len(coins)
    )

    with ThreadPoolExecutor(
        max_workers=CFG.max_workers
    ) as executor:

        futures = {
            executor.submit(
                analyze_coin,
                coin + "USDT",
                btc_context
            ): coin
            for coin in coins
        }

        for future in as_completed(
            futures
        ):

            coin = futures[future]

            try:

                result = future.result()

                if result is not None:
                    results.append(result)

            except Exception as e:

                logger.warning(
                    "Worker failed for %s: %s",
                    coin,
                    e
                )

    # ---------------------------------------------
    # Sort by score
    # ---------------------------------------------

    results.sort(
        key=lambda x: (
            x["score"],
            x["volume_ratio"]
        ),
        reverse=True
    )

    logger.info(
        "Analysis finished. Candidates: %s",
        len(results)
    )

    # ---------------------------------------------
    # Send new signals
    # ---------------------------------------------

    sent_count = 0

    for signal in results:

        if (
            sent_count
            >= CFG.max_signals_per_run
        ):
            break

        key = signal_key(
            signal
        )

        if key in state:
            continue

        message = format_signal(
            signal
        )

        if send_telegram(
            message
        ):

            state[key] = time.time()

            sent_count += 1

            logger.info(
                "Signal sent: %s %s score=%s",
                signal["symbol"],
                signal["direction"],
                signal["score"]
            )

    # ---------------------------------------------
    # Save state
    # ---------------------------------------------

    save_state(state)

    # ---------------------------------------------
    # Summary report
    # ---------------------------------------------

    if CFG.send_no_signal_report:

        report = format_scan_report(
            total_coins=len(coins),
            analyzed=len(coins),
            candidates=len(results),
            sent=sent_count,
            btc_context=btc_context
        )

        send_telegram(
            report
        )

    # ---------------------------------------------
    # Final logs
    # ---------------------------------------------

    logger.info(
        "======================================"
    )

    logger.info(
        "Coins: %s",
        len(coins)
    )

    logger.info(
        "Analyzed: %s",
        len(coins)
    )

    logger.info(
        "Candidates: %s",
        len(results)
    )

    logger.info(
        "New signals sent: %s",
        sent_count
    )

    logger.info(
        "Bot finished successfully"
    )

    logger.info(
        "======================================"
    )


# =========================================================
# START
# =========================================================

if __name__ == "__main__":
    main()
