import unittest
from research.earlyedge.portfolio_backtest import portfolio_replay

def row(t,o=100,h=101,l=99,c=100,s="NONE",stop="2",target="4"):
    return {"timestamp":str(t),"open":str(o),"high":str(h),"low":str(l),"close":str(c),
            "signal":s,"stop_pct":stop,"target_pct":target}

class PortfolioTests(unittest.TestCase):
    def test_shared_capital_prevents_overallocation(self):
        data={"AAA":[row(1,s="ENTRY"),row(2,o=100,h=101,l=99,c=100),row(3)],
              "BBB":[row(1,s="ENTRY"),row(2,o=100,h=101,l=99,c=100),row(3)]}
        r=portfolio_replay(data,starting_cash=1000,max_total_exposure=500,
                           max_position=1000,fee_pct=0,slippage_pct=0)
        self.assertLessEqual(r["max_deployed"],500.000001)
        self.assertLessEqual(r["max_open_positions"],2)

    def test_watch_is_not_a_trade(self):
        data={"AAA":[row(1,s="WATCH"),row(2),row(3)]}
        self.assertEqual(portfolio_replay(data)["trade_count"],0)

    def test_stop_wins_same_bar_collision(self):
        data={"AAA":[row(1,s="ENTRY"),row(2,h=105,l=95,c=100)]}
        r=portfolio_replay(data,fee_pct=0,slippage_pct=0)
        self.assertEqual(r["trades"][0]["reason"],"STOP")

    def test_trade_fills_on_next_available_bar(self):
        data={"AAA":[row(1,s="ENTRY"),row(2,o=101,h=102,l=100,c=101),row(3,h=108,l=100,c=107)]}
        r=portfolio_replay(data,fee_pct=0,slippage_pct=0)
        self.assertEqual(r["trades"][0]["entry_time"],"2")

if __name__=="__main__":unittest.main()
