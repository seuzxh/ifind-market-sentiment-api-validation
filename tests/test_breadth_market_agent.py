import datetime as dt
import unittest
from unittest.mock import patch

from breadth_market_agent import run_experiment, run_exploration, _event_values, _breadth_index
from market_agent import PRIMARY_CODE


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


class BreadthExplorationTests(unittest.TestCase):
    def event_rows(self, part, rows):
        history = [{"instrument_code": PRIMARY_CODE, "trade_date": s["date"]} for s in part]
        return _event_values(part, history, _breadth_index(payload(rows))[0])

    def test_future_breadth_does_not_change_earlier_events(self):
        part = samples(6)
        rows = [(s["date"], i+1, 0, 9-i) for i, s in enumerate(part)]
        before = self.event_rows(part, rows)
        rows[-1] = (part[-1]["date"], 0, 0, 10)
        after = self.event_rows(part, rows)
        self.assertEqual(before[:-1], after[:-1])
        self.assertTrue(before[3]["events"]["rising_3"])

    def test_missing_previous_breadth_cannot_skip_to_older_date(self):
        part = samples(4)
        rows = [(part[0]["date"], 1, 0, 9), (part[2]["date"], 8, 0, 2),
                (part[3]["date"], 9, 0, 1)]
        events = self.event_rows(part, rows)
        self.assertIsNone(events[2]["events"]["rising_1"])
        self.assertIsNone(events[2]["events"]["rebound_from_washout"])
        self.assertIsNone(events[3]["events"]["rising_3"])
        self.assertTrue(events[3]["events"]["rising_1"])

    def test_previous_segment_is_not_a_valid_prior_day(self):
        part = samples(1, dt.date(2026, 5, 22)) + samples(1, dt.date(2026, 5, 26), "B")
        rows = [(part[0]["date"], 1, 0, 9), (part[1]["date"], 8, 0, 2)]
        events = self.event_rows(part, rows)
        self.assertIsNone(events[1]["events"]["rising_1"])
        self.assertIsNone(events[1]["events"]["rebound_from_washout"])

    def test_prior_day_uses_raw_history_even_if_signal_sample_missing(self):
        part = samples(3)
        history = [{"instrument_code": PRIMARY_CODE, "trade_date": s["date"]} for s in part]
        breadth = _breadth_index(payload([(part[0]["date"], 1, 0, 9),
                                         (part[2]["date"], 8, 0, 2)]))[0]
        events = _event_values([part[0], part[2]], history, breadth)
        self.assertIsNone(events[1]["events"]["rising_1"])

    def test_fixed_thresholds_and_divergence_use_current_information(self):
        part = samples(4)
        part[1]["features"] = {"market_return": -0.01}
        part[2]["features"] = {"market_return": 0.01}
        events = self.event_rows(part, [(part[0]["date"], 2, 0, 8),
                                       (part[1]["date"], 6, 0, 4),
                                       (part[2]["date"], 4, 0, 6),
                                       (part[3]["date"], 8, 0, 2)])
        self.assertTrue(events[0]["events"]["washout"])
        self.assertTrue(events[1]["events"]["positive_divergence"])
        self.assertTrue(events[1]["events"]["rebound_from_washout"])
        self.assertTrue(events[2]["events"]["negative_divergence"])
        self.assertTrue(events[3]["events"]["broad_rally"])

    def test_test_labels_do_not_change_candidate_and_baseline_matches(self):
        part = samples(100)
        history = [{"instrument_code": PRIMARY_CODE, "trade_date": s["date"]} for s in part]
        rows = payload([(s["date"], 9, 0, 1) for s in part])
        with patch("breadth_market_agent.build_signal_rows", return_value=part):
            before = run_exploration(history, rows)
        for sample in part[70:]:
            sample["next_return"] = -0.9
        with patch("breadth_market_agent.build_signal_rows", return_value=part):
            after = run_exploration(history, rows)
        self.assertEqual(before["segments"]["A"]["selected"], "broad_rally")
        self.assertEqual(after["segments"]["A"]["selected"], "broad_rally")
        for method in after["segments"]["A"]["methods"].values():
            for view in method.values():
                for phase in view.values():
                    self.assertEqual(phase["eligible_dates"], phase["baseline"]["eligible_dates"])
                    self.assertEqual(phase["eligible_count"], phase["baseline"]["event_count"])
        self.assertFalse(after["deployment_allowed"])
        self.assertEqual(after["comparison_count"], 14)
        self.assertIsNone(after["segments"]["B"]["selected"])


if __name__ == "__main__":
    unittest.main()
