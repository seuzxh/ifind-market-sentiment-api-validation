import datetime as dt
import statistics
import unittest
from unittest.mock import patch

from market_agent import PRIMARY_CODE
from trend_vol_market_agent import (
    METHODS,
    _candidate_flags,
    _enrich_samples,
    _volatility_by_date,
    run_experiment,
)


def samples(count=100, start=dt.date(2025, 1, 1), segment="A"):
    return [{
        "date": (start + dt.timedelta(days=index)).isoformat(),
        "next_date": (start + dt.timedelta(days=index + 1)).isoformat(),
        "segment_id": segment,
        "direction": "偏强",
        "risk_mode": "Risk-On",
        "next_return": 0.01 if index % 2 == 0 else -0.01,
        "features": {"ret5": 0.03, "ret20": 0.08},
    } for index in range(count)]


def history(dates, returns):
    return [{
        "instrument_code": PRIMARY_CODE,
        "trade_date": date,
        "pre_close": 100.0,
        "close": 100.0 * (1.0 + value),
    } for date, value in zip(dates, returns)]


class TrendVolTests(unittest.TestCase):
    def test_fixed_candidate_definitions(self):
        sample = {
            "features": {"ret5": 0.03, "ret20": 0.08},
            "vol20": 0.02,
            "prior_vol20_median": 0.02,
        }
        flags = _candidate_flags(sample)
        self.assertEqual(set(flags), set(METHODS))
        self.assertTrue(flags["trend_strength"])
        self.assertTrue(flags["trend_acceleration"])
        self.assertTrue(flags["low_vol"])
        self.assertFalse(flags["high_vol"])
        self.assertTrue(flags["trend_low_vol"])
        self.assertEqual(sample["z5"], 1.5)
        self.assertEqual(sample["z20"], 4.0)

    def test_future_return_does_not_change_prior_features(self):
        dates = [(dt.date(2025, 1, 1) + dt.timedelta(days=index)).isoformat() for index in range(45)]
        part = samples(2)
        part[0]["date"], part[1]["date"] = dates[30], dates[31]
        rows = history(dates, [0.01 + (index % 3) * 0.001 for index in range(45)])
        before = _enrich_samples(part, rows)
        expected = statistics.pstdev([0.01 + (index % 3) * 0.001 for index in range(11, 31)])
        self.assertAlmostEqual(before[0]["vol20"], expected)
        rows[-1]["close"] = 150.0
        after = _enrich_samples(part, rows)
        self.assertEqual(before, after)

    def test_boundary_resets_prior_volatility_history(self):
        a_dates = [(dt.date(2026, 4, 20) + dt.timedelta(days=index)).isoformat() for index in range(33)]
        b_dates = [(dt.date(2026, 5, 26) + dt.timedelta(days=index)).isoformat() for index in range(25)]
        part = samples(2, dt.date(2026, 6, 16), "B")
        part[0]["date"], part[1]["date"] = b_dates[20], b_dates[21]
        rows = history(a_dates, [0.20 if index % 2 else -0.20 for index in range(33)])
        rows += history(b_dates, [0.01 if index % 2 else -0.01 for index in range(25)])
        enriched = _enrich_samples(part, rows)
        self.assertIsNotNone(enriched[0]["prior_vol20_median"])
        self.assertLess(enriched[0]["prior_vol20_median"], 0.02)
        self.assertLess(enriched[1]["prior_vol20_median"], 0.02)

    def test_missing_return_invalidates_its_twenty_day_window(self):
        dates = [(dt.date(2025, 1, 1) + dt.timedelta(days=index)).isoformat() for index in range(25)]
        rows = history(dates, [0.01 if index % 2 else -0.01 for index in range(25)])
        rows[10]["close"] = None
        volatility = _volatility_by_date(rows)
        self.assertNotIn(dates[19], volatility)
        self.assertNotIn(dates[24], volatility)

    def test_split_and_deployment_are_fixed(self):
        part = samples()
        enriched = [{**row, "vol20": 0.02, "prior_vol20_median": 0.02,
                     "z5": 1.5, "z20": 4.0,
                     "candidate_flags": {name: True for name in METHODS}}
                    for row in part]
        with patch("trend_vol_market_agent.build_signal_rows", return_value=part), \
             patch("trend_vol_market_agent._enrich_samples", return_value=enriched):
            result = run_experiment([])
        self.assertFalse(result["deployment_allowed"])
        self.assertEqual(result["segments"]["A"]["split_index"], 70)
        self.assertEqual(result["segments"]["A"]["test_start"], part[70]["date"])
        self.assertEqual(result["method_order"], list(METHODS))
        self.assertIn("current_long_flat", result["segments"]["A"]["test"]["metrics"])
        self.assertIn("baseline", result["segments"]["A"]["test"]["metrics"])


if __name__ == "__main__":
    unittest.main()
