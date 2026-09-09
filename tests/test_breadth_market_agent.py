import datetime as dt
import unittest
from unittest.mock import patch

from breadth_market_agent import run_experiment


def payload(rows):
    return {"errorcode": 0, "tables": [{"table": {
        "p00112_f001": [r[0] for r in rows], "p00112_f002": [r[1] for r in rows],
        "p00112_f003": [r[2] for r in rows], "p00112_f004": [r[3] for r in rows],
    }}]}


def samples(count=3, start=dt.date(2026, 1, 1), segment="A"):
    return [{"date": (start + dt.timedelta(days=i)).isoformat(),
             "next_date": (start + dt.timedelta(days=i+1)).isoformat(),
             "segment_id": segment, "direction": "偏强", "risk_mode": "Risk-On",
             "next_return": -0.1 if i % 2 else 0.1} for i in range(count)]


def experiment(part, rows):
    with patch("breadth_market_agent.build_signal_rows", return_value=part):
        return run_experiment([], payload(rows))


class BreadthTests(unittest.TestCase):
    def test_exact_dates_only_without_forward_or_backward_fill(self):
        result = experiment(samples(), [("2026-01-02", 6, 1, 3), ("2026-01-04", 8, 1, 1)])
        part = result["segments"]["A"]["all"]
        self.assertEqual(part["matched_dates"], ["2026-01-02"])
        self.assertEqual(part["missing_dates"], ["2026-01-01", "2026-01-03"])
        self.assertIn("2026-01-04", result["unmatched_breadth_dates"])

    def test_flat_is_in_denominator_and_half_does_not_confirm(self):
        result = experiment(samples(), [("2026-01-01", 5, 4, 1),
                                         ("2026-01-02", 6, 3, 1), ("2026-01-03", 4, 4, 2)])
        part = result["segments"]["A"]["all"]
        self.assertEqual(part["confirmed_dates"], ["2026-01-02"])
        self.assertEqual(part["metrics"]["original"]["active_observations"], 3)
        self.assertEqual(part["metrics"]["breadth_confirm"]["active_observations"], 1)
        self.assertEqual(part["metrics"]["breadth_confirm"]["hit_rate"], 0)

    def test_missing_dates_do_not_move_holdout(self):
        part = samples(100)
        full = [(s["date"], 6, 1, 3) for s in part]
        complete = experiment(part, full)["segments"]["A"]
        sparse = experiment(part, full[75:])["segments"]["A"]
        self.assertEqual(sparse["test_start"], part[70]["date"])
        self.assertEqual(sparse["test_start"], complete["test_start"])
        self.assertEqual(sparse["test"]["sample_count"], 30)
        self.assertEqual(sparse["test"]["matched_count"], 25)

    def test_invalid_counts_duplicates_and_boundary_are_rejected(self):
        values = [-1, float("nan"), float("inf"), 1.5, None]
        part = samples(9)
        rows = [(part[i]["date"], value, 1, 2) for i, value in enumerate(values)]
        rows += [(part[5]["date"], 0, 0, 0), (part[6]["date"], 6, 1, 3),
                 (part[6]["date"], 6, 1, 3), ("2026-05-25", 6, 1, 3)]
        result = experiment(part, rows)
        self.assertEqual(result["valid_breadth_count"], 0)
        self.assertEqual(len(result["rejected_breadth"]), len(rows))
        self.assertFalse(result["deployment_allowed"])
        self.assertEqual(result["scope"], "A_candidate_SH_SZ")

    def test_segments_never_share_split_or_equity(self):
        a = samples(100)
        b = samples(45, dt.date(2026, 6, 1), "B")
        result = experiment(a+b, [(s["date"], 6, 1, 3) for s in a+b])
        self.assertNotIn("all", result)
        self.assertEqual(result["segments"]["B"]["test_start"], b[40]["date"])
        self.assertEqual(result["segments"]["B"]["development"]["sample_count"], 0)
        for key, part in (("A", a), ("B", b)):
            separate = experiment(part, [(s["date"], 6, 1, 3) for s in part])
            self.assertEqual(result["segments"][key], separate["segments"][key])


if __name__ == "__main__":
    unittest.main()
