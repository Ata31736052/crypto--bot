import unittest
from research.earlyedge.strategy import evaluate_engines

def falling_hourly():
    bars=[]
    for i in range(60):
        close=200.0-2.0*i
        bars.append({"open_time":i*3_600_000,"close_time":(i+1)*3_600_000-1,
          "open":close+.3,"high":close+.5,"low":close-.5,"close":close,"volume":100.0})
    return bars

def falling_then_reclaim(volume=200.0):
    bars=[];base=100*3_600_000
    for i in range(39):
        close=100.0-i*.5;t=base+i*900_000
        bars.append({"open_time":t,"close_time":t+899999,"open":close+.1,
          "high":close+.2,"low":close-.2,"close":close,"volume":100.0})
    prev=bars[-1]["close"];t=base+39*900_000
    bars.append({"open_time":t,"close_time":t+899999,"open":prev-.1,
      "high":prev+.7,"low":prev-.3,"close":prev+.5,"volume":volume})
    return bars

class BearMarketOpportunityTests(unittest.TestCase):
    def test_confirmed_high_volume_bottom_reversal_can_enter_in_bear_market(self):
        signals=evaluate_engines(falling_then_reclaim(200.0),falling_hourly(),39)
        self.assertEqual(len(signals),1)
        self.assertEqual(signals[0]["engine"],"BOTTOM_REVERSAL")
        self.assertEqual(signals[0]["state"],"ENTRY")

    def test_low_volume_bottom_setup_is_removed_not_sent_as_watch(self):
        self.assertEqual(evaluate_engines(falling_then_reclaim(120.0),falling_hourly(),39),[])

    def test_only_bottom_reversal_engine_is_enabled(self):
        signals=evaluate_engines(falling_then_reclaim(200.0),falling_hourly(),39)
        self.assertTrue(all(s["engine"]=="BOTTOM_REVERSAL" for s in signals))

if __name__=="__main__": unittest.main()
