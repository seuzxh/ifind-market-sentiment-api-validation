import datetime as dt
import unittest

from market_agent import (
    parse_calendar_response,
    parse_history_response,
    segment_for_date,
    validate_same_segment,
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


if __name__ == "__main__":
    unittest.main()
