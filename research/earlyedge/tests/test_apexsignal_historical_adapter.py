import unittest
from unittest.mock import patch

import pandas as pd

import main
from research.earlyedge.apexsignal_historical_adapter import (
    analyze_current_main_from_history, closed_frame,
)


def frame(n=215, step=1):
    return pd.DataFrame({
        "close_time": [i * step for i in range(n)],
        "open": [100.0] * n, "high": [101.0] * n,
        "low": [99.0] * n, "close": [100.0] * n,
        "volume": [10.0] * n,
    })


class ApexHistoricalAdapterTests(unittest.TestCase):
    def test_closed_frame_excludes_decision_candle_and_caps_history(self):
        source = frame(300)
        out = closed_frame(source, decision_ms=280, limit=250)
        self.assertEqual(len(out), 250)
        self.assertLess(int(out["close_time"].max()), 280)
        self.assertEqual(int(out["close_time"].iloc[-1]), 279)

    def test_adapter_injects_closed_frames_and_restores_live_fetcher(self):
        symbol_frames = {"1d": frame(), "4h": frame(), "1h": frame()}
        btc_frames = {"1d": frame(), "4h": frame()}
        original_klines = main.klines
        observed = {}

        def fake_daily_regime(df):
            return "BULLISH"

        def fake_analyze(symbol, btc_d, btc_h):
            observed["btc_d"] = btc_d
            observed["btc_h"] = btc_h
            observed["frames"] = {
                interval: main.klines(symbol, interval, 250)
                for interval in ("1d", "4h", "1h")
            }
            return "result", {"ok": True}, None

        with patch.object(main, "add_indicators", side_effect=lambda x: x), \
             patch.object(main, "daily_regime", side_effect=fake_daily_regime), \
             patch.object(main, "analyze_symbol", side_effect=fake_analyze):
            result = analyze_current_main_from_history(
                "ETHUSDT", symbol_frames, btc_frames, decision_ms=214
            )

        self.assertEqual(result[0], "result")
        self.assertEqual(observed["btc_d"], "BULLISH")
        self.assertEqual(observed["btc_h"], "BULLISH")
        for df in observed["frames"].values():
            self.assertEqual(len(df), 214)
            self.assertLess(int(df["close_time"].max()), 214)
        self.assertIs(main.klines, original_klines)

    def test_adapter_restores_fetcher_after_strategy_exception(self):
        original_klines = main.klines
        frames = {"1d": frame(), "4h": frame(), "1h": frame()}
        btc = {"1d": frame(), "4h": frame()}
        with patch.object(main, "add_indicators", side_effect=lambda x: x), \
             patch.object(main, "daily_regime", return_value="BULLISH"), \
             patch.object(main, "analyze_symbol", side_effect=RuntimeError("test failure")):
            with self.assertRaises(RuntimeError):
                analyze_current_main_from_history("ETHUSDT", frames, btc, 214)
        self.assertIs(main.klines, original_klines)


if __name__ == "__main__":
    unittest.main()
