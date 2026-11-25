"""Experimental short-horizon Binance Spot scanner.

Research-branch candidate only. Sends confirmed BUY ENTRY alerts only; never
places orders. Iran quiet-window check precedes all network I/O.
"""
from __future__ import annotations
import json, logging, os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import requests
from research.earlyedge.iran_schedule import iran_now, scan_window_status
from research.earlyedge.strategy import evaluate_engines

BINANCE="https://data-api.binance.vision"
MIN_QUOTE_VOLUME=5_000_000.0
MAX_SYMBOLS=100
WORKERS=8
TIMEOUT=15
STATE_PATH=Path(os.getenv("EARLYEDGE_STATE_FILE","earlyedge_state.json"))
log=logging.getLogger("earlyedge-live-candidate")

def _get(session,path,params=None):
    response=session.get(BINANCE+path,params=params or {},timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()

def _bars(raw,now_ms,interval_ms):
    out=[]
    for k in raw:
        close_time=int(k[6])
        if close_time>=now_ms: continue
        out.append({"open_time":int(k[0]),"close_time":close_time,
          "open":float(k[1]),"high":float(k[2]),"low":float(k[3]),
          "close":float(k[4]),"volume":float(k[5])})
    return out

def _decision_indices(bars15,now):
    local=iran_now(now)
    start=local.replace(hour=7,minute=0,second=0,microsecond=0)
    start_ms=int(start.timestamp()*1000)
    eligible=[i for i,b in enumerate(bars15) if int(b["close_time"])+1>=start_ms]
    return eligible[-2:]

def _load_state(path):
    try:
        value=json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError): return {}

def _save_state(path,state):
    target=Path(path);target.parent.mkdir(parents=True,exist_ok=True)
    temporary=target.with_suffix(target.suffix+".tmp")
    temporary.write_text(json.dumps(state,sort_keys=True,indent=2)+"\n",encoding="utf-8")
    temporary.replace(target)

def _nearest_resistance(bars,entry):
    pivots=[]
    for i in range(2,len(bars)-2):
        high=float(bars[i]["high"])
        if high>entry and high>=max(float(x["high"]) for x in bars[i-2:i]) and high>max(float(x["high"]) for x in bars[i+1:i+3]):
            pivots.append(high)
    return min(pivots) if pivots else None

def _format_alert(symbol,signal,bars15):
    entry=float(signal["price"]);risk=float(signal["stop_pct"])
    sl=entry*(1-risk/100);tp1=entry*(1+risk*1.8/100);tp2=entry*(1+risk*2.4/100)
    resistance=_nearest_resistance(bars15[-80:],entry)
    headroom=f"{(resistance/entry-1)*100:.2f}%" if resistance else "نامشخص"
    return (f"🟢 <b>ورود تأییدشده (ENTRY)</b>\n"
      f"<b>{symbol}</b> | شکار کف با حجم قوی\n"
      f"قیمت مرجع: {entry:.8g}\nحد ضرر: {sl:.8g} ({risk:.2f}%)\n"
      f"هدف ۱: {tp1:.8g} | RR=1.8\nهدف ۲: {tp2:.8g} | RR=2.4\n"
      f"فاصله تا مقاومت تأییدشده: {headroom}\n"
      "فقط هشدار خرید اسپات؛ بدون معامله‌گری خودکار.")

