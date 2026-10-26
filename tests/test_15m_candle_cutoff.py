"""QA-only tests for strict 15-minute candle cutoffs; no strategy/runtime changes."""
import unittest

import pandas as pd

from historical_backtest import frame_at


class FifteenMinuteCandleCutoffTests(unittest.TestCase):
    def test_only_fully_closed_15m_candles_before_decision_are_visible(self):
        # Decision at 10:10 UTC. A candle closing exactly at 10:10 is excluded.
        decision_ms = 10 * 60 * 60 * 1000 + 10 * 60 * 1000
        candles = pd.DataFrame({
            "open_time": [
                9 * 60 * 60 * 1000 + 30 * 60 * 1000,
                9 * 60 * 60 * 1000 + 45 * 60 * 1000,
                10 * 60 * 60 * 1000,
                10 * 60 * 60 * 1000 + 15 * 60 * 1000,
            ],
            "close_time": [
                9 * 60 * 60 * 1000 + 45 * 60 * 1000 - 1,
                10 * 60 * 60 * 1000 - 1,
                decision_ms,
                decision_ms + 15 * 60 * 1000 - 1,
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
