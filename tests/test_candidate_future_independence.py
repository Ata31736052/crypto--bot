import unittest
from unittest.mock import patch

import pandas as pd

import historical_backtest as hb


class CandidateFutureIndependenceTests(unittest.TestCase):
    def make_raw_frames(self, future_open):
        history = pd.DataFrame({
            "open_time": [i * 1_000 for i in range(211)],
            "close_time": [i * 1_000 + 999 for i in range(211)],
            "open": [100.0] * 211,
            "high": [102.0] * 211,
            "low": [98.0] * 211,
            "close": [101.0] * 211,
            "volume": [10.0] * 211,
        })
        future = pd.DataFrame([{
            "open_time": 301_000, "close_time": 301_999,
            "open": future_open, "high": future_open * 1.02,
            "low": future_open * 0.98, "close": future_open * 1.01,
            "volume": 999_999.0,
        }])
        frame = pd.concat([history, future], ignore_index=True)
        return {"1d": frame.copy(), "4h": frame.copy(), "1h": frame.copy()}

    def test_future_bar_mutation_changes_only_execution_metadata(self):
        decision_ms = 300_000
        first_frames = self.make_raw_frames(110.0)
        changed_frames = self.make_raw_frames(999.0)
        seen = []

        def fake_analyze(symbol, btc_d, btc_h, historical_frames, enable_resistance_gate):
            seen.append({key: frame.copy(deep=True)
                         for key, frame in historical_frames.items()})
            return ({
                "symbol": symbol,
                "setup": {"strategy": "TREND", "score": 90},
                "h": historical_frames["1h"].copy(deep=True),
            }, None, None)

        with patch.object(hb.main, "analyze_symbol", side_effect=fake_analyze):
            first = hb.candidate_at(
                "AAAUSDT", first_frames, decision_ms, "neutral", "neutral", False
            )
            changed = hb.candidate_at(
                "AAAUSDT", changed_frames, decision_ms, "neutral", "neutral", False
            )

        self.assertIsNotNone(first)
        self.assertIsNotNone(changed)
        self.assertEqual(first["symbol"], changed["symbol"])
        self.assertEqual(first["setup"], changed["setup"])
        pd.testing.assert_frame_equal(first["h"], changed["h"])
        for captured in seen:
            for frame in captured.values():
                self.assertTrue((frame["close_time"] < decision_ms).all())
        self.assertEqual(first["entry_open_raw"], 110.0)
        self.assertEqual(changed["entry_open_raw"], 999.0)
        self.assertEqual(first["entry_time"], changed["entry_time"])
        self.assertEqual(first["decision_time"], changed["decision_time"])


if __name__ == "__main__":
    unittest.main()
