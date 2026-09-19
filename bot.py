# ============================================
# 🤖 ربات جامع و هوشمند سیگنال‌دهی کریپتو - نسخه Ultimate Pro (اصلاح‌شده)
# ============================================

import json, time, os, ssl, urllib.request, warnings
from datetime import datetime
warnings.filterwarnings('ignore')

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT = os.getenv("TELEGRAM_CHAT_ID")
STATE_FILE = "signals_state.json"

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

COINS = [
    # Top Market Cap & Major Coins
    'BTC', 'ETH', 'SOL', 'BNB', 'XRP', 'ADA', 'DOGE', 'AVAX', 'LINK', 'DOT',
    'LTC', 'BCH', 'ETC', 'XLM', 'UNI', 'FIL', 'TRX', 'ATOM', 'NEAR', 'AAVE',
    
    # Layer 1 & Layer 2 Ecosystems
    'SUI', 'APT', 'ARB', 'OP', 'MATIC', 'SEI', 'INJ', 'TIA', 'STX',
    'FTM', 'ALGO', 'EGLD', 'ROSE', 'MINA', 'IMX', 'MNT', 'RON', 'CELO', 'FLOW',
    
    # AI & Big Data
    'FET', 'RNDR', 'TAO', 'AGIX', 'OCEAN', 'AKT',
    
    # Popular Meme Coins & High Momentum
    'PEPE', 'WIF', 'BONK', 'FLOKI', 'SHIB', 'MEME', 'NOT', 'ORDI', 'SATS', 'BOME',
    
    # Other Solid Altcoins
    'RUNE', 'ICP', 'KAS', 'JUP'
]

def http(url, t=10, retries=3):
    for i in range(retries):
        try:
            req = urllib.request.Request(
                url, 
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
            )
            with urllib.request.urlopen(req, context=CTX, timeout=t) as x:
                return json.loads(x.read().decode())
        except Exception:
            if i < retries - 1:
                time.sleep(0.5)
            continue
    return None

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_state(state):
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
    except Exception:
        pass

def get_ticker_price(sym):
    url_bn = f"https://data-api.binance.vision/api/v3/ticker/price?symbol={sym}USDT"
    d = http(url_bn)
    if d and isinstance(d, dict) and 'price' in d:
        return float(d['price'])
    
    url_kc = f"https://api.kucoin.com/api/v1/market/orderbook/level1?symbol={sym}-USDT"
    d_kc = http(url_kc)
    if d_kc and d_kc.get('code') == '200000' and isinstance(d_kc.get('data'), dict) and 'price' in d_kc['data']:
        return float(d_kc['data']['price'])
        
    return None

def klines(sym, tf='4h', limit=220):
    live_price = get_ticker_price(sym)
    
    url_bn = f"https://data-api.binance.vision/api/v3/klines?symbol={sym}USDT&interval={tf}&limit={limit}"
    d = http(url_bn)
    if d and isinstance(d, list) and len(d) > 1:
        if not live_price:
            live_price = float(d[-1][4])
            
        d = d[:-1]
        opens = [float(c[1]) for c in d]
        highs = [float(c[2]) for c in d]
        lows = [float(c[3]) for c in d]
        closes = [float(c[4]) for c in d]
        vols = [float(c[5]) for c in d]
        return {'o': opens, 'h': highs, 'l': lows, 'p': closes, 'v': vols, 'price': live_price}

    url_kc = f"https://api.kucoin.com/api/v1/market/candles?symbol={sym}-USDT&type={tf if tf!='1d' else '1day'}"
    d = http(url_kc)
    if d and d.get('code') == '200000' and isinstance(d.get('data'), list) and len(d['data']) > 1:
        data = d['data'][1:limit]
        data.reverse()
        
        if not live_price:
            live_price = float(data[-1][2])
            
        opens = [float(c[1]) for c in data]
        closes = [float(c[2]) for c in data]
        highs = [float(c[3]) for c in data]
        lows = [float(c[4]) for c in data]
        vols = [float(c[5]) for c in data]
        return {'o': opens, 'h': highs, 'l': lows, 'p': closes, 'v': vols, 'price': live_price}

    return None

def ema_series(p, n):
    if len(p) < n: return []
    k = 2 / (n + 1)
    emas = [sum(p[:n]) / n]
    for x in p[n:]:
        emas.append(x * k + emas[-1] * (1 - k))
    return emas

def ema(p, n):
    s = ema_series(p, n)
    return s[-1] if s else None

def macd(p, fast=12, slow=26, signal=9):
    if len(p) < slow + signal: return None, None, None
    ema_fast = ema_series(p, fast)
    ema_slow = ema_series(p, slow)
    ema_fast = ema_fast[slow - fast:]
    macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
    signal_line = ema_series(macd_line, signal)
    if not signal_line: return None, None, None
    curr_macd = macd_line[-1]
    curr_signal = signal_line[-1]
    curr_hist = curr_macd - curr_signal
    return curr_macd, curr_signal, curr_hist

