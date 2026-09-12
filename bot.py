# ============================================
# 🤖 ربات ارز دیجیتال - نسخه نهایی و کامل
# ============================================

import json, time, os, ssl, urllib.request, warnings
from datetime import datetime
warnings.filterwarnings('ignore')

# 🔑 دریافت اطلاعات از GitHub Secrets
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT = os.getenv("TELEGRAM_CHAT_ID")

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

COINS = [
    'BTC', 'ETH', 'BNB', 'SOL', 'XRP',
    'ADA', 'DOGE', 'TRX', 'LINK', 'AVAX',
    'DOT', 'MATIC', 'ATOM', 'NEAR', 'FIL',
    'APT', 'ARB', 'OP', 'INJ', 'SUI'
]

# دامنه‌های پشتیبان بایننس برای دور زدن محدودیت آی‌پی GitHub Actions
BINANCE_HOSTS = [
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com"
]


def http(path, t=20, retries=2):
    last_err = None
    for host in BINANCE_HOSTS:
        url = f"{host}{path}" if path.startswith('/') else path
        for attempt in range(retries):
            try:
                req = urllib.request.Request(url)
                req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)')
                req.add_header('Accept', 'application/json')
                with urllib.request.urlopen(req, context=CTX, timeout=t) as x:
                    return json.loads(x.read().decode())
            except Exception as e:
                last_err = e
                time.sleep(0.5)
    raise last_err


_cache = {}
_cache_t = 0


def tickers():
    global _cache, _cache_t
    if _cache and time.time() - _cache_t < 60:
        return _cache
    try:
        d = http("/api/v3/ticker/24hr", t=25)
        _cache = {i['symbol']: i for i in d}
        _cache_t = time.time()
    except Exception as e:
        print(f"  ⚠️ warn ticker: {e}")
    return _cache


def klines(sym, tf):
    try:
        path = f"/api/v3/klines?symbol={sym}USDT&interval={tf}&limit=100"
        d = http(path, t=15)
        if not d or not isinstance(d, list):
            return None
            
        closes = [float(c[4]) for c in d]
        vols = [float(c[5]) for c in d]
        t = tickers().get(f"{sym}USDT", {})
        
        return {
            'p': closes,
            'v': vols,
            'price': float(t.get('lastPrice', closes[-1] if closes else 0)),
            'chg': float(t.get('priceChangePercent', 0)),
        }
    except Exception as e:
        print(f"  ⚠️ Error fetching klines for {sym} ({tf}): {e}")
        return None


def ema(p, n):
    if len(p) < n:
        return None
    k = 2 / (n + 1)
    e = sum(p[:n]) / n
    for x in p[n:]:
        e = x * k + e * (1 - k)
    return e


def rsi(p, n=14):
    if len(p) < n + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(p)):
        change = p[i] - p[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))

    avg_gain = sum(gains[:n]) / n
    avg_loss = sum(losses[:n]) / n

    for i in range(n, len(gains)):
        avg_gain = (avg_gain * (n - 1) + gains[i]) / n
        avg_loss = (avg_loss * (n - 1) + losses[i]) / n

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def macd_histogram(p):
    if len(p) < 35:
        return None

    def calculate_ema_sequence(data, period):
        k = 2 / (period + 1)
        val = sum(data[:period]) / period
        res = [val]
        for x in data[period:]:
            val = x * k + val * (1 - k)
            res.append(val)
        return res

    ema12 = calculate_ema_sequence(p, 12)
    ema26 = calculate_ema_sequence(p, 26)

    offset = len(ema12) - len(ema26)
    macd_line = [f - s for f, s in zip(ema12[offset:], ema26)]
    if len(macd_line) < 9:
        return None

    signal_line = calculate_ema_sequence(macd_line, 9)
    return macd_line[-1] - signal_line[-1]


def trend(tf_data):
    if not tf_data:
        return 'side'
    e9, e21, e50 = tf_data.get('e9'), tf_data.get('e21'), tf_data.get('e50')
    if not (e9 and e21 and e50):
        return 'side'
    if e9 > e21 > e50:
        return 'up'
    if e9 < e21 < e50:
        return 'down'
    return 'side'


