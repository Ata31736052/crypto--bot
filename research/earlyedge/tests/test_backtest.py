import unittest
from research.earlyedge.backtest import replay

def row(t,o,h,l,c,s="NONE",stop="2",target="4"):
    return {"timestamp":t,"open":str(o),"high":str(h),"low":str(l),"close":str(c),
            "signal":s,"stop_pct":stop,"target_pct":target}

class ReplayTests(unittest.TestCase):
    def test_entry_is_next_bar_open_and_target_exit(self):
        rows=[row("01",100,101,99,100,"ENTRY"),row("02",100,105,99,104),
              row("03",104,105,103,104)]
        r=replay(rows,fee_pct=0,slippage_pct=0)
        self.assertEqual(r["trade_count"],1)
        t=r["trades"][0]
        self.assertEqual(t["entry_time"],"02")
        self.assertEqual(t["reason"],"TARGET")
        self.assertAlmostEqual(t["entry"],100)

    def test_stop_wins_same_bar_collision(self):
        rows=[row("01",100,101,99,100,"ENTRY"),row("02",100,105,95,102)]
        r=replay(rows,fee_pct=0,slippage_pct=0)
        self.assertEqual(r["trades"][0]["reason"],"STOP")

    def test_watch_is_not_a_trade(self):
        rows=[row("01",100,101,99,100,"WATCH"),row("02",100,101,99,100)]
        self.assertEqual(replay(rows)["trade_count"],0)

    def test_fees_and_slippage_reduce_profit(self):
        rows=[row("01",100,101,99,100,"ENTRY"),row("02",100,105,99,104)]
        free=replay(rows,fee_pct=0,slippage_pct=0)
        costly=replay(rows,fee_pct=.1,slippage_pct=.05)
        self.assertGreater(free["net_pnl"],costly["net_pnl"])
        self.assertGreater(costly["fees_total"],0)

if __name__=="__main__": unittest.main()
