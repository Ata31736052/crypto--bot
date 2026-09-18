# ============================================
# 🤖 ربات جامع سیگنال‌دهی کریپتو - تایم‌فریم ۴ ساعته (4H) - نسخه نهایی
# 🎯 با ۳۰ ارز برتر، تاییدیه MACD، ATR، RSI، EMA و پرایس‌اکشن
# ============================================

import json, time, os, ssl, urllib.request, warnings
from datetime import datetime
warnings.filterwarnings('ignore')

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT = os.getenv("TELEGRAM_CHAT_ID")

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

# لیست ۳۰ ارز محبوب و پرمعامله بازار (به‌روزرسانی شده با نماد GRAM)
COINS = [
    'BTC', 'ETH', 'SOL', 'BNB', 'XRP', 
    'ADA', 'DOGE', 'AVAX', 'LINK', 'DOT', 
    'NEAR', 'SUI', 'APT', 'ARB', 'OP',
    'PEPE', 'FET', 'RNDR', 'INJ', 'MATIC',
    'GRAM', 'NOT', 'SHIB', 'LTC', 'TRX',
    'ATOM', 'TIA', 'WIF', 'SEI', 'STX'
]

def http(url, t=10):
    try:
        req = urllib.request.Request(
            url, 
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
        )
        with urllib.request.urlopen(req, context=CTX, timeout=t) as x:
            return json.loads(x.read().decode())
    except Exception:
        return None

def get_ticker_price(sym):
    # ۱. دریافت قیمت لحظه‌ای و دقیق از بایننس
    url_bn = f"https://data-api.binance.vision/api/v3/ticker/price?symbol={sym}USDT"
    d = http(url_bn)
    if d and isinstance(d, dict) and 'price' in d:
        return float(d['price'])
    
    # ۲. سرور پشتیبان: دریافت قیمت لحظه‌ای از کوکوین
    url_kc = f"https://api.kucoin.com/api/v1/market/orderbook/level1?symbol={sym}-USDT"
    d_kc = http(url_kc)
    if d_kc and d_kc.get('code') == '200000' and isinstance(d_kc.get('data'), dict) and 'price' in d_kc['data']:
        return float(d_kc['data']['price'])
        
    return None

def klines(sym, tf='4h'):
    # دریافت قیمت زنده و آنلاین بازار
    live_price = get_ticker_price(sym)
    
    # ۱. دریافت ۱۲۰ کندل از Binance برای محاسبه دقیق EMA50
    url_bn = f"https://data-api.binance.vision/api/v3/klines?symbol={sym}USDT&interval={tf}&limit=120"
    d = http(url_bn)
    if d and isinstance(d, list) and len(d) > 1:
        if not live_price:
            live_price = float(d[-1][4])
            
        d = d[:-1] # حذف کندل جاری (در حال تشکیل) برای تحلیل دقیق تکنیکال
        opens = [float(c[1]) for c in d]
        highs = [float(c[2]) for c in d]
        lows = [float(c[3]) for c in d]
        closes = [float(c[4]) for c in d]
        vols = [float(c[5]) for c in d]
        return {'o': opens, 'h': highs, 'l': lows, 'p': closes, 'v': vols, 'price': live_price}

    # ۲. سرور پشتیبان KuCoin
    url_kc = f"https://api.kucoin.com/api/v1/market/candles?symbol={sym}-USDT&type=4hour"
    d = http(url_kc)
    if d and d.get('code') == '200000' and isinstance(d.get('data'), list) and len(d['data']) > 1:
        data = d['data'][1:121] # حذف کندل جاری در کوکوین
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
    
    # هم‌راستاسازی طول آرایه‌ها
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

