import unittest
from unittest.mock import patch

import requests
import main


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None, json_error=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise self._json_error
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class SignalUniverseTests(unittest.TestCase):
    def test_btc_is_excluded_but_other_spot_usdt_pairs_remain(self):
        payload = {"symbols": [
            {"symbol": "BTCUSDT", "status": "TRADING", "quoteAsset": "USDT", "isSpotTradingAllowed": True},
            {"symbol": "ETHUSDT", "status": "TRADING", "quoteAsset": "USDT", "isSpotTradingAllowed": True},
            {"symbol": "BNBUSDT", "status": "BREAK", "quoteAsset": "USDT", "isSpotTradingAllowed": True},
            {"symbol": "SOLBTC", "status": "TRADING", "quoteAsset": "BTC", "isSpotTradingAllowed": True},
            {"symbol": "ETHUPUSDT", "status": "TRADING", "quoteAsset": "USDT", "isSpotTradingAllowed": True},
        ]}
        with patch.object(main, "get_json", return_value=payload):
            self.assertEqual(main.exchange_symbols(), ["ETHUSDT"])

    def test_btc_remains_the_macro_regime_source(self):
        daily = object()
        four_hour = object()
        with patch.object(main, "klines", side_effect=[daily, four_hour]) as klines, \
             patch.object(main, "add_indicators", side_effect=lambda frame: frame), \
             patch.object(main, "daily_regime", side_effect=["DAILY_STATE", "4H_STATE"]):
            self.assertEqual(main.btc_regime(), ("DAILY_STATE", "4H_STATE"))
        self.assertEqual(klines.call_args_list, [
            unittest.mock.call("BTCUSDT", "1d", 250),
            unittest.mock.call("BTCUSDT", "4h", 250),
        ])


class TelegramDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.token = patch.object(main, "TELEGRAM_TOKEN", "test-token")
        self.chat = patch.object(main, "TELEGRAM_CHAT_ID", "test-chat")
        self.token.start()
        self.chat.start()
        self.addCleanup(self.token.stop)
        self.addCleanup(self.chat.stop)

    def test_429_retry_after_then_success(self):
        responses = [
            FakeResponse(429, {"ok": False, "parameters": {"retry_after": 2}}),
            FakeResponse(200, {"ok": True, "result": {"message_id": 1}}),
        ]
        with patch.object(main.session, "post", side_effect=responses) as post, \
             patch.object(main.time, "sleep") as sleep:
            self.assertTrue(main.send_telegram("mock message"))
        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once_with(2.0)

    def test_429_uses_retry_after_header_when_body_has_no_value(self):
        responses = [
            FakeResponse(429, {"ok": False}, {"Retry-After": "3"}),
            FakeResponse(200, {"ok": True}),
        ]
        with patch.object(main.session, "post", side_effect=responses), \
             patch.object(main.time, "sleep") as sleep:
            self.assertTrue(main.send_telegram("mock message"))
        sleep.assert_called_once_with(3.0)

    def test_exhausted_429_stops_after_bounded_attempts(self):
        response = FakeResponse(429, {"ok": False, "parameters": {"retry_after": 0}})
        with patch.object(main.session, "post", return_value=response) as post, \
             patch.object(main.time, "sleep"):
            with self.assertRaises(requests.HTTPError):
                main.send_telegram("mock message")
        self.assertEqual(post.call_count, main.TELEGRAM_MAX_ATTEMPTS)

    def test_http_errors_are_not_retried(self):
        for status in (400, 500, 503):
            with self.subTest(status=status):
                response = FakeResponse(status, {"ok": False})
                with patch.object(main.session, "post", return_value=response) as post:
                    with self.assertRaises(requests.HTTPError):
                        main.send_telegram("mock message")
                post.assert_called_once()

    def test_network_timeout_is_not_retried(self):
        with patch.object(main.session, "post", side_effect=requests.Timeout("ambiguous")) as post:
            with self.assertRaises(requests.Timeout):
                main.send_telegram("mock message")
        post.assert_called_once()

    def test_api_ok_false_returns_failure_without_retry(self):
        response = FakeResponse(200, {"ok": False, "description": "chat not found"})
        with patch.object(main.session, "post", return_value=response) as post:
            self.assertFalse(main.send_telegram("mock message"))
        post.assert_called_once()

    def test_invalid_api_json_is_not_retried(self):
        response = FakeResponse(200, json_error=ValueError("bad json"))
        with patch.object(main.session, "post", return_value=response) as post:
            with self.assertRaisesRegex(RuntimeError, "invalid JSON"):
                main.send_telegram("mock message")
        post.assert_called_once()


if __name__ == "__main__":
    unittest.main()
