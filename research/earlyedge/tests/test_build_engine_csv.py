import tempfile,unittest
from pathlib import Path
from research.earlyedge.build_engine_csv import build_signals,write_outputs
from research.earlyedge.strategy import ENGINES

def bars(n,step,base=100,trend=.02):
    result=[]
    for i in range(n):
        c=base+i*trend;t=i*step
        result.append({"open_time":t,"close_time":t+step-1,"open":c-.01,
          "high":c+.1,"low":c-.1,"close":c,"volume":100.0})
    return result

class BuildSignalTests(unittest.TestCase):
    def test_only_enabled_engine_series_is_built(self):
        out=build_signals(bars(240,900000),bars(100,3600000))
        self.assertEqual(set(out),{"BOTTOM_REVERSAL"})
        self.assertEqual(len(out["BOTTOM_REVERSAL"]),240)
        self.assertTrue(all(row["signal"] in {"NONE","ENTRY"} for row in out["BOTTOM_REVERSAL"]))

    def test_writes_only_enabled_engine_file(self):
        with tempfile.TemporaryDirectory() as d:
            write_outputs({name:[{"timestamp":"1","open":1,"high":1,"low":1,
              "close":1,"signal":"NONE","stop_pct":1.5,"target_pct":2.7}]
              for name in ENGINES},d)
            files=list(Path(d).glob("*.csv"))
            self.assertEqual([p.name for p in files],["bottom_reversal.csv"])

if __name__=="__main__":unittest.main()