def analyze(sym):
    tfs = {'15m': '۱۵د', '1h': '۱س', '4h': '۴س', '1d': 'روز'}
    res = {}

    for i, n in tfs.items():
        k = klines(sym, i)
        if not k or len(k['p']) < 50:
            continue

        e9 = ema(k['p'], 9)
        e21 = ema(k['p'], 21)
        e50 = ema(k['p'], 50)
        rr = rsi(k['p'])
        h = macd_histogram(k['p'])

        va = sum(k['v'][-6:-1]) / 5 if len(k['v']) >= 6 else 1
        vr = k['v'][-1] / va if va > 0 else 1

        bs = ss = 0

        # امتیازدهی شاخص RSI
        if 50 < rr < 65:
            bs += 2
        elif 35 < rr < 50:
            ss += 2

        # امتیازدهی MACD
        if h is not None:
            if h > 0:
                bs += 2
            else:
                ss += 2

        # امتیازدهی روند EMA
        if e9 and e21 and e50:
            if e9 > e21 > e50:
                bs += 3
            elif e9 < e21 < e50:
                ss += 3

        # امتیازدهی حجم
        if vr > 1.1:
            if k['p'][-1] > k['p'][-2]:
                bs += 2
            else:
                ss += 2

        res[n] = {
            'price': k['price'], 'chg': k['chg'], 'rsi': rr,
            'e9': e9, 'e21': e21, 'e50': e50,
            'vol': vr, 'bs': bs, 'ss': ss, 'macd': h,
        }
        time.sleep(0.05)

    if len(res) < 4:
        return None

    t4, t1 = res.get('۴س'), res.get('روز')
    if not t4 or not t1:
        return None

    r4, r1 = trend(t4), trend(t1)

    if r4 == 'up' and r1 == 'up':
        d = 'buy'
    elif r4 == 'down' and r1 == 'down':
        d = 'sell'
    else:
        return None

    w = {'۱۵د': 0.5, '۱س': 1.0, '۴س': 2.0, 'روز': 3.0}
    wb = sum(res[n]['bs'] * w.get(n, 1) for n in res)
    ws = sum(res[n]['ss'] * w.get(n, 1) for n in res)

    rsi4, rsi1 = t4['rsi'], t1['rsi']

    if d == 'buy':
        ok = 38 < rsi4 < 72 and 38 < rsi1 < 72
        sc = wb
    else:
        ok = 28 < rsi4 < 62 and 28 < rsi1 < 62
        sc = ws

    vol_ok = t4['vol'] >= 0.85

    # حد نصاب منطقی برای صدور سیگنال
    if d == 'buy':
        if sc >= 12 and ok and vol_ok:
            sig, act, st = "خرید قوی", "ورود", 'strong'
        elif sc >= 8 and ok and vol_ok:
            sig, act, st = "خرید متوسط", "بررسی", 'medium'
        else:
            return None
    else:
        if sc >= 12 and ok and vol_ok:
            sig, act, st = "فروش قوی", "خروج", 'strong'
        elif sc >= 8 and ok and vol_ok:
            sig, act, st = "فروش متوسط", "بررسی", 'medium'
        else:
            return None

    return {
        'sym': sym, 'tf': res, 'score': round(sc, 1),
        'dir': d, 'sig': sig, 'act': act, 'st': st,
        'time': datetime.now().strftime('%m-%d %H:%M'),
    }


def send(msg):
    if not TOKEN or not CHAT:
        print("  ❌ Error: TELEGRAM_TOKEN or TELEGRAM_CHAT_ID is missing!")
        return False
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        d = json.dumps({
            "chat_id": CHAT, "text": msg,
            "parse_mode": "HTML", "disable_web_page_preview": True,
        }).encode()
        req = urllib.request.Request(url, data=d)
        req.add_header('Content-Type', 'application/json')
        with urllib.request.urlopen(req, context=CTX, timeout=25) as x:
            return x.status == 200
    except Exception as e:
        print(f"  ⚠️ warn telegram: {e}")
        return False


