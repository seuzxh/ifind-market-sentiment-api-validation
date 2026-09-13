import datetime as dt
import unittest
from unittest.mock import patch

from market_agent import STYLE_PAIRS
from style_persistence_market_agent import (
    METHODS,
    _candidate_flags,
    _enrich_samples,
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
        "features": {},
    } for index in range(count)]


class StylePersistenceTests(unittest.TestCase):
    def test_fixed_candidate_definitions(self):
        flags = _candidate_flags({
            "SIZE": [False, True, True, False, True],
            "RISK": [False, False, True, True, True],
            "MOM": [True, False, False, False, True],
        })
        self.assertEqual(set(flags), set(METHODS))
        self.assertTrue(flags["any2_positive_3d"])
        self.assertFalse(flags["risk_mom_positive_3d"])
        self.assertTrue(flags["any2_positive_5d"])
        self.assertTrue(flags["all_positive_today"])
        self.assertTrue(flags["any2_negative_to_positive"])

    def test_insufficient_window_is_unavailable(self):
        flags = _candidate_flags({group: [True] for group in ("SIZE", "RISK", "MOM")})
        self.assertIsNone(flags["any2_positive_3d"])
        self.assertIsNone(flags["risk_mom_positive_3d"])
        self.assertIsNone(flags["any2_positive_5d"])
        self.assertTrue(flags["all_positive_today"])
        self.assertIsNone(flags["any2_negative_to_positive"])

    def test_boundary_does_not_reuse_prior_segment(self):
        part = [{
            **samples(1, dt.date(2026, 5, 26), "B")[0],
            "date": "2026-05-26",
        }]
        rows = []
        for date in ("2026-05-21", "2026-05-22", "2026-05-26"):
            rows.append({"instrument_code": "883957.TI", "trade_date": date})
            for positive, negative in STYLE_PAIRS.values():
                rows.extend([
                    {"instrument_code": positive, "trade_date": date,
                     "pre_close": 100, "close": 101},
                    {"instrument_code": negative, "trade_date": date,
                     "pre_close": 100, "close": 100},
                ])
        flags = _enrich_samples(part, rows)[0]["candidate_flags"]
        self.assertIsNone(flags["any2_positive_3d"])
        self.assertTrue(flags["all_positive_today"])

    def test_split_and_deployment_are_fixed(self):
        part = samples()
        enriched = [{**row, "candidate_flags": {name: True for name in METHODS}}
                    for row in part]
        with patch("style_persistence_market_agent.build_signal_rows", return_value=part), \
             patch("style_persistence_market_agent._enrich_samples", return_value=enriched):
            result = run_experiment([])
        self.assertFalse(result["deployment_allowed"])
        self.assertEqual(result["segments"]["A"]["split_index"], 70)
        self.assertEqual(result["segments"]["A"]["test_start"], part[70]["date"])
        self.assertEqual(result["method_order"], list(METHODS))
        metrics = result["segments"]["A"]["test"]["metrics"]
        self.assertIn("current_long_flat", metrics)
        self.assertIn("conservative_long_flat", metrics)
        self.assertIn("average_active_return", metrics["any2_positive_3d"])


if __name__ == "__main__":
    unittest.main()
