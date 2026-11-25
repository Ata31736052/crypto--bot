import tempfile, unittest
from pathlib import Path
from research.earlyedge.build_engine_csv import build_signals, write_outputs
from research.earlyedge.strategy import ENGINES

def bars(n,step,base=100,trend=.02):
    result=[]
    for i in range(n):
        c=base+i*trend;t=i*step
        result.append({"open_time":t,"close_time":t+step-1,"open":c-.01,
                       "high":c+.1,"low":c-.1,"close":c,"volume":100.0})
    return result

class BuildSignalTests(unittest.TestCase):
    def test_builds_one_complete_series_per_engine(self):
        out=build_signals(bars(240,900000),bars(100,3600000))
        self.assertEqual(set(out),set(ENGINES))
        self.assertTrue(all(len(rows)==240 for rows in out.values()))
        self.assertTrue(all(row["signal"] in {"NONE","WATCH","ENTRY"}
                            for rows in out.values() for row in rows))

    def test_writes_four_separate_files(self):
        with tempfile.TemporaryDirectory() as d:
            write_outputs({name:[{"timestamp":"1","open":1,"high":1,"low":1,
                "close":1,"signal":"NONE","stop_pct":1.5,"target_pct":2.7}]
                for name in ENGINES},d)
            self.assertEqual(len(list(Path(d).glob("*.csv"))),4)

if __name__=="__main__":unittest.main()
