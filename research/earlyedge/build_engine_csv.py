"""Convert Binance kline CSVs into one causal signal CSV per EarlyEdge engine."""
import argparse, csv
from pathlib import Path
from research.earlyedge.strategy import ENGINES, evaluate_engines

def read_bars(path):
    with open(path,newline="",encoding="utf-8") as f:
        rows=list(csv.DictReader(f))
    required={"open_time","close_time","open","high","low","close","volume"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError(f"{path}: missing Binance kline columns")
    out=[]
    last=-1
    for r in rows:
        item={k:r[k] for k in required}
        item["open_time"]=int(item["open_time"]);item["close_time"]=int(item["close_time"])
        if item["open_time"]<=last: raise ValueError("15m/1h candles must be strictly ordered")
        last=item["open_time"]
        for k in ("open","high","low","close","volume"):
            item[k]=float(item[k])
        if min(item["open"],item["high"],item["low"],item["close"])<=0 or item["volume"]<0:
            raise ValueError("invalid OHLCV values")
        if item["high"]<max(item["open"],item["low"],item["close"]) or item["low"]>min(item["open"],item["close"],item["high"]):
            raise ValueError("invalid OHLC range")
        out.append(item)
    return out

def build_signals(bars15,bars1h):
    output={engine:[] for engine in ENGINES}
    for i,bar in enumerate(bars15):
        by_engine={s["engine"]:s for s in evaluate_engines(bars15,bars1h,i)}
        for engine in ENGINES:
            s=by_engine.get(engine)
            output[engine].append({
                "timestamp":str(bar["close_time"]),
                "open":bar["open"],"high":bar["high"],"low":bar["low"],"close":bar["close"],
                "signal":s["state"] if s else "NONE",
                "stop_pct":s["stop_pct"] if s else 1.5,
                "target_pct":s["target_pct"] if s else 2.7
            })
    return output

def write_outputs(signals,out_dir):
    folder=Path(out_dir);folder.mkdir(parents=True,exist_ok=True)
    fields=["timestamp","open","high","low","close","signal","stop_pct","target_pct"]
    for engine,rows in signals.items():
        path=folder/(engine.lower()+".csv")
        with path.open("w",newline="",encoding="utf-8") as f:
            writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(rows)

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--15m",dest="bars15",required=True)
    p.add_argument("--1h",dest="bars1h",required=True)
    p.add_argument("--out-dir",default="data/earlyedge/signals")
    a=p.parse_args()
    signals=build_signals(read_bars(a.bars15),read_bars(a.bars1h))
    write_outputs(signals,a.out_dir)
    print("Generated separate research signal CSVs for: "+", ".join(signals))
if __name__=="__main__":main()