def rsi(p, n=14):
    if len(p) < n + 1: return 50.0
    gains, losses = [], []
    for i in range(1, len(p)):
        chg = p[i] - p[i - 1]
        gains.append(max(chg, 0))
        losses.append(max(-chg, 0))
    avg_g = sum(gains[:n]) / n
    avg_l = sum(losses[:n]) / n
    for i in range(n, len(gains)):
        avg_g = (avg_g * (n - 1) + gains[i]) / n
        avg_l = (avg_l * (n - 1) + losses[i]) / n
    if avg_l == 0: return 100.0
    return 100.0 - (100.0 / (1.0 + (avg_g / avg_l)))

def atr(highs, lows, closes, n=14):
    if len(closes) < n + 1: return None
    tr_list = []
    for i in range(1, len(closes)):
        h = highs[i]
        l = lows[i]
        pc = closes[i - 1]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        tr_list.append(tr)
    return sum(tr_list[-n:]) / n

def get_btc_macro_trend():
    k1d = klines('BTC', '1d', limit=60)
    if not k1d:
        return 'NEUTRAL'
    e50 = ema(k1d['p'], 50)
    if not e50:
        return 'NEUTRAL'
    return 'BULLISH' if k1d['price'] > e50 else 'BEARISH'

def analyze_tf(sym, tf, btc_trend):
    k_data = klines(sym, tf, limit=220)
    if not k_data:
        return None

    p, h, l, o, v = k_data['p'], k_data['h'], k_data['l'], k_data['o'], k_data['v']
    curr_price = k_data['price']
    
    e9 = ema(p, 9)
    e21 = ema(p, 21)
    e50 = ema(p, 50)
    e200 = ema(p, 200)
    rsi_val = rsi(p)
    atr_val = atr(h, l, p, 14)
    macd_val, signal_val, hist_val = macd(p)

    if not (e9 and e21 and e50 and e200 and atr_val and macd_val is not None):
        return None

    body = abs(p[-1] - o[-1])
    candle_range = h[-1] - l[-1]
    
    trend_icon = "🟢" if e9 > e21 else "🔴"
    base_info = {'sym': sym, 'tf': tf.upper(), 'price': curr_price, 'rsi': round(rsi_val, 1), 'icon': trend_icon}

    score_buy = 0
    score_sell = 0

    if e9 > e21: score_buy += 3
    if e21 > e50: score_buy += 2
    if e9 < e21: score_sell += 3
    if e21 < e50: score_sell += 2

    if curr_price > e200: score_buy += 2
    else: score_sell += 2

    if 40 < rsi_val < 70: score_buy += 3
    if 30 < rsi_val < 60: score_sell += 3

    macd_status = "خنثی"
    if hist_val > 0:
        score_buy += 2
        macd_status = "صعودی 🟢"
    elif hist_val < 0:
        score_sell += 2
        macd_status = "نزولی 🔴"

    avg_vol = sum(v[-21:-1]) / 20 if len(v) >= 21 else sum(v) / len(v)
    is_green_candle = p[-1] > o[-1]
    if v[-1] > (1.2 * avg_vol):
        if is_green_candle: score_buy += 2
        else: score_sell += 2

    min_score = 6 if tf == '1h' else 7

    direction = None
    if score_buy >= min_score and score_buy > score_sell and btc_trend != 'BEARISH':
        direction = 'buy'
        final_score = score_buy
    elif score_sell >= min_score and score_sell > score_buy and btc_trend != 'BULLISH':
        direction = 'sell'
        final_score = score_sell

    if not direction:
        return {'is_signal': False, **base_info}

    atr_mult_sl = 1.0
    atr_mult_tp1 = 1.5
    atr_mult_tp2 = 3.0

    if direction == 'buy':
        sl = curr_price - (atr_mult_sl * atr_val)
        tp1 = curr_price + (atr_mult_tp1 * atr_val)
        tp2 = curr_price + (atr_mult_tp2 * atr_val)
        sig_text = "خرید (LONG)"
    else:
        sl = curr_price + (atr_mult_sl * atr_val)
        tp1 = curr_price - (atr_mult_tp1 * atr_val)
        tp2 = curr_price - (atr_mult_tp2 * atr_val)
        sig_text = "فروش (SHORT)"

    risk = abs(curr_price - sl)
    reward = abs(tp2 - curr_price)
    rr_ratio = round(reward / risk, 2) if risk > 0 else 2.5

    account_size = 1000.0
    risk_amount = account_size * 0.01
    stop_pct = risk / curr_price
    position_value = risk_amount / stop_pct if stop_pct > 0 else account_size
    
    max_lev = 15 if tf == '1h' else 10
    leverage = max(1, min(max_lev, int(position_value / account_size)))

    return {
        'is_signal': True,
        'sym': sym,
        'tf': tf.upper(),
        'price': curr_price, 
        'dir': direction,
        'sig': sig_text, 
        'score': final_score,
        'rsi': round(rsi_val, 1),
        'macd_status': macd_status,
        'atr': fp(atr_val),
        'sl': sl, 
        'tp1': tp1, 
        'tp2': tp2,
        'rr': rr_ratio,
        'pos_size': round(position_value, 1),
        'leverage': leverage,
        'time': datetime.now().strftime('%H:%M'),
        'icon': trend_icon
    }

