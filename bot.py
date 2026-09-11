# ============================================
# 🤖 ربات ارز دیجیتال - نسخه نهایی
# ============================================

import json, time, os, ssl, urllib.request, warnings
from datetime import datetime
warnings.filterwarnings('ignore')

TOKEN = "8838013512:AAEnGtEodh2SrWmuL4uhsPtzlGO6Lx2fX9o"
CHAT = "90464197"

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

# ۲۰ ارز با پشتوانه قوی و پتانسیل رشد
COINS = [
    'BTC', 'ETH', 'BNB', 'SOL', 'XRP',
    'ADA', 'DOGE', 'TRX', 'LINK', 'AVAX',
    'DOT', 'MATIC', 'ATOM', 'NEAR', 'FIL',
    'APT', 'ARB', 'OP', 'INJ', 'SUI'
]


def http(url, t=30, retries=2):
    last_err = None
    for attempt in range(retries + 1):
        try:
            r = urllib.request.Request(url)
            r.add_header('User-Agent', 'Mozilla/5.0')
            r.add_header('Accept', 'application/json')
            with urllib.request.urlopen(r, context=CTX, timeout=t) as x:
                return json.loads(x.read().decode())
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(1.5)
    raise last_err


_cache = {}
_cache_t = 0


def tickers():
    global _cache, _cache_t
    if _cache and time.time() - _cache_t < 60:
        return _cache
    try:
        d = http("https://api.binance.com/api/v3/ticker/24hr", t=30)
        _cache = {i['symbol']: i for i in d}
        _cache_t = time.time()
    except Exception as e:
        print(f"  warn ticker: {e}")
    return _cache


def klines(sym, tf):
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={sym}USDT&interval={tf}&limit=100"
        d = http(url, t=20)
        closes = [float(c[4]) for c in d]
        vols = [float(c[5]) for c in d][:-1]
        t = tickers().get(f"{sym}USDT", {})
        return {
            'p': closes,
            'v': vols,
            'price': float(t.get('lastPrice', closes[-1] if closes else 0)),
            'chg': float(t.get('priceChangePercent', 0)),
        }
    except Exception:
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
    d = [p[i] - p[i - 1] for i in range(1, len(p))]
    g = sum(x for x in d[:n] if x > 0) / n
    l = sum(-x for x in d[:n] if x < 0) / n
    for x in d[n:]:
        g = (g * (n - 1) + (x if x > 0 else 0)) / n
        l = (l * (n - 1) + (-x if x < 0 else 0)) / n
    if l == 0:
        return 90.0
    return max(5.0, min(95.0, 100 - 100 / (1 + g / l)))


def macd(p):
    if len(p) < 35:
        return None

    def es(pp, n):
        k = 2 / (n + 1)
        e = sum(pp[:n]) / n
        r = [e]
        for x in pp[n:]:
            e = x * k + e * (1 - k)
            r.append(e)
        return r

    e12, e26 = es(p, 12), es(p, 26)
    m = min(len(e12), len(e26))
    line = [a - b for a, b in zip(e12[-m:], e26[-m:])]
    sig = es(line, 9)
    if not sig:
        return None
    return line[-1] - sig[-1]


def trend(tf):
    if not tf:
        return 'side'
    e9, e21, e50 = tf.get('e9'), tf.get('e21'), tf.get('e50')
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
        if not k or len(k['p']) < 30:
            continue
        e9 = ema(k['p'], 9)
        e21 = ema(k['p'], 21)
        e50 = ema(k['p'], 50)
        rr = rsi(k['p'])
        h = macd(k['p'])
        va = sum(k['v'][-5:]) / 5 if len(k['v']) >= 5 else 1
        vr = k['v'][-1] / va if va > 0 else 1
        bs = ss = 0
        if rr > 70:
            ss += 2
        elif rr < 30:
            bs += 2
        elif rr > 60:
            ss += 1
        elif rr < 40:
            bs += 1
        if h is not None:
            if h > 0:
                bs += 2
            else:
                ss += 2
        if e9 and e21 and e50:
            if e9 > e21 > e50:
                bs += 3
            elif e9 < e21 < e50:
                ss += 3
            elif e9 > e21:
                bs += 1
            elif e9 < e21:
                ss += 1
        if vr > 2:
            if bs > ss:
                bs += 2
            else:
                ss += 2
        res[n] = {
            'price': k['price'], 'chg': k['chg'], 'rsi': rr,
            'e9': e9, 'e21': e21, 'e50': e50,
            'vol': vr, 'bs': bs, 'ss': ss, 'macd': h,
        }
        time.sleep(0.1)

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
        ok = 50 < rsi4 < 65 and 50 < rsi1 < 68
        sc = wb
    else:
        ok = 35 < rsi4 < 50 and 32 < rsi1 < 50
        sc = ws

    vol_ok = t4['vol'] > 1.3

    if d == 'buy':
        if sc >= 40 and ok and vol_ok:
            sig, act, st = "خرید قوی", "ورود", 'strong'
        elif sc >= 25 and ok and vol_ok:
            sig, act, st = "خرید متوسط", "بررسی", 'medium'
        else:
            return None
    else:
        if sc >= 40 and ok and vol_ok:
            sig, act, st = "فروش قوی", "خروج", 'strong'
        elif sc >= 25 and ok and vol_ok:
            sig, act, st = "فروش متوسط", "بررسی", 'medium'
        else:
            return None

    return {
        'sym': sym, 'tf': res, 'score': round(sc, 1),
        'dir': d, 'sig': sig, 'act': act, 'st': st,
        'time': datetime.now().strftime('%m-%d %H:%M'),
    }


def send(msg):
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        d = json.dumps({
            "chat_id": CHAT, "text": msg,
            "parse_mode": "HTML", "disable_web_page_preview": True,
        }).encode()
        r = urllib.request.Request(url, data=d)
        r.add_header('Content-Type', 'application/json')
        with urllib.request.urlopen(r, context=CTX, timeout=30) as x:
            return x.status == 200
    except Exception as e:
        print(f"  warn telegram: {e}")
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

print("=" * 50)
print("Robot v6")
print("=" * 50)
print(f"Coins: {len(COINS)}")
print("Filter: 4H + 1D")
print("=" * 50)

send("🤖 <b>ربات راه‌اندازی شد</b>\n\n"
     f"📊 {len(COINS)} ارز\n"
     f"⏱ فیلتر: ۴ ساعته + روزانه\n"
     f"🎯 فقط سیگنال معتبر")

alerted = {}

while True:
    try:
        print(f"\nCycle: {datetime.now().strftime('%H:%M:%S')}")
        print("-" * 50)
        sent_count = 0
        valid_count = 0

        tickers()

        for s in COINS:
            a = analyze(s)
            if not a:
                continue
            valid_count += 1

            now = time.time()
            last = alerted.get(s, 0)
            cd = 600 if a['st'] == 'strong' else 3600

            if now - last >= cd:
                if send(fmt(a)):
                    alerted[s] = now
                    sent_count += 1
                    print(f"  SENT {s} | {a['sig']} ({a['score']})")
                    time.sleep(4)
            else:
                print(f"  skip {s}")

            time.sleep(0.3)

        print(f"\nValid: {valid_count} | Sent: {sent_count}")

        if len(alerted) > 200:
            alerted = {k: v for k, v in alerted.items() if v > time.time() - 7200}

        print("Wait 3 min...")
        time.sleep(180)

    except KeyboardInterrupt:
        print("\nStopped")
        break
    except Exception as e:
        print(f"Error: {e}")
        time.sleep(15)
