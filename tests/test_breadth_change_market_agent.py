import datetime as dt
import unittest
from unittest.mock import patch

from breadth_change_market_agent import (
    METHODS,
    _breadth_index,
    _event_values,
    run_experiment,
)
from market_agent import PRIMARY_CODE


def payload(rows):
    return {
        "errorcode": 0,
        "tables": [{"table": {
            "p00112_f001": [row[0] for row in rows],
            "p00112_f002": [row[1] for row in rows],
            "p00112_f003": [row[2] for row in rows],
            "p00112_f004": [row[3] for row in rows],
        }}],
    }


def samples(count=6, start=dt.date(2026, 1, 1), segment="A"):
    return [{
        "date": (start + dt.timedelta(days=index)).isoformat(),
        "next_date": (start + dt.timedelta(days=index + 1)).isoformat(),
        "segment_id": segment,
        "direction": "偏强",
        "risk_mode": "Risk-On",
        "next_return": 0.01 if index % 2 == 0 else -0.01,
    } for index in range(count)]


class BreadthChangeTests(unittest.TestCase):
    def test_fixed_change_definitions(self):
        part = samples()
        history = [{"instrument_code": PRIMARY_CODE, "trade_date": row["date"]} for row in part]
        # U: 0.1, 0.2, 0.3, 0.2, 0.1, 0.2；净广度同向变化。
        rows = [(part[0]["date"], 1, 0, 9),
                (part[1]["date"], 2, 0, 8),
                (part[2]["date"], 3, 0, 7),
                (part[3]["date"], 2, 0, 8),
                (part[4]["date"], 1, 0, 9),
                (part[5]["date"], 2, 0, 8)]
        events = _event_values(part, history, _breadth_index(payload(rows))[0])
        self.assertIsNone(events[0]["events"]["up_ratio_change_1d"])
        self.assertTrue(events[1]["events"]["up_ratio_change_1d"])
        self.assertFalse(events[3]["events"]["up_ratio_change_1d"])
        self.assertTrue(events[3]["events"]["up_ratio_change_3d"])
        self.assertTrue(events[3]["events"]["up_ratio_diffusion_3d"] is False)
        self.assertTrue(events[3]["events"]["up_ratio_contraction_3d"] is False)
        self.assertEqual(set(events[3]["events"]), set(METHODS))

    def test_missing_previous_date_does_not_fill_or_skip(self):
        part = samples(4)
        history = [{"instrument_code": PRIMARY_CODE, "trade_date": row["date"]} for row in part]
        rows = [(part[0]["date"], 1, 0, 9), (part[2]["date"], 3, 0, 7),
                (part[3]["date"], 4, 0, 6)]
        events = _event_values(part, history, _breadth_index(payload(rows))[0])
        self.assertIsNone(events[1]["events"]["up_ratio_change_1d"])
        self.assertIsNone(events[2]["events"]["up_ratio_change_1d"])
        self.assertIsNone(events[2]["events"]["up_ratio_change_3d"])
        self.assertTrue(events[3]["events"]["up_ratio_change_1d"])

    def test_boundary_date_cannot_supply_prior_change(self):
        part = samples(1, dt.date(2026, 5, 22), "A") + samples(1, dt.date(2026, 5, 26), "B")
        history = [{"instrument_code": PRIMARY_CODE, "trade_date": row["date"]} for row in part]
        rows = [(part[0]["date"], 1, 0, 9), (part[1]["date"], 8, 0, 2)]
        events = _event_values(part, history, _breadth_index(payload(rows))[0])
        self.assertIsNone(events[1]["events"]["up_ratio_change_1d"])
        self.assertIsNone(events[1]["events"]["net_breadth_change_1d"])

    def test_future_breadth_does_not_change_previous_events(self):
        part = samples()
        history = [{"instrument_code": PRIMARY_CODE, "trade_date": row["date"]} for row in part]
        rows = [(row["date"], index + 1, 0, 10 - index) for index, row in enumerate(part)]
        before = _event_values(part, history, _breadth_index(payload(rows))[0])
        rows[-1] = (part[-1]["date"], 0, 0, 10)
        after = _event_values(part, history, _breadth_index(payload(rows))[0])
        self.assertEqual(before[:-1], after[:-1])

    def test_split_rule_and_deployment_flag(self):
        part = samples(100)
        rows = payload([(row["date"], 6, 1, 3) for row in part])
        with patch("breadth_change_market_agent.build_signal_rows", return_value=part):
            result = run_experiment([], rows)
        self.assertFalse(result["deployment_allowed"])
        self.assertEqual(result["segments"]["A"]["split_index"], 70)
        self.assertEqual(result["segments"]["A"]["test_start"], part[70]["date"])
        self.assertEqual(result["method_order"], list(METHODS))


if __name__ == "__main__":
    unittest.main()