def fp(n):
    if n is None:
        return "-"
    if n >= 1000:
        return f"{n:,.2f}"
    if n >= 1:
        return f"{n:,.4f}"
    if n >= 0.01:
        return f"{n:.6f}"
    return f"{n:.8f}"


def rsi_status(r):
    if r > 70:
        return "اشباع خرید"
    if r < 30:
        return "اشباع فروش"
    if r > 60:
        return "بالا"
    if r < 40:
        return "پایین"
    return "خنثی"


def vol_status(v):
    if v > 2:
        return "خیلی بالا"
    if v > 1.5:
        return "بالا"
    if v > 1:
        return "معمولی"
    return "کم"


def ema_status(e9, e21, e50):
    if not (e9 and e21 and e50):
        return "نامشخص"
    if e9 > e21 > e50:
        return "صعودی قوی"
    if e9 < e21 < e50:
        return "نزولی قوی"
    return "مخلوط"


def chg_status(c):
    if c > 5:
        return "رشد شدید"
    if c > 2:
        return "صعودی"
    if c < -5:
        return "ریزش شدید"
    if c < -2:
        return "نزولی"
    return "خنثی"


def fmt(a):
    t4 = a['tf']['۴س']
    t1 = a['tf']['روز']

    icon = "🟢" if a['dir'] == 'buy' else "🔴"
    strength_fa = "قوی" if a['st'] == 'strong' else "متوسط"

    m = (
        f"{icon} <b>{a['sym']}/USDT</b>\n\n"
        f"🎯 سیگنال: <b>{a['sig']} {strength_fa}</b>\n"
        f"📌 اقدام: {a['act']}\n"
        f"📊 امتیاز: <b>{a['score']}</b>\n\n"
        f"💰 قیمت: <b>{fp(t4['price'])}</b> دلار\n"
        f"📈 تغییر ۲۴ ساعت: {t4['chg']:+.2f}% ({chg_status(t4['chg'])})\n\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"⏱ <b>تایم‌فریم ۴ ساعته</b>\n"
        f"• RSI: {t4['rsi']:.1f} ({rsi_status(t4['rsi'])})\n"
        f"• EMA9: {fp(t4['e9'])}\n"
        f"• EMA21: {fp(t4['e21'])}\n"
        f"• EMA50: {fp(t4['e50'])}\n"
        f"• روند: {ema_status(t4['e9'], t4['e21'], t4['e50'])}\n"
        f"• حجم: {t4['vol']:.1f}x ({vol_status(t4['vol'])})\n\n"
        f"⏱ <b>تایم‌فریم روزانه</b>\n"
        f"• RSI: {t1['rsi']:.1f} ({rsi_status(t1['rsi'])})\n"
        f"• EMA9: {fp(t1['e9'])}\n"
        f"• EMA21: {fp(t1['e21'])}\n"
        f"• EMA50: {fp(t1['e50'])}\n"
        f"• روند: {ema_status(t1['e9'], t1['e21'], t1['e50'])}\n\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📅 {a['time']}"
    )
    return m[:4000]


# ============================================
# 🎯 اجرای اصلی
# ============================================

if __name__ == "__main__":
    print("=" * 50)
    print("Starting Crypto Bot Scan...")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)

    # تست اولیه سلامت اتصال به تلگرام
    test_sent = send("🤖 <b>تست ربات سیگنال</b>\n\nربات اجرا شد و در حال اسکن بازار است...")
    if test_sent:
        print("  ✅ Test message sent to Telegram successfully!")
    else:
        print("  ❌ Failed to send test message. Check TELEGRAM_TOKEN and TELEGRAM_CHAT_ID.")

    sent_count = 0
    valid_count = 0

    tickers()

    for s in COINS:
        a = analyze(s)
        if not a:
            continue
        valid_count += 1

        if send(fmt(a)):
            sent_count += 1
            print(f"  SENT: {s} | Signal: {a['sig']} ({a['score']})")
            time.sleep(1)

        time.sleep(0.1)

    print("-" * 50)
    print(f"Scan Completed. Valid Signals: {valid_count} | Sent: {sent_count}")
    print("=" * 50)
