import unittest
import numpy as np
import pandas as pd
from earlyedge_strategy import ENGINE_NAMES, build_risk_levels, evaluate, indicators

def candles(n=240, trend=0.0005):
    close = 100 * np.exp(np.arange(n) * trend)
    return pd.DataFrame({'open': close*.999, 'high': close*1.006, 'low': close*.994, 'close': close, 'volume': np.full(n,1000.0)})

class EarlyEdgeTests(unittest.TestCase):
    def test_indicator_columns(self):
        out=indicators(candles())
        for key in ('ema9','ema21','ema50','ema200','rsi','macd_hist','atr','vol_ratio'):
            self.assertIn(key,out.columns)
    def test_short_history_fails_closed(self):
        result=evaluate(candles(100),candles(100))
        self.assertEqual(result['status'],'INSUFFICIENT_HISTORY')
        self.assertEqual(result['candidates'],[])
    def test_valid_history_returns_known_engines_only(self):
        result=evaluate(candles(),candles())
        self.assertEqual(result['status'],'OK')
        self.assertTrue(all(c['engine'] in ENGINE_NAMES for c in result['candidates']))
        self.assertTrue(all(c['entry'] or c['early_warning'] for c in result['candidates']))
    def test_risk_levels_enforce_spot_long_geometry(self):
        levels=build_risk_levels(100.0,2.0)
        self.assertIsNotNone(levels)
        self.assertLess(levels['sl'],levels['entry'])
        self.assertGreater(levels['tp1'],levels['entry'])
        self.assertGreater(levels['tp2'],levels['tp1'])
        self.assertEqual(levels['rr1'],1.8)
        self.assertEqual(levels['rr2'],2.4)
    def test_invalid_risk_rejected(self):
        self.assertIsNone(build_risk_levels(0,1))
        self.assertIsNone(build_risk_levels(100,20))

if __name__=='__main__':
    unittest.main()
