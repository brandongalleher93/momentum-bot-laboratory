import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from bot.http_limits import bound_market_client


class HttpLimitTests(unittest.TestCase):
    def test_market_requests_have_finite_timeouts_and_retries(self):
        request = Mock(return_value="response")
        client = SimpleNamespace(_session=SimpleNamespace(request=request))
        bound_market_client(client)
        self.assertEqual(client._session.request("GET", "https://example.invalid"), "response")
        request.assert_called_once_with("GET", "https://example.invalid", timeout=(10, 30))
        self.assertEqual(client._retry, 2)
        self.assertEqual(client._retry_wait, 3)

    def test_sdk_contract_drift_cannot_silently_remove_timeout(self):
        with self.assertRaises(RuntimeError):
            bound_market_client(SimpleNamespace())