def run_scan(now=None,session=None,telegram_token=None,telegram_chat_id=None,state_path=None,send=True):
    status=scan_window_status(now)
    if not status["allowed"]:
        log.info("EARLYEDGE_SCAN_SKIPPED %s",status)
        return {"status":"skipped_outside_iran_window","signals":0,"api_calls":0}
    session=session or requests.Session()
    token=os.getenv("TELEGRAM_TOKEN","") if telegram_token is None else telegram_token
    chat_id=os.getenv("TELEGRAM_CHAT_ID","") if telegram_chat_id is None else telegram_chat_id
    state_file=state_path or STATE_PATH
    now_dt=iran_now(now);now_ms=int(now_dt.timestamp()*1000);state=_load_state(state_file)
    exchange=_get(session,"/api/v3/exchangeInfo")
    tradable={x["symbol"] for x in exchange.get("symbols",[])
      if x.get("status")=="TRADING" and x.get("quoteAsset")=="USDT"
      and x.get("isSpotTradingAllowed") is not False and x.get("symbol")!="BTCUSDT"
      and not any(t in x.get("symbol","") for t in ("UPUSDT","DOWNUSDT","BULLUSDT","BEARUSDT"))}
    tickers=_get(session,"/api/v3/ticker/24hr");ranked=[]
    for item in tickers if isinstance(tickers,list) else []:
        symbol=item.get("symbol")
        if symbol not in tradable: continue
        try: volume=float(item.get("quoteVolume",0))
        except (TypeError,ValueError): continue
        if volume>=MIN_QUOTE_VOLUME: ranked.append((volume,symbol))
    symbols=[s for _,s in sorted(ranked,reverse=True)[:MAX_SYMBOLS]]
    btc_raw=_get(session,"/api/v3/klines",{"symbol":"BTCUSDT","interval":"1h","limit":100})
    btc=_bars(btc_raw,now_ms,3_600_000);btc_label="نامشخص"
    if len(btc)>=50:
        closes=[x["close"] for x in btc];fast=sum(closes[-21:])/21;slow=sum(closes[-50:])/50
        btc_label="صعودی" if closes[-1]>fast>slow else "نزولی" if closes[-1]<fast<slow else "خنثی"
    def scan_symbol(symbol):
        raw15=_get(session,"/api/v3/klines",{"symbol":symbol,"interval":"15m","limit":250})
        raw1h=_get(session,"/api/v3/klines",{"symbol":symbol,"interval":"1h","limit":100})
        b15=_bars(raw15,now_ms,900_000);b1h=_bars(raw1h,now_ms,3_600_000)
        if len(b15)<35 or len(b1h)<50: return symbol,b15,[]
        found=[]
        for i in _decision_indices(b15,now_dt):
            if i>=29: found.extend(evaluate_engines(b15,b1h,i))
        return symbol,b15,found
    found=[];api_errors=0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures={pool.submit(scan_symbol,s):s for s in symbols}
        for future in as_completed(futures):
            try:
                symbol,bars15,signals=future.result()
                for signal in signals:
                    # Defensive release gate: ENTRY only, and only enabled engine.
                    if signal.get("state")!="ENTRY" or signal.get("engine")!="BOTTOM_REVERSAL": continue
                    key=f"{symbol}:{signal['engine']}:{signal['timestamp']}"
                    if key not in state: found.append((key,symbol,signal,bars15))
            except Exception as exc:
                api_errors+=1;log.warning("SYMBOL_SCAN_FAILED %s: %s",futures[future],exc)
    sent=0
    for key,symbol,signal,bars15 in sorted(found,key=lambda x:(x[1],x[2]["timestamp"])):
        message=f"BTC context: {btc_label}\n"+_format_alert(symbol,signal,bars15)
        if send:
            if not token or not chat_id:
                log.warning("Telegram credentials missing; not sending candidate alert");continue
            response=session.post(f"https://api.telegram.org/bot{token}/sendMessage",
              json={"chat_id":chat_id,"text":message,"parse_mode":"HTML","disable_web_page_preview":True},
              timeout=TIMEOUT)
            response.raise_for_status()
            if response.json().get("ok") is not True: raise RuntimeError("Telegram rejected candidate alert")
            sent+=1
        state[key]={"sent_at":now_ms,"symbol":symbol,"engine":signal["engine"],
                    "state":"ENTRY","candle_close":signal["timestamp"]}
    if send and sent: _save_state(state_file,state)
    return {"status":"completed","symbols":len(symbols),"signals_found":len(found),
            "signals_sent":sent,"api_errors":api_errors,"btc_context":btc_label}

if __name__=="__main__":
    logging.basicConfig(level=os.getenv("LOG_LEVEL","INFO"))
    print(json.dumps(run_scan(),ensure_ascii=False,sort_keys=True))