def analyze(sym):
    k4h = klines(sym, '4h')
    if not k4h:
        return None

    p4h, h4h, l4h, o4h, v4h = k4h['p'], k4h['h'], k4h['l'], k4h['o'], k4h['v']
    curr_price = k4h['price']
    
    e9 = ema(p4h, 9)
    e21 = ema(p4h, 21)
    e50 = ema(p4h, 50)
    rsi4h = rsi(p4h)
    atr4h = atr(h4h, l4h, p4h, 14)
    macd_val, signal_val, hist_val = macd(p4h)

    if not (e9 and e21 and e50 and atr4h and macd_val is not None):
        return None

    score_buy = 0
    score_sell = 0

    # ۱. ترتیب میانگین‌های متحرک (EMA Trend)
    if e9 > e21: score_buy += 3
    if e21 > e50: score_buy += 2
    if e9 < e21: score_sell += 3
    if e21 < e50: score_sell += 2

    # ۲. وضعیت اندیکاتور RSI
    if 45 < rsi4h < 68: score_buy += 3
    if 32 < rsi4h < 55: score_sell += 3

    # ۳. تاییدیه اندیکاتور MACD
    macd_status = "خنثی"
    if hist_val > 0:
        score_buy += 2
        macd_status = "صعودی 🟢"
    elif hist_val < 0:
        score_sell += 2
        macd_status = "نزولی 🔴"

    # ۴. فیلتر حجم معاملات (ترکیب حجم با کندل جهتی)
    avg_vol = sum(v4h[-21:-1]) / 20 if len(v4h) >= 21 else sum(v4h) / len(v4h)
    is_green_candle = p4h[-1] > o4h[-1]
    if v4h[-1] > avg_vol:
        if is_green_candle:
            score_buy += 1
        else:
            score_sell += 1

    # ۵. الگوی کندلی بازگشتی (Pin Bar)
    body = abs(p4h[-1] - o4h[-1])
    candle_range = h4h[-1] - l4h[-1]
    if candle_range > 0:
        lower_shadow = min(p4h[-1], o4h[-1]) - l4h[-1]
        upper_shadow = h4h[-1] - max(p4h[-1], o4h[-1])
        if lower_shadow > (2 * body) and lower_shadow > (0.5 * candle_range):
            score_buy += 2
        if upper_shadow > (2 * body) and upper_shadow > (0.5 * candle_range):
            score_sell += 2

    direction = None
    final_score = 0
    
    # حد نصاب ۷ از ۱۳ برای صدور سیگنال ورود
    if score_buy >= 7 and score_buy > score_sell:
        direction = 'buy'
        final_score = score_buy
    elif score_sell >= 7 and score_sell > score_buy:
        direction = 'sell'
        final_score = score_sell

    trend_icon = "🟢" if e9 > e21 else "🔴"

    base_info = {
        'sym': sym, 
        'price': curr_price, 
        'rsi4h': round(rsi4h, 1),
        'icon': trend_icon
    }

    if not direction:
        return {'is_signal': False, **base_info}

    if direction == 'buy':
        sl = curr_price - (1.5 * atr4h)
        tp1 = curr_price + (2.0 * atr4h)
        tp2 = curr_price + (4.0 * atr4h)
        sig_text = "خرید (LONG)"
    else:
        sl = curr_price + (1.5 * atr4h)
        tp1 = curr_price - (2.0 * atr4h)
        tp2 = curr_price - (4.0 * atr4h)
        sig_text = "فروش (SHORT)"

    risk = abs(curr_price - sl)
    reward = abs(tp2 - curr_price)
    rr_ratio = round(reward / risk, 2) if risk > 0 else 2.0

    return {
        'is_signal': True,
        'sym': sym, 
        'price': curr_price, 
        'dir': direction,
        'sig': sig_text, 
        'score': final_score,
        'rsi4h': round(rsi4h, 1),
        'macd_status': macd_status,
        'atr4h': fp(atr4h),
        'sl': sl, 
        'tp1': tp1, 
        'tp2': tp2,
        'rr': rr_ratio,
        'time': datetime.now().strftime('%H:%M'),
        'icon': trend_icon
    }

def send(msg):
    if not TOKEN or not CHAT: return False
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
    except Exception:
        return False

def fp(n):
    if n >= 1000: return f"{n:,.2f}"
    if n >= 1: return f"{n:,.3f}"
    return f"{n:.5f}"

def fmt(a):
    tv_link = f"https://www.tradingview.com/chart/?symbol=BINANCE:{a['sym']}USDT"
    
    return (
        f"{a['icon']} <b>#سیگنال_4ساعته_{a['sym']} | USDT</b>\n\n"
        f"🎯 جهت معامله: <b>{a['sig']}</b>\n"
        f"📊 قدرت سیگنال: <b>{a['score']} / 13</b>\n"
        f"💰 قیمت ورود: <b>{fp(a['price'])} $</b>\n"
        f"⚖️ نسبت ریسک به ریوارد (R/R): <b>1:{a['rr']}</b>\n"
        f"📏 دامنه نوسان (ATR 14): <b>{a['atr4h']} $</b>\n\n"
        f"🛑 حد زیان (1.5x ATR): <b>{fp(a['sl'])} $</b>\n"
        f"🎯 حد سود اول (2x ATR): <b>{fp(a['tp1'])} $</b>\n"
        f"🚀 حد سود دوم (4x ATR): <b>{fp(a['tp2'])} $</b>\n\n"
        f"📈 وضعیت RSI (4H): <b>{a['rsi4h']}</b>\n"
        f"📊 وضعیت MACD: <b>{a['macd_status']}</b>\n"
        f"🔗 <a href='{tv_link}'>مشاهده نمودار در TradingView</a>\n"
        f"📅 زمان ثبت: {a['time']}"
    )

if __name__ == "__main__":
    print("شروع اسکن بازار در تایم‌فریم ۴ ساعته...")
    
    signals = []
    market_summary = []
    
    for s in COINS:
        a = analyze(s)
        if a:
            if a['is_signal']:
                signals.append(a)
            market_summary.append(a)
        time.sleep(0.1)

    if signals:
        for sig in signals:
            send(fmt(sig))
            time.sleep(0.3)
    else:
        now = datetime.now().strftime('%H:%M')
        
        if market_summary:
            avg_rsi_4h = round(sum(item['rsi4h'] for item in market_summary) / len(market_summary), 1)
            
            msg = f"📊 <b>گزارش بازار کریپتو (۴ ساعته - {now})</b>\n\n"
            msg += f"• میانگین RSI ۴ ساعته بازار: <b>{avg_rsi_4h}</b>\n"
            msg += "• وضعیت سیگنال: <i>هیچ ارزی تمام شرایط ورود معتبر (امتیاز بالای ۷) را احراز نکرد.</i>\n\n"
            
            for item in market_summary:
                price_str = fp(item['price'])
                msg += f"{item['icon']} <b>{item['sym']}</b>: {price_str}$ | RSI: {item['rsi4h']}\n"
                
            msg += "\n🔍 اسکن بعدی سر ۴ ساعت انجام می‌شود."
        else:
            msg = f"⚠️ <b>خطا در دریافت داده‌ها ({now})</b>\n\nاتصال به سرورهای بازار برقرار نشد."

        send(msg)

    print("پایان اسکن.")
                     
