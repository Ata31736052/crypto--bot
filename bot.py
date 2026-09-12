# ============================================
# 🤖 ربات سیگنال‌دهی حرفه‌ای ارز دیجیتال - نسخه VIP
# ============================================

import json, time, os, ssl, urllib.request, warnings
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

warnings.filterwarnings('ignore')

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT = os.getenv("TELEGRAM_CHAT_ID")

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

# لیست ۳۵ ارز معتبر و با نقدینگی بالا
COINS = [
    'BTC', 'ETH', 'SOL', 'BNB', 'XRP',
    'DOGE', 'ADA', 'TRX', 'AVAX', 'LINK',
    'DOT', 'SUI', 'NEAR', 'APT', 'FET',
    'RENDER', 'TAO', 'TIA', 'INJ', 'OP',
    'ARB', 'MATIC', 'ATOM', 'FIL', 'AAVE',
    'UNI', 'LDO', 'STX', 'GMX', 'PENDLE',
    'PEPE', 'SHIB', 'WIF', 'FLOKI', 'BONK'
]

BINANCE_HOSTS = [
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com"
]

def http(path, t=10):
    for host in BINANCE_HOSTS:
        url = f"{host}{path}" if path.startswith('/') else path
        try:
            req = urllib.request.Request(url)
            req.add_header('User-Agent', 'Mozilla/5.0')
            with urllib.request.urlopen(req, context=CTX, timeout=t) as x:
                return json.loads(x.read().decode())
        except Exception:
            continue
    return None

_cache = {}
def get_tickers():
    global _cache
    d = http("/api/v3/ticker/24hr", t=15)
    if d and isinstance(d, list):
        _cache = {i['symbol']: i for i in d}
    return _cache

def klines(sym, tf):
    d = http(f"/api/v3/klines?symbol={sym}USDT&interval={tf}&limit=60", t=10)
    if not d or not isinstance(d, list):
        return None
    
    closes = [float(c[4]) for c in d]
    highs = [float(c[2]) for c in d]
    lows = [float(c[3]) for c in d]
    vols = [float(c[5]) for c in d]
    
    t = _cache.get(f"{sym}USDT", {})
    return {
        'p': closes, 'h': highs, 'l': lows, 'v': vols,
        'price': float(t.get('lastPrice', closes[-1] if closes else 0)),
        'chg': float(t.get('priceChangePercent', 0)),
    }

# --------------------------------------------
# 📊 اندیکاتورهای فنی
# --------------------------------------------

def ema(p, n):
    if len(p) < n: return None
    k = 2 / (n + 1)
    e = sum(p[:n]) / n
    for x in p[n:]: e = x * k + e * (1 - k)
    return e

def rsi(p, n=14):
    if len(p) < n + 1: return 50.0
    gains = [max(p[i] - p[i-1], 0) for i in range(1, len(p))]
    losses = [max(p[i-1] - p[i], 0) for i in range(1, len(p))]
    avg_g = sum(gains[:n]) / n
    avg_l = sum(losses[:n]) / n
    for i in range(n, len(gains)):
        avg_g = (avg_g * (n - 1) + gains[i]) / n
        avg_l = (avg_l * (n - 1) + losses[i]) / n
    if avg_l == 0: return 100.0
    return 100.0 - (100.0 / (1.0 + (avg_g / avg_l)))

def stoch_rsi(p, n=14):
    rsi_vals = []
    for i in range(n, len(p) + 1):
        rsi_vals.append(rsi(p[:i], n))
    if len(rsi_vals) < n: return 50.0
    curr = rsi_vals[-1]
    low_r, high_r = min(rsi_vals[-n:]), max(rsi_vals[-n:])
    if high_r == low_r: return 50.0
    return ((curr - low_r) / (high_r - low_r)) * 100

def atr(h, l, c, n=14):
    if len(c) < n + 1: return None
    tr = [max(h[i] - l[i], abs(h[i] - c[i-1]), abs(l[i] - c[i-1])) for i in range(1, len(c))]
    return sum(tr[-n:]) / n

def macd_hist(p):
    if len(p) < 35: return 0
    def calc_ema(data, period):
        k = 2 / (period + 1)
        v = sum(data[:period]) / period
        res = [v]
        for x in data[period:]:
            v = x * k + v * (1 - k)
            res.append(v)
        return res
    e12 = calc_ema(p, 12)
    e26 = calc_ema(p, 26)
    macd = [f - s for f, s in zip(e12[len(e12)-len(e26):], e26)]
    sig = calc_ema(macd, 9)
    return macd[-1] - sig[-1]

# --------------------------------------------
# 🎯 تحلیل حرفه‌ای
# --------------------------------------------

