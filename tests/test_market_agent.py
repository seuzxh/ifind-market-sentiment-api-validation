import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from market_agent import (
    parse_calendar_response,
    parse_history_response,
    segment_for_date,
    validate_same_segment,
)
from market_agent import (
    IfindClient,
    fetch_breadth,
    fetch_calendar,
    fetch_history,
    fetch_realtime,
    classify_market,
    compute_features,
    load_token_from_values,
    normalize_breadth,
    normalize_realtime,
    redact_text,
)


class ContractTests(unittest.TestCase):
    def test_history_arrays_are_joined_by_date(self):
        payload = {
            "errorcode": 0,
            "tables": [{
                "thscode": "883957.TI",
                "time": ["2026-05-22"],
                "table": {
                    "pre_close": [100.0],
                    "close": [101.0],
                    "chg": [1.0],
                    "pct_chg": [1.0],
                },
            }],
        }
        row = parse_history_response(payload)[0]
        self.assertEqual(row["trade_date"], "2026-05-22")
        self.assertEqual(row["instrument_code"], "883957.TI")

    def test_history_preserves_unknown_fields_and_empty_table(self):
        payload = {
            "errorcode": 0,
            "tables": [
                {"thscode": "A.TI", "time": [], "table": {"vendor_field": []}},
                {"thscode": "A.TI", "time": ["2026-05-22"], "table": {"vendor_field": [7]}},
            ],
        }
        self.assertEqual(parse_history_response(payload)[0]["vendor_field"], 7)

    def test_history_array_length_mismatch_fails(self):
        payload = {
            "errorcode": 0,
            "tables": [{"time": ["2026-05-22"], "table": {"close": []}}],
        }
        with self.assertRaises(ValueError):
            parse_history_response(payload)

    def test_nonzero_errorcode_fails(self):
        with self.assertRaises(ValueError):
            parse_history_response({"errorcode": -4224, "errmsg": "date index is invalid"})

    def test_calendar_reads_time_array(self):
        payload = {"errorcode": 0, "tables": {"time": ["2026-09-01", "2026-09-02"]}}
        self.assertEqual(parse_calendar_response(payload), ["2026-09-01", "2026-09-02"])

    def test_calendar_normalizes_datetime_values(self):
        payload = {"errorcode": 0, "tables": {"dates": ["2026-09-01 00:00:00"]}}
        self.assertEqual(parse_calendar_response(payload), ["2026-09-01"])

    def test_calendar_error_fails(self):
        with self.assertRaises(ValueError):
            parse_calendar_response({"errorcode": -1, "errmsg": "bad request"})

    def test_segment_boundary_is_exclusive(self):
        self.assertEqual(segment_for_date(dt.date(2026, 5, 22)), "A")
        self.assertIsNone(segment_for_date(dt.date(2026, 5, 25)))
        self.assertEqual(segment_for_date(dt.date(2026, 5, 26)), "B")

    def test_cross_segment_window_fails(self):
        with self.assertRaises(ValueError):
            validate_same_segment(["2026-05-22", "2026-05-26"])

    def test_boundary_window_fails(self):
        with self.assertRaises(ValueError):
            validate_same_segment(["2026-05-25"])

    def test_empty_window_is_allowed(self):
        validate_same_segment([])


