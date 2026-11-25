import unittest

from research.earlyedge.strategy import evaluate_engines


def falling_hourly():
    bars = []
    for i in range(60):
        close = 200.0 - 2.0 * i
        bars.append({
            "open_time": i * 3_600_000,
            "close_time": (i + 1) * 3_600_000 - 1,
            "open": close + 0.3, "high": close + 0.5,
            "low": close - 0.5, "close": close, "volume": 100.0,
        })
    return bars


def falling_then_reclaim(volume=200.0):
    bars = []
    base_time = 100 * 3_600_000
    for i in range(39):
        close = 100.0 - i * 0.5
        bars.append({
            "open_time": base_time + i * 900_000,
            "close_time": base_time + (i + 1) * 900_000 - 1,
            "open": close + 0.1, "high": close + 0.2,
            "low": close - 0.2, "close": close, "volume": 100.0,
        })
    prev_close = bars[-1]["close"]
    bars.append({
        "open_time": base_time + 39 * 900_000,
        "close_time": base_time + 40 * 900_000 - 1,
        "open": prev_close - 0.1, "high": prev_close + 0.7,
        "low": prev_close - 0.3, "close": prev_close + 0.5,
        "volume": volume,
    })
    return bars


def rising_breakout():
    bars = []
    base_time = 100 * 3_600_000
    for i in range(39):
        close = 80.0 + i * 0.2
        bars.append({
            "open_time": base_time + i * 900_000,
            "close_time": base_time + (i + 1) * 900_000 - 1,
            "open": close - 0.05, "high": close + 0.1,
            "low": close - 0.2, "close": close, "volume": 100.0,
        })
    prev_close = bars[-1]["close"]
    bars.append({
        "open_time": base_time + 39 * 900_000,
        "close_time": base_time + 40 * 900_000 - 1,
        "open": prev_close - 0.1, "high": prev_close + 0.8,
        "low": prev_close - 0.2, "close": prev_close + 0.7,
        "volume": 250.0,
    })
    return bars


class BearMarketOpportunityTests(unittest.TestCase):
    def test_bottom_reversal_can_enter_during_bearish_hourly_context(self):
        signals = evaluate_engines(falling_then_reclaim(200.0), falling_hourly(), 39)
        bottom = [s for s in signals if s["engine"] == "BOTTOM_REVERSAL"]
        self.assertTrue(bottom, signals)
        self.assertEqual(bottom[0]["state"], "ENTRY")

    def test_bearish_bottom_reversal_requires_stronger_volume(self):
        signals = evaluate_engines(falling_then_reclaim(120.0), falling_hourly(), 39)
        bottom = [s for s in signals if s["engine"] == "BOTTOM_REVERSAL"]
        self.assertTrue(bottom, signals)
        self.assertEqual(bottom[0]["state"], "WATCH")

    def test_countertrend_breakout_can_enter_with_strong_volume(self):
        signals = evaluate_engines(rising_breakout(), falling_hourly(), 39)
        breakout = [s for s in signals if s["engine"] == "BREAKOUT"]
        self.assertTrue(breakout, signals)
        self.assertEqual(breakout[0]["state"], "ENTRY")


if __name__ == "__main__":
    unittest.main()