def analyze(sym):
    tfs = {'1h': '۱س', '4h': '۴س', '1d': 'روز'}
    res = {}

    for tf_code, tf_fa in tfs.items():
        k = klines(sym, tf_code)
        if not k or len(k['p']) < 40: continue

        e9, e21, e50 = ema(k['p'], 9), ema(k['p'], 21), ema(k['p'], 50)
        r_val = rsi(k['p'])
        stoch = stoch_rsi(k['p'])
        m_hist = macd_hist(k['p'])
        atr_val = atr(k['h'], k['l'], k['p'])

        v_avg = sum(k['v'][-20:-1]) / 19 if len(k['v']) >= 20 else 1
        v_rat = k['v'][-1] / v_avg if v_avg > 0 else 1

        bs = ss = 0

        # ۱. روند بر اساس EMAها
        if e9 and e21 and e50:
            if e9 > e21 > e50: bs += 4
            elif e9 < e21 < e50: ss += 4

        # ۲. شاخص RSI & Stoch RSI
        if 45 < r_val < 65 and stoch < 80: bs += 2.5
        elif 35 < r_val < 55 and stoch > 20: ss += 2.5

        # ۳. مکدی
        if m_hist > 0: bs += 2
        else: ss += 2

        # ۴. تایید حجم
        if v_rat > 1.2:
            if k['p'][-1] > k['p'][-2]: bs += 2.5
            else: ss += 2.5

        res[tf_fa] = {
            'price': k['price'], 'chg': k['chg'], 'rsi': r_val,
            'e9': e9, 'e21': e21, 'e50': e50, 'vol': v_rat,
            'bs': bs, 'ss': ss, 'atr': atr_val
        }

    if len(res) < 3: return None

    t4, t1 = res.get('۴س'), res.get('روز')
    if not t4 or not t1: return None

    # تعیین جهت کلی با تایید تایم‌فریم بالا
    if t4['bs'] >= 7 and t1['bs'] >= 5: d = 'buy'
    elif t4['ss'] >= 7 and t1['ss'] >= 5: d = 'sell'
    else: return None

    score = t4['bs'] if d == 'buy' else t4['ss']
    if score < 7.5: return None

    price = t4['price']
    atr_val = t4['atr'] or (price * 0.02)

    # محاسبه Stop Loss & Take Profit بر اساس نوسانات ATR
    if d == 'buy':
        sl = price - (atr_val * 1.5)
        tp1 = price + (atr_val * 1.8)
        tp2 = price + (atr_val * 3.2)
        sig_name = "خرید قوی (LONG)" if score >= 9.5 else "خرید متوسط"
    else:
        sl = price + (atr_val * 1.5)
        tp1 = price - (atr_val * 1.8)
        tp2 = price - (atr_val * 3.2)
        sig_name = "فروش قوی (SHORT)" if score >= 9.5 else "فروش متوسط"

    return {
        'sym': sym, 'dir': d, 'sig': sig_name, 'score': round(score, 1),
        'price': price, 'chg': t4['chg'], 'rsi': t4['rsi'],
        'vol': t4['vol'], 'sl': sl, 'tp1': tp1, 'tp2': tp2,
        'time': datetime.now().strftime('%H:%M')
    }

def fp(n):
    if not n: return "-"
    if n >= 1000: return f"{n:,.2f}"
    if n >= 1: return f"{n:,.4f}"
    return f"{n:.6f}"

def fmt(a):
    icon = "🟢 #LONG" if a['dir'] == 'buy' else "🔴 #SHORT"
    m = (
        f"{icon} | <b>{a['sym']}/USDT</b>\n\n"
        f"🎯 سیگنال: <b>{a['sig']}</b>\n"
        f"📊 قدر سیگنال: <b>{a['score']} / 11</b>\n"
        f"💰 قیمت ورود: <b>{fp(a['price'])} USDT</b>\n"
        f"📈 تغییر ۲۴h: {a['chg']:+.2f}%\n\n"
        f"🎯 <b>تارگت‌ها و حد زیان پیشنهاد شده:</b>\n"
        f"🔹 حد سود اول (TP1): <b>{fp(a['tp1'])}</b>\n"
        f"🚀 حد سود دوم (TP2): <b>{fp(a['tp2'])}</b>\n"
        f"🛑 حد زیان (SL): <b>{fp(a['sl'])}</b>\n\n"
        f"📊 شاخص RSI: {a['rsi']:.1f} | حجم: {a['vol']:.1f}x\n"
        f"⏰ زمان اسکن: {a['time']}"
    )
    return m

def send(msg):
    if not TOKEN or not CHAT: return False
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        d = json.dumps({"chat_id": CHAT, "text": msg, "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(url, data=d)
        req.add_header('Content-Type', 'application/json')
        with urllib.request.urlopen(req, context=CTX, timeout=10) as x:
            return x.status == 200
    except Exception: return False

if __name__ == "__main__":
    print("🚀 Starting Fast VIP Crypto Scan...")
    get_tickers()
    
    # اسکن همزمان ارزها با ۱۰ نخ پردازشی (سرعت بالا)
    signals = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        results = executor.map(analyze, COINS)
        for r in results:
            if r: signals.append(r)

    sent_count = 0
    for s in signals:
        if send(fmt(s)):
            sent_count += 1
            time.sleep(0.5)

    print(f"✅ Scan finished in seconds! Signals found & sent: {sent_count}")
    