def send(msg):
    if not TOKEN or not CHAT: 
        print("❌ Error: Telegram Token or Chat ID not found.")
        return False
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        d = json.dumps({
            "chat_id": CHAT, 
            "text": msg, 
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }).encode()
        req = urllib.request.Request(url, data=d, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, context=CTX, timeout=10) as x:
            return x.status == 200
    except Exception as e:
        print(f"❌ Telegram Send Error: {e}")
        return False

def fp(n):
    if n >= 1000: return f"{n:,.2f}"
    if n >= 1: return f"{n:,.3f}"
    return f"{n:.5f}"

def fmt(a):
    tv_link = f"https://www.tradingview.com/chart/?symbol=BINANCE:{a['sym']}USDT"
    tf_tag = "⚡️ [تایم‌فریم ۱ ساعته - اسکالپ/دی‌ترید]" if a['tf'] == '1H' else "🌊 [تایم‌فریم ۴ ساعته - روند اصلی]"
    
    return (
        f"{a['icon']} <b>#سیگنال_{a['tf']}_{a['sym']} | USDT</b>\n"
        f"<i>{tf_tag}</i>\n\n"
        f"🎯 جهت معامله: <b>{a['sig']}</b>\n"
        f"📊 قدرت سیگنال: <b>{a['score']} / 15</b>\n"
        f"💰 قیمت ورود: <b>{fp(a['price'])} $</b>\n"
        f"⚖️ نسبت R/R: <b>1:{a['rr']}</b>\n"
        f"📐 اهرم پیشنهادی: <b>{a['leverage']}x</b> | حجم: <b>{a['pos_size']} $</b>\n\n"
        f"🛑 حد زیان: <b>{fp(a['sl'])} $</b>\n"
        f"🎯 حد سود اول (سیو سود): <b>{fp(a['tp1'])} $</b>\n"
        f"🚀 حد سود دوم (اصلی): <b>{fp(a['tp2'])} $</b>\n\n"
        f"📈 وضعیت RSI: <b>{a['rsi']}</b> | MACD: <b>{a['macd_status']}</b>\n"
        f"🔗 <a href='{tv_link}'>مشاهده نمودار در TradingView</a>\n"
        f"📅 زمان ثبت: {a['time']}"
    )

if __name__ == "__main__":
    print("شروع اسکن هوشمند بازار...")
    state = load_state()
    btc_trend = get_btc_macro_trend()
    
    signals = []
    
    for s in COINS:
        a1h = analyze_tf(s, '1h', btc_trend)
        if a1h and a1h['is_signal']:
            state_key = f"{s}_1h"
            if state.get(state_key, {}).get('dir') != a1h['dir']:
                signals.append(a1h)
                state[state_key] = {'dir': a1h['dir'], 'time': a1h['time']}

        a4h = analyze_tf(s, '4h', btc_trend)
        if a4h and a4h['is_signal']:
            state_key = f"{s}_4h"
            if state.get(state_key, {}).get('dir') != a4h['dir']:
                signals.append(a4h)
                state[state_key] = {'dir': a4h['dir'], 'time': a4h['time']}

        time.sleep(0.08)

    save_state(state)

    if signals:
        for sig in signals:
            send(fmt(sig))
            time.sleep(0.3)
        print(f"تعداد {len(signals)} سیگنال جدید ارسال شد.")
    else:
        now = datetime.now().strftime('%H:%M')
        btc_icon = "🟢 صعودی" if btc_trend == 'BULLISH' else ("🔴 نزولی" if btc_trend == 'BEARISH' else "⚪️ خنثی")
        msg = f"📊 <b>گزارش اسکن بازار کریپتو ({now})</b>\n\n"
        msg += f"🌐 روند کلان بیت‌کوین (1D): <b>{btc_icon}</b>\n"
        msg += "• وضعیت: <i>در هر دو تایم‌فریم ۱H و ۴H سیگنال جدید و تاییدشده‌ای یافت نشد.</i>\n\n"
        msg += "🔍 اسکن بعدی سر ساعت انجام می‌شود."
        send(msg)
        print("سیگنال جدیدی یافت نشد. گزارش خلاصه ارسال گردید.")

    print("پایان اسکن.")
