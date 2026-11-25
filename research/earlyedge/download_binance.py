"""Download paginated Binance Spot klines for reproducible research datasets.

Uses only Python's standard library. Output is raw OHLCV; no synthetic signals
are inserted. The downloader excludes candles that have not closed yet.
"""
import argparse, csv, json, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = "https://data-api.binance.vision/api/v3/klines"
INTERVAL_MS = {"5m":300_000,"15m":900_000,"1h":3_600_000,"4h":14_400_000,"1d":86_400_000}
FIELDS = ["open_time","open","high","low","close","volume","close_time",
          "quote_volume","trades","taker_buy_base","taker_buy_quote","ignore"]

def fetch_page(symbol, interval, start_ms, end_ms, opener=urlopen, pause=0.15):
    query=urlencode({"symbol":symbol.upper(),"interval":interval,"startTime":start_ms,
                     "endTime":end_ms,"limit":1000})
    req=Request(BASE+"?"+query,headers={"User-Agent":"EarlyEdge-Research/0.1"})
    with opener(req,timeout=20) as response:
        data=json.loads(response.read().decode("utf-8"))
    if not isinstance(data,list):
        raise ValueError("Unexpected Binance klines response")
    if pause: time.sleep(pause)
    return data

def download_klines(symbol, interval, start_ms, end_ms, opener=urlopen, pause=0.15):
    if interval not in INTERVAL_MS: raise ValueError("unsupported interval")
    if start_ms < 0 or end_ms <= start_ms: raise ValueError("invalid time range")
    step=INTERVAL_MS[interval]
    cursor=start_ms
    rows=[]
    while cursor <= end_ms:
        page=fetch_page(symbol,interval,cursor,end_ms,opener=opener,pause=pause)
        if not page: break
        previous=cursor-1
        for item in page:
            if len(item)<12: raise ValueError("malformed kline row")
            ot,ct=int(item[0]),int(item[6])
            if ot < cursor or ot <= previous: raise ValueError("non-monotonic/overlapping Binance data")
            previous=ot
            if ct < end_ms: rows.append(item[:12])
        next_cursor=int(page[-1][0])+step
        if next_cursor <= cursor: raise ValueError("pagination did not advance")
        cursor=next_cursor
        if len(page)<1000: break
    return rows

def save_dataset(symbol, interval, days, out_dir="data/earlyedge"):
    end_ms=int(datetime.now(timezone.utc).timestamp()*1000)
    start_ms=int((datetime.now(timezone.utc)-timedelta(days=days)).timestamp()*1000)
    rows=download_klines(symbol,interval,start_ms,end_ms)
    folder=Path(out_dir); folder.mkdir(parents=True,exist_ok=True)
    stem=f"{symbol.upper()}_{interval}_{days}d"
    csv_path=folder/(stem+".csv")
    with csv_path.open("w",newline="",encoding="utf-8") as f:
        writer=csv.writer(f); writer.writerow(FIELDS); writer.writerows(rows)
    meta={"symbol":symbol.upper(),"interval":interval,"requested_days":days,
          "start_utc":datetime.fromtimestamp(start_ms/1000,timezone.utc).isoformat(),
          "end_utc":datetime.fromtimestamp(end_ms/1000,timezone.utc).isoformat(),
          "rows":len(rows),"first_open_time":rows[0][0] if rows else None,
          "last_close_time":rows[-1][6] if rows else None,
          "source":"Binance Spot /api/v3/klines","signals_generated":False,
          "forming_candle_included":False}
    (folder/(stem+".json")).write_text(json.dumps(meta,indent=2),encoding="utf-8")
    return meta

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--symbol",required=True);p.add_argument("--interval",choices=INTERVAL_MS,default="5m")
    p.add_argument("--days",type=int,default=180);p.add_argument("--out-dir",default="data/earlyedge")
    a=p.parse_args()
    if a.days<1 or a.days>365: p.error("--days must be between 1 and 365")
    print(json.dumps(save_dataset(a.symbol,a.interval,a.days,a.out_dir),indent=2))
if __name__=="__main__": main()