class SecurityTests(unittest.TestCase):
    def test_redact_removes_token(self):
        self.assertNotIn("secret-token", redact_text("x secret-token y", "secret-token"))

    def test_empty_token_fails_before_request(self):
        with self.assertRaises(RuntimeError):
            load_token_from_values("", "")

    def test_client_posts_fixed_https_request_and_redacts_evidence(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def getcode(self):
                return 200

            def read(self):
                return json.dumps({"errorcode": 0, "tables": []}).encode()

        class FakeOpener:
            def __init__(self):
                self.request = None

            def open(self, request, timeout):
                self.request = request
                self.timeout = timeout
                return FakeResponse()

        opener = FakeOpener()
        with tempfile.TemporaryDirectory() as directory, patch(
            "market_agent.urllib.request.build_opener", return_value=opener
        ):
            client = IfindClient("secret-token", Path(directory))
            self.assertEqual(client.post("history_data", {"reqBody": {"codes": "A.TI", "debug": "secret-token"}})["errorcode"], 0)
            self.assertEqual(opener.request.method, "POST")
            self.assertEqual(opener.request.full_url, "https://quantapi.51ifind.com/api/v1/history_data")
            self.assertEqual(opener.timeout, 25)
            self.assertEqual(opener.request.get_header("Access_token"), "secret-token")
            saved = "".join(path.read_text(encoding="utf-8") for path in Path(directory).rglob("*" ) if path.is_file())
            self.assertNotIn("secret-token", saved)

    def test_unknown_endpoint_is_rejected(self):
        with patch("market_agent.urllib.request.build_opener"):
            client = IfindClient("secret-token")
        with self.assertRaises(ValueError):
            client.post("http://example.com", {})

    def test_business_error_does_not_echo_token(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def getcode(self):
                return 200

            def read(self):
                return json.dumps({"errorcode": -1, "errmsg": "secret-token rejected"}).encode()

        class FakeOpener:
            def open(self, request, timeout):
                return FakeResponse()

        with patch("market_agent.urllib.request.build_opener", return_value=FakeOpener()):
            client = IfindClient("secret-token")
            with self.assertRaises(ValueError) as raised:
                client.post("history_data", {})
        self.assertNotIn("secret-token", str(raised.exception))

    def test_non_200_status_fails_even_with_json_body(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def getcode(self):
                return 201

            def read(self):
                return b'{"errorcode": 0}'

        class FakeOpener:
            def open(self, request, timeout):
                return FakeResponse()

        with patch("market_agent.urllib.request.build_opener", return_value=FakeOpener()):
            client = IfindClient("secret-token")
            with self.assertRaises(RuntimeError):
                client.post("history_data", {})


class NormalizationTests(unittest.TestCase):
    def test_breadth_preserves_null_and_percent_units(self):
        payload = {
            "errorcode": 0,
            "tables": [{"table": {
                "p00112_f001": ["2026/09/08"],
                "p00112_f002": [3257],
                "p00112_f003": [91],
                "p00112_f004": [1859],
            }}],
        }
        row = normalize_breadth(payload)[0]
        self.assertEqual(row["up"], 3257)
        self.assertEqual(row["flat"], 91)
        self.assertAlmostEqual(row["up_ratio"], 3257 / (3257 + 91 + 1859))
        self.assertEqual(row["scope"], "A_candidate_SH_SZ")

    def test_breadth_missing_values_are_not_zero(self):
        payload = {"errorcode": 0, "tables": [{"table": {
            "p00112_f001": ["2026/09/08"], "p00112_f002": [1], "p00112_f003": [None], "p00112_f004": [2]
        }}]}
        row = normalize_breadth(payload)[0]
        self.assertIsNone(row["flat"])
        self.assertIsNone(row["net_breadth"])

    def test_realtime_keeps_null(self):
        payload = {"errorcode": 0, "tables": [{"table": {
            "riseCount": [2066], "fallCount": [3304], "suspensionCount": [None]
        }}]}
        row = normalize_realtime(payload)
        self.assertEqual(row["riseCount"], 2066)
        self.assertIsNone(row["suspensionCount"])


class FetchContractTests(unittest.TestCase):
    class FakeClient:
        def __init__(self, payloads):
            self.payloads = payloads
            self.calls = []

        def post(self, endpoint, body):
            self.calls.append((endpoint, body))
            return self.payloads[endpoint]

    def test_fetch_history_wraps_req_body_and_cps(self):
        fake = self.FakeClient({"history_data": {"errorcode": 0, "tables": []}})
        self.assertEqual(fetch_history(["A.TI", "B.TI"], "2026-09-01", "2026-09-08", "1", fake), [])
        endpoint, body = fake.calls[0]
        self.assertEqual(endpoint, "history_data")
        self.assertEqual(body["reqBody"]["codes"], "A.TI,B.TI")
        self.assertEqual(body["reqBody"]["functionpara"], {"CPS": "1"})

    def test_fetch_calendar_uses_confirmed_market_code(self):
        fake = self.FakeClient({"get_trade_dates": {"errorcode": 0, "tables": {"time": ["2026-09-08"]}}})
        self.assertEqual(fetch_calendar("2026-09-01", "2026-09-08", fake), ["2026-09-08"])
        endpoint, body = fake.calls[0]
        self.assertEqual(endpoint, "get_trade_dates")
        self.assertEqual(body["marketcode"], "212001")

    def test_fetch_breadth_uses_candidate_scope(self):
        fake = self.FakeClient({"data_pool": {"errorcode": 0, "tables": []}})
        self.assertEqual(fetch_breadth("2026-09-01", "2026-09-08", fake), [])
        endpoint, body = fake.calls[0]
        self.assertEqual(endpoint, "data_pool")
        self.assertEqual(body["functionpara"]["p0"], "A股")

    def test_fetch_realtime_maps_primary_index(self):
        fake = self.FakeClient({"real_time_quotation": {"errorcode": 0, "tables": [{"table": {"latest": [101]}}]}})
        row = fetch_realtime("883957.TI", fake)
        self.assertEqual(row["instrument_code"], "883957.TI")
        self.assertEqual(row["latest"], 101)


class RuleTests(unittest.TestCase):
    def test_bullish_rule_needs_two_style_spreads(self):
        features = {
            "market_return": 0.01,
            "ret5": 0.02,
            "ret20": 0.03,
            "size_spread": 0.01,
            "risk_spread": 0.02,
            "mom_spread": -0.01,
            "net_breadth": 0.20,
            "up_ratio": 0.60,
            "segment_id": "B",
        }
        result = classify_market(features)
        self.assertEqual(result["direction"], "偏强")
        self.assertEqual(result["risk_mode"], "Risk-On")
        self.assertEqual(result["drawdown_control"], "谨慎偏多")
        self.assertGreaterEqual(len(result["reasons"]), 2)

    def test_missing_features_are_not_silent_zero(self):
        result = classify_market({"segment_id": "B"})
        self.assertEqual(result["direction"], "证据不足")
        self.assertEqual(result["risk_mode"], "中性")

    def test_risk_off_blocks_bullish_exposure(self):
        result = classify_market({
            "market_return": 0.01, "ret5": 0.02, "ret20": 0.03,
            "size_spread": -0.01, "risk_spread": -0.02, "mom_spread": 0.00,
        })
        self.assertEqual(result["direction"], "偏强")
        self.assertEqual(result["risk_mode"], "Risk-Off")
        self.assertEqual(result["drawdown_control"], "观望")

    def test_cross_segment_features_are_rejected(self):
        with self.assertRaises(ValueError):
            compute_features([
                {"instrument_code": "883957.TI", "trade_date": "2026-05-22", "close": 100, "pre_close": 99},
                {"instrument_code": "883957.TI", "trade_date": "2026-05-26", "close": 101, "pre_close": 100},
            ], [])


class CliTests(unittest.TestCase):
    def test_missing_token_exits_before_request_and_does_not_echo_secret(self):
        env = os.environ.copy()
        env.pop("IFIND_ACCESS_TOKEN", None)
        completed = subprocess.run(
            [sys.executable, "market_agent.py", "status"],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertNotIn("secret-token", completed.stdout)

    def test_boundary_date_returns_explicit_json_state_without_token(self):
        env = os.environ.copy()
        env["IFIND_ACCESS_TOKEN"] = "test-only-token"
        completed = subprocess.run(
            [sys.executable, "market_agent.py", "status", "--date", "2026-05-25", "--json", "--no-save"],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(completed.returncode, 1)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["as_of_date"], "2026-05-25")
        self.assertEqual(payload["quality_status"], "边界日")
        self.assertNotIn("test-only-token", completed.stdout)


if __name__ == "__main__":
    unittest.main()
