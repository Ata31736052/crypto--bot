"""QA-only tests for strict 15-minute candle cutoffs; no strategy/runtime changes."""
import unittest

import pandas as pd

from historical_backtest import frame_at


class FifteenMinuteCandleCutoffTests(unittest.TestCase):
    def test_only_fully_closed_15m_candles_before_decision_are_visible(self):
        # Decision at 10:10 UTC. The candle ending exactly at 10:10 is not
        # eligible under the repository's strict close_time < decision rule.
        decision_ms = 10 * 60 * 60 * 1000 + 10 * 60 * 1000
        candles = pd.DataFrame({
            "open_time": [
                9 * 60 * 60 * 1000 + 30 * 60 * 1000,  # 09:30
                9 * 60 * 60 * 1000 + 45 * 60 * 1000,  # 09:45
                10 * 60 * 60 * 1000,                   # 10:00
                10 * 60 * 60 * 1000 + 15 * 60 * 1000, # 10:15, future
            ],
            "close_time": [
                9 * 60 * 60 * 1000 + 45 * 60 * 1000 - 1,  # 09:44:59.999
                10 * 60 * 60 * 1000 - 1,                  # 09:59:59.999
                decision_ms,                               # exact cutoff
                decision_ms + 15 * 60 * 1000 - 1,         # future close
            ],
            "close": [101.0, 102.0, 103.0, 104.0],
        })

        visible = frame_at(candles, decision_ms)

        self.assertEqual(visible["close"].tolist(), [101.0, 102.0])
        self.assertTrue((visible["close_time"] < decision_ms).all())

    def test_cutoff_does_not_mutate_source_dataframe(self):
        decision_ms = 10 * 60 * 60 * 1000
        candles = pd.DataFrame({
            "close_time": [decision_ms - 1, decision_ms],
            "close": [10.0, 11.0],
        })
        original = candles.copy(deep=True)

        frame_at(candles, decision_ms)

        pd.testing.assert_frame_equal(candles, original)


if __name__ == "__main__":
    unittest.main()
