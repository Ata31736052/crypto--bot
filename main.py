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

    # Main timeframe
    timeframe = "4h"

    # Candles
    kline_limit = 300

    # Signal score
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

    # Stop Loss
    sl_atr_multiplier = 1.40
    max_sl_percent = 6.0

    # Take Profit
    tp1_rr = 1.80
    tp2_rr = 3.00

    # EMA distance filter
    max_distance_from_ema21 = 4.0

    # Maximum new signals per scan
    max_signals_per_run = 3

    # Duplicate protection
    state_file = "signals_state.json"
    dedup_hours = 8

    # Parallel analysis
    max_workers = 10

    # Always send scan report
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


def http_get(
    url,
    params=None,
    timeout=20
):

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

        logger.error(
            "TELEGRAM_TOKEN is missing"
        )

        return False

    if not CFG.telegram_chat_id:

        logger.error(
            "TELEGRAM_CHAT_ID is missing"
        )

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

            logger.info(
                "Telegram message sent"
            )

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

            stats = data.get(
                "stats",
                {}
            )

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
                item.get(
                    "symbol",
                    ""
                )
            ).upper()

            if not symbol.endswith("USDT"):
                continue

            quote_volume = safe_float(
                item.get(
                    "quoteVolume"
                )
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
            volumes.get(
                coin,
                0
            )
        )

        if volume < CFG.min_24h_usdt_volume:
            continue

        final_coins.append(
            coin
        )

    final_coins.sort(
        key=lambda x: volumes.get(
            x,
            0
        ),
        reverse=True
    )

    logger.info(
        "Final scan list after liquidity filter: %s",
        len(final_coins)
    )

    if final_coins:

        logger.info(
            "Scan preview: %s",
            ", ".join(
                final_coins[:30]
            )
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

        # حذف کندل در حال تشکیل
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

    rs = (
        avg_gain
        / avg_loss.replace(
            0,
            np.nan
        )
    )

    rsi = (
        100
        - (
            100
            / (1 + rs)
        )
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
        high
        - previous_close
    ).abs()

    tr3 = (
        low
        - previous_close
    ).abs()

    true_range = pd.concat(
        [
            tr1,
            tr2,
            tr3
        ],
        axis=1
    ).max(axis=1)

    atr = true_range.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()

    return atr


# =========================================================
# ADD INDICATORS
# =========================================================

def add_indicators(df):

    df = df.copy()

    # EMA
    df["ema9"] = (
        df["close"]
        .ewm(
            span=9,
            adjust=False
        )
        .mean()
    )

    df["ema21"] = (
        df["close"]
        .ewm(
            span=21,
            adjust=False
        )
        .mean()
    )

    df["ema50"] = (
        df["close"]
        .ewm(
            span=50,
            adjust=False
        )
        .mean()
    )

    df["ema200"] = (
        df["close"]
        .ewm(
            span=200,
            adjust=False
        )
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
        ema_fast
        - ema_slow
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

    # Volume
    df["volume_ma20"] = (
        df["volume"]
        .rolling(20)
        .mean()
    )

    df["volume_ratio"] = (
        df["volume"]
        / df["volume_ma20"]
    )

    # Candle body
    candle_range = (
        df["high"]
        - df["low"]
    )

    df["body_ratio"] = (
        (
            df["close"]
            - df["open"]
        ).abs()
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
# BTC CONTEXT
# =========================================================

def get_btc_context():

    btc_4h = get_btc_trend(
        "4h"
    )

    btc_1d = get_btc_trend(
        "1d"
    )

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

    recent = df.tail(
        60
    ).copy()

    half = len(recent) // 2

    first = recent.iloc[
        :half
    ]

    second = recent.iloc[
        half:
    ]

    # Lows
    price_low_1 = first[
        "low"
    ].min()

    price_low_2 = second[
        "low"
    ].min()

    rsi_low_1 = first[
        "rsi"
    ].min()

    rsi_low_2 = second[
        "rsi"
    ].min()

    # Highs
    price_high_1 = first[
        "high"
    ].max()

    price_high_2 = second[
        "high"
    ].max()

    rsi_high_1 = first[
        "rsi"
    ].max()

    rsi_high_2 = second[
        "rsi"
    ].max()

    # Bullish divergence
    if (
        price_low_2 < price_low_1
        and rsi_low_2 > rsi_low_1
    ):

        return "BULLISH"

    # Bearish divergence
    if (
        price_high_2 > price_high_1
        and rsi_high_2 < rsi_high_1
    ):

        return "BEARISH"

    return "NONE"# =========================================================
# COIN ANALYSIS
# =========================================================

def analyze_coin(symbol, btc_context):

    result = {
        "symbol": symbol,
        "status": "UNKNOWN",
        "direction": None,
        "score": 0,
        "price": 0,
        "rsi": 0,
        "volume_ratio": 0,
        "sl_percent": 0,
        "reason": ""
    }

    try:

        # -------------------------------------------------
        # Get 4H candles
        # -------------------------------------------------

        df = get_klines(
            symbol + "USDT",
            CFG.timeframe,
            CFG.kline_limit
        )

        if df is None:

            result["status"] = "NO_DATA"
            result["reason"] = "داده کندلی دریافت نشد"

            return result

        if len(df) < 200:

            result["status"] = "NOT_ENOUGH_DATA"
            result["reason"] = "تعداد کندل کافی نیست"

            return result

        # -------------------------------------------------
        # Indicators
        # -------------------------------------------------

        df = add_indicators(df)

        last = df.iloc[-1]

        price = safe_float(
            last["close"]
        )

        ema9 = safe_float(
            last["ema9"]
        )

        ema21 = safe_float(
            last["ema21"]
        )

        ema50 = safe_float(
            last["ema50"]
        )

        ema200 = safe_float(
            last["ema200"]
        )

        rsi = safe_float(
            last["rsi"]
        )

        macd = safe_float(
            last["macd"]
        )

        macd_signal = safe_float(
            last["macd_signal"]
        )

        macd_hist = safe_float(
            last["macd_hist"]
        )

        atr = safe_float(
            last["atr"]
        )

        volume_ratio = safe_float(
            last["volume_ratio"]
        )

        result["price"] = price
        result["rsi"] = rsi
        result["volume_ratio"] = volume_ratio

        # -------------------------------------------------
        # Basic validation
        # -------------------------------------------------

        if price <= 0:

            result["status"] = "INVALID_PRICE"
            result["reason"] = "قیمت نامعتبر"

            return result

        if atr <= 0:

            result["status"] = "INVALID_ATR"
            result["reason"] = "ATR نامعتبر"

            return result

        # -------------------------------------------------
        # EMA structure
        # -------------------------------------------------

        bullish_structure = (
            price > ema9
            and ema9 > ema21
            and ema21 > ema50
            and ema50 > ema200
        )

        bearish_structure = (
            price < ema9
            and ema9 < ema21
            and ema21 < ema50
            and ema50 < ema200
        )

        if not bullish_structure and not bearish_structure:

            result["status"] = "NO_EMA_STRUCTURE"
            result["reason"] = "ساختار EMA تأیید نمی‌شود"

            return result

        # -------------------------------------------------
        # Direction
        # -------------------------------------------------

        if bullish_structure:

            direction = "LONG"

        else:

            direction = "SHORT"

        result["direction"] = direction

        # -------------------------------------------------
        # Distance from EMA21
        # -------------------------------------------------

        ema21_distance = (
            abs(price - ema21)
            / ema21
            * 100
        )

        if (
            ema21_distance
            > CFG.max_distance_from_ema21
        ):

            result["status"] = "EMA21_DISTANCE"

            result["reason"] = (
                "فاصله قیمت از EMA21 زیاد است: "
                f"{ema21_distance:.2f}%"
            )

            return result

        # -------------------------------------------------
        # Volume filter
        # -------------------------------------------------

        if volume_ratio < CFG.min_volume_ratio:

            result["status"] = "LOW_VOLUME"

            result["reason"] = (
                "حجم پایین است: "
                f"{volume_ratio:.2f}x"
            )

            return result

        # -------------------------------------------------
        # RSI extreme filter
        # -------------------------------------------------

        if (
            direction == "LONG"
            and rsi > 65
        ):

            result["status"] = "RSI_TOO_HIGH"

            result["reason"] = (
                "RSI برای خرید بالا است: "
                f"{rsi:.1f}"
            )

            return result

        if (
            direction == "SHORT"
            and rsi < 35
        ):

            result["status"] = "RSI_TOO_LOW"

            result["reason"] = (
                "RSI برای فروش پایین است: "
                f"{rsi:.1f}"
            )

            return result

        # -------------------------------------------------
        # RSI divergence
        # -------------------------------------------------

        divergence = detect_rsi_divergence(
            df
        )

        if (
            direction == "LONG"
            and divergence == "BEARISH"
        ):

            result["status"] = (
                "OPPOSITE_DIVERGENCE"
            )

            result["reason"] = (
                "واگرایی نزولی مخالف سیگنال"
            )

            return result

        if (
            direction == "SHORT"
            and divergence == "BULLISH"
        ):

            result["status"] = (
                "OPPOSITE_DIVERGENCE"
            )

            result["reason"] = (
                "واگرایی صعودی مخالف سیگنال"
            )

            return result

        # -------------------------------------------------
        # BTC Daily context filter
        # -------------------------------------------------

        btc_daily = btc_context.get(
            "1d",
            "UNKNOWN"
        )

        if (
            direction == "LONG"
            and btc_daily == "BEARISH"
        ):

            result["status"] = (
                "BTC_DAILY_AGAINST"
            )

            result["reason"] = (
                "روند Daily بیت‌کوین مخالف خرید است"
            )

            return result

        if (
            direction == "SHORT"
            and btc_daily == "BULLISH"
        ):

            result["status"] = (
                "BTC_DAILY_AGAINST"
            )

            result["reason"] = (
                "روند Daily بیت‌کوین مخالف فروش است"
            )

            return result

        # -------------------------------------------------
        # Score
        # -------------------------------------------------

        score = 0

        # EMA structure
        score += 25

        # RSI
        if direction == "LONG":

            if 40 <= rsi <= 60:
                score += 15

            elif 35 <= rsi < 40:
                score += 10

            elif 60 < rsi <= 65:
                score += 8

        else:

            if 40 <= rsi <= 60:
                score += 15

            elif 60 < rsi <= 65:
                score += 10

            elif 35 <= rsi < 40:
                score += 8

        # MACD
        if direction == "LONG":

            if macd > macd_signal:
                score += 15

            if macd_hist > 0:
                score += 5

        else:

            if macd < macd_signal:
                score += 15

            if macd_hist < 0:
                score += 5

        # Volume
        if volume_ratio >= CFG.strong_volume_ratio:

            score += 15

        elif volume_ratio >= CFG.min_volume_ratio:

            score += 8

        # BTC context
        btc_4h = btc_context.get(
            "4h",
            "UNKNOWN"
        )

        if (
            direction == "LONG"
            and btc_4h == "BULLISH"
        ):

            score += 10

        elif (
            direction == "SHORT"
            and btc_4h == "BEARISH"
        ):

            score += 10

        # Divergence confirmation
        if (
            direction == "LONG"
            and divergence == "BULLISH"
        ):

            score += 10

        elif (
            direction == "SHORT"
            and divergence == "BEARISH"
        ):

            score += 10

        # -------------------------------------------------
        # Minimum score
        # -------------------------------------------------

        if score < CFG.min_score:

            result["status"] = "LOW_SCORE"

            result["score"] = score

            result["reason"] = (
                f"امتیاز کافی نیست: {score}"
            )

            return result

        # -------------------------------------------------
        # Stop Loss
        # -------------------------------------------------

        if direction == "LONG":

            stop_loss = (
                price
                - atr * CFG.sl_atr_multiplier
            )

            if stop_loss <= 0:

                result["status"] = "INVALID_SL"
                result["reason"] = "حد ضرر نامعتبر"

                return result

            sl_percent = (
                (price - stop_loss)
                / price
                * 100
            )

        else:

            stop_loss = (
                price
                + atr * CFG.sl_atr_multiplier
            )

            sl_percent = (
                (stop_loss - price)
                / price
                * 100
            )

        result["sl_percent"] = sl_percent

        # -------------------------------------------------
        # Maximum Stop Loss
        # -------------------------------------------------

        if sl_percent > CFG.max_sl_percent:

            result["status"] = "SL_TOO_LARGE"

            result["reason"] = (
                "حد ضرر بیش از "
                f"{CFG.max_sl_percent:.1f}% است: "
                f"{sl_percent:.2f}%"
            )

            return result

        # -------------------------------------------------
        # Take Profits
        # -------------------------------------------------

        risk_distance = abs(
            price - stop_loss
        )

        if direction == "LONG":

            tp1 = (
                price
                + risk_distance
                * CFG.tp1_rr
            )

            tp2 = (
                price
                + risk_distance
                * CFG.tp2_rr
            )

        else:

            tp1 = (
                price
                - risk_distance
                * CFG.tp1_rr
            )

            tp2 = (
                price
                - risk_distance
                * CFG.tp2_rr
            )

        # -------------------------------------------------
        # Position size
        # -------------------------------------------------

        risk_amount = (
            CFG.account_size_usdt
            * CFG.risk_percent
            / 100
        )

        if risk_distance <= 0:

            result["status"] = "INVALID_SL"
            result["reason"] = "فاصله ریسک نامعتبر"

            return result

        position_size = (
            risk_amount
            / risk_distance
        )

        position_usdt = (
            position_size
            * price
        )

        position_usdt = min(
            position_usdt,
            CFG.max_position_usdt
        )

        # -------------------------------------------------
        # Final signal
        # -------------------------------------------------

        result["status"] = "SIGNAL"

        result["score"] = score

        result["stop_loss"] = stop_loss
        result["tp1"] = tp1
        result["tp2"] = tp2

        result["risk_amount"] = risk_amount

        result["position_size"] = position_size

        result["position_usdt"] = position_usdt

        result["divergence"] = divergence

        if score >= CFG.strong_score:

            result["strength"] = "STRONG"

        else:

            result["strength"] = "NORMAL"

        return result

    except Exception as e:

        logger.exception(
            "Analysis error for %s",
            symbol
        )

        result["status"] = "ERROR"

        result["reason"] = (
            f"خطای تحلیل: {str(e)[:100]}"
        )

        return result# =========================================================
# SIGNAL FORMAT
# =========================================================

def format_signal(result):

    symbol = result["symbol"]
    direction = result["direction"]
    score = result["score"]
    price = result["price"]

    stop_loss = result["stop_loss"]
    tp1 = result["tp1"]
    tp2 = result["tp2"]

    rsi = result["rsi"]
    volume_ratio = result["volume_ratio"]
    sl_percent = result["sl_percent"]

    position_usdt = result["position_usdt"]
    risk_amount = result["risk_amount"]

    strength = result.get(
        "strength",
        "NORMAL"
    )

    if direction == "LONG":
        direction_text = "🟢 LONG / خرید"
    else:
        direction_text = "🔴 SHORT / فروش"

    if strength == "STRONG":
        strength_text = "🔥 STRONG"
    else:
        strength_text = "✅ NORMAL"

    return (
        "🚨 <b>Crypto Signal</b>\n"
        "━━━━━━━━━━━━━━━━\n"
        f"🪙 <b>{symbol}</b>\n"
        f"📌 جهت: <b>{direction_text}</b>\n"
        f"⭐ قدرت: <b>{strength_text}</b>\n"
        f"🏆 Score: <b>{score}/100</b>\n"
        "\n"
        f"💰 قیمت ورود: <b>{price:.8g}</b>\n"
        f"🛑 حد ضرر: <b>{stop_loss:.8g}</b>\n"
        f"📉 ریسک SL: <b>{sl_percent:.2f}%</b>\n"
        f"🎯 TP1: <b>{tp1:.8g}</b>\n"
        f"🎯 TP2: <b>{tp2:.8g}</b>\n"
        "\n"
        f"📊 RSI: <b>{rsi:.1f}</b>\n"
        f"📦 Volume: <b>{volume_ratio:.2f}x</b>\n"
        f"💵 حجم پوزیشن: <b>{position_usdt:.2f} USDT</b>\n"
        f"⚠️ ریسک سرمایه: <b>{risk_amount:.2f} USDT</b>\n"
        "\n"
        "⏱ تایم‌فریم: <b>4H</b>\n"
        "📡 Data: Binance\n"
        "🔎 Market: Nobitex-listed\n"
        "━━━━━━━━━━━━━━━━"
    )


# =========================================================
# REJECTION REASONS
# =========================================================

REASON_NAMES = {

    "NO_DATA":
        "داده کندلی دریافت نشد",

    "NOT_ENOUGH_DATA":
        "کندل کافی نبود",

    "INVALID_PRICE":
        "قیمت نامعتبر",

    "INVALID_ATR":
        "ATR نامعتبر",

    "NO_EMA_STRUCTURE":
        "ساختار EMA تأیید نشد",

    "EMA21_DISTANCE":
        "فاصله زیاد از EMA21",

    "LOW_VOLUME":
        "حجم معاملات پایین",

    "RSI_TOO_HIGH":
        "RSI برای خرید بالا بود",

    "RSI_TOO_LOW":
        "RSI برای فروش پایین بود",

    "OPPOSITE_DIVERGENCE":
        "واگرایی مخالف",

    "BTC_DAILY_AGAINST":
        "روند Daily بیت‌کوین مخالف بود",

    "LOW_SCORE":
        "امتیاز کافی نبود",

    "INVALID_SL":
        "حد ضرر نامعتبر",

    "SL_TOO_LARGE":
        "حد ضرر بیشتر از حد مجاز",

    "ERROR":
        "خطای تحلیل",

    "UNKNOWN":
        "سایر"
}


# =========================================================
# SCAN REPORT
# =========================================================

def format_scan_report(
    total_coins,
    analyzed,
    signals,
    new_signals,
    btc_context,
    results
):

    from collections import Counter

    counter = Counter()

    for result in results:

        status = result.get(
            "status",
            "UNKNOWN"
        )

        if status != "SIGNAL":
            counter[status] += 1

    lines = []

    lines.append(
        "📊 <b>گزارش اسکن ارزها</b>"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━"
    )

    lines.append(
        f"🪙 تعداد ارزها: <b>{total_coins}</b>"
    )

    lines.append(
        f"🔎 تحلیل‌شده: <b>{analyzed}</b>"
    )

    lines.append(
        f"🎯 کاندیدا: <b>{signals}</b>"
    )

    lines.append(
        f"🆕 سیگنال جدید: <b>{new_signals}</b>"
    )

    lines.append("")

    lines.append(
        "₿ <b>روند بیت‌کوین</b>"
    )

    lines.append(
        f"4H: <b>{btc_context.get('4h', 'UNKNOWN')}</b>"
    )

    lines.append(
        f"Daily: <b>{btc_context.get('1d', 'UNKNOWN')}</b>"
    )

    lines.append(
        f"Combined: <b>{btc_context.get('combined', 'UNKNOWN')}</b>"
    )

    lines.append("")

    lines.append(
        "❌ <b>دلایل رد شدن</b>"
    )

    if counter:

        # بیشترین دلایل در ابتدا
        sorted_reasons = sorted(
            counter.items(),
            key=lambda x: x[1],
            reverse=True
        )

        for status, count in sorted_reasons:

            reason_name = REASON_NAMES.get(
                status,
                status
            )

            lines.append(
                f"• {reason_name}: <b>{count}</b>"
            )

    else:

        lines.append(
            "• موردی برای رد شدن وجود ندارد"
        )

    lines.append("")

    lines.append(
        "⏱ <b>Timeframe: 4H</b>"
    )

    lines.append(
        "📡 <b>Data: Binance</b>"
    )

    lines.append(
        "🔎 <b>Coins: Nobitex-listed</b>"
    )

    return "\n".join(lines)


# =========================================================
# SIGNAL DEDUPLICATION
# =========================================================

def get_signal_key(result):

    symbol = result.get(
        "symbol",
        ""
    )

    direction = result.get(
        "direction",
        ""
    )

    # زمان فعلی بر اساس بازه 4 ساعته
    bucket = int(
        time.time()
        // (4 * 3600)
    )

    return (
        f"{symbol}_"
        f"{direction}_"
        f"{bucket}"
    )


def is_duplicate_signal(
    result,
    state
):

    key = get_signal_key(
        result
    )

    old_time = safe_float(
        state.get(key, 0)
    )

    if old_time <= 0:
        return False

    age_hours = (
        time.time()
        - old_time
    ) / 3600

    return (
        age_hours
        < CFG.dedup_hours
    )


def mark_signal_sent(
    result,
    state
):

    key = get_signal_key(
        result
    )

    state[key] = time.time()


# =========================================================
# CLEAN OLD STATE
# =========================================================

def clean_old_state(state):

    now = time.time()

    max_age = (
        CFG.dedup_hours
        * 3600
        * 3
    )

    cleaned = {}

    for key, value in state.items():

        old_time = safe_float(
            value
        )

        if (
            old_time > 0
            and now - old_time < max_age
        ):

            cleaned[key] = value

    return cleaned


# =========================================================
# MAIN
# =========================================================

def main():

    logger.info(
        "Starting Crypto Signal Bot"
    )

    logger.info(
        "Timeframe: %s",
        CFG.timeframe
    )

    # -----------------------------------------------------
    # Telegram check
    # -----------------------------------------------------

    if not CFG.telegram_token:

        logger.error(
            "TELEGRAM_TOKEN is missing"
        )

        return

    if not CFG.telegram_chat_id:

        logger.error(
            "TELEGRAM_CHAT_ID is missing"
        )

        return

    # -----------------------------------------------------
    # Load state
    # -----------------------------------------------------

    state = load_state()

    state = clean_old_state(
        state
    )

    # -----------------------------------------------------
    # Get coins
    # -----------------------------------------------------

    coins = get_scan_coins()

    if not coins:

        logger.error(
            "No coins available for scanning"
        )

        send_telegram(
            "⚠️ <b>Crypto Bot Error</b>\n\n"
            "لیست ارزهای قابل اسکن دریافت نشد."
        )

        return

    # -----------------------------------------------------
    # BTC context
    # -----------------------------------------------------

    btc_context = get_btc_context()

    logger.info(
        "Starting analysis of %s coins...",
        len(coins)
    )

    # -----------------------------------------------------
    # Analyze coins in parallel
    # -----------------------------------------------------

    results = []

    with ThreadPoolExecutor(
        max_workers=CFG.max_workers
    ) as executor:

        futures = {
            executor.submit(
                analyze_coin,
                coin,
                btc_context
            ): coin

            for coin in coins
        }

        for future in as_completed(
            futures
        ):

            coin = futures[
                future
            ]

            try:

                result = future.result()

                if result:

                    results.append(
                        result
                    )

            except Exception as e:

                logger.error(
                    "Future error for %s: %s",
                    coin,
                    e
                )

                results.append({
                    "symbol": coin,
                    "status": "ERROR",
                    "reason": str(e)
                })

    # -----------------------------------------------------
    # Signal candidates
    # -----------------------------------------------------

    candidates = [
        result
        for result in results
        if result.get(
            "status"
        ) == "SIGNAL"
    ]

    # مرتب‌سازی بر اساس Score
    candidates.sort(
        key=lambda x: x.get(
            "score",
            0
        ),
        reverse=True
    )

    logger.info(
        "Analysis finished. Candidates: %s",
        len(candidates)
    )

    # -----------------------------------------------------
    # Send new signals
    # -----------------------------------------------------

    new_signals = 0

    for result in candidates:

        if new_signals >= CFG.max_signals_per_run:
            break

        if is_duplicate_signal(
            result,
            state
        ):

            logger.info(
                "Duplicate signal skipped: %s %s",
                result.get("symbol"),
                result.get("direction")
            )

            continue

        message = format_signal(
            result
        )

        if send_telegram(
            message
        ):

            mark_signal_sent(
                result,
                state
            )

            new_signals += 1

    # -----------------------------------------------------
    # Save state
    # -----------------------------------------------------

    save_state(
        state
    )

    # -----------------------------------------------------
    # Scan report
    # -----------------------------------------------------

    if CFG.send_no_signal_report:

        report = format_scan_report(
            total_coins=len(coins),
            analyzed=len(results),
            signals=len(candidates),
            new_signals=new_signals,
            btc_context=btc_context,
            results=results
        )

        send_telegram(
            report
        )

    # -----------------------------------------------------
    # Final logs
    # -----------------------------------------------------

    logger.info(
        "Coins: %s",
        len(coins)
    )

    logger.info(
        "Analyzed: %s",
        len(results)
    )

    logger.info(
        "Candidates: %s",
        len(candidates)
    )

    logger.info(
        "New signals sent: %s",
        new_signals
    )

    logger.info(
        "Bot finished successfully"
    )


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    main()
