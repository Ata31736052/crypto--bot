import unittest
from research.earlyedge.strategy import evaluate_engines, _ema, _rsi

def bars(n,step=900000,base=100,trend=.05,volume=100):
    out=[]
    for i in range(n):
        c=base+i*trend
        t=i*step
        out.append({"open_time":t,"close_time":t+step-1,"open":c-.02,
                    "high":c+.15,"low":c-.15,"close":c,"volume":volume})
    return out

class IndicatorTests(unittest.TestCase):
    def test_ema_requires_warmup(self):
        self.assertIsNone(_ema([1,2,3],5))
        self.assertAlmostEqual(_ema([1,2,3,4,5],3),4)

    def test_rsi_is_bounded(self):
        value=_rsi([float(i) for i in range(20)])
        self.assertEqual(value,100)

class EngineTests(unittest.TestCase):
    def test_insufficient_history_returns_no_signal(self):
        self.assertEqual(evaluate_engines(bars(20),bars(60,3600000),19),[])

    def test_higher_timeframe_forming_candle_is_excluded(self):
        b15=bars(45)
        b1h=bars(60,3600000)
        decision=b15[-1]["close_time"]
        b1h[-1]["close_time"]=decision+1
        out=evaluate_engines(b15,b1h,44)
        # The incomplete latest 1h bar cannot be used to satisfy context warmup.
        self.assertEqual(out,[])

    def test_future_15m_rows_do_not_change_past_decision(self):
        b15=bars(45)
        b1h=bars(80,3600000)
        before=evaluate_engines(b15,b1h,40)
        extended=b15+bars(5,base=500,trend=10)
        after=evaluate_engines(extended,b1h,40)
        self.assertEqual(before,after)

if __name__=="__main__": unittest.main()
