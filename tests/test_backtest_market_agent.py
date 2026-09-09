import datetime as dt
import unittest
from unittest.mock import patch

from backtest_market_agent import build_signal_rows, run_backtest


def _rows(count=23, start=dt.date(2026, 6, 1)):
    rows = []
    codes = [
        "883957.TI", "700050.TI", "700047.TI", "700035.TI", "700034.TI",
        "700038.TI", "700039.TI",
    ]
    for index in range(count):
        date = (start + dt.timedelta(days=index)).isoformat()
        for code in codes:
            base = 100 + index
            if code in {"700050.TI", "700035.TI", "700038.TI"}:
                base += 1
            rows.append({"instrument_code": code, "trade_date": date, "pre_close": base, "close": base + 1})
    return rows


class BacktestTests(unittest.TestCase):
    def test_signal_rows_use_next_date_and_skip_warmup(self):
        samples = build_signal_rows(_rows())
        self.assertEqual(len(samples), 2)
        self.assertEqual(samples[0]["date"], "2026-06-21")
        self.assertEqual(samples[0]["next_date"], "2026-06-22")

    def test_backtest_has_conservative_policy_and_reference(self):
        result = run_backtest(_rows())
        self.assertEqual(result["recommended_policy"], "conservative_long_flat")
        self.assertIn("buy_and_hold_reference", result["policies"])

    def test_boundary_transition_is_not_a_sample(self):
        samples = build_signal_rows(_rows(start=dt.date(2026, 4, 1), count=60))
        self.assertTrue(all(sample["segment_id"] in {"A", "B"} for sample in samples))
        self.assertTrue(all(sample["date"] != "2026-05-25" for sample in samples))
        self.assertTrue(all(sample["next_date"] != "2026-05-25" for sample in samples))


    def test_full_lookback_restarts_after_boundary(self):
        samples = build_signal_rows(_rows(start=dt.date(2026, 4, 1), count=90))
        segment_b = [sample for sample in samples if sample["segment_id"] == "B"]
        self.assertTrue(segment_b)
        # 合成数据每天一条；5 月 26 日之后积累 20 个完整间隔才能产生信号。
        self.assertEqual(segment_b[0]["date"], "2026-06-15")
        self.assertTrue(all(sample["date"] >= "2026-06-15" for sample in segment_b))

    def test_flat_return_is_not_a_bearish_hit(self):
        samples = [{
            "date": "2026-06-21", "next_date": "2026-06-22", "segment_id": "B",
            "direction": "偏弱", "risk_mode": "Risk-Off", "next_return": 0.0,
        }]
        with patch("backtest_market_agent.build_signal_rows", return_value=samples):
            result = run_backtest([])
        self.assertEqual(result["signal_accuracy"]["偏弱"]["hit_rate"], 0)

    def test_equity_is_never_concatenated_across_segments(self):
        samples = [
            {"date": "2026-05-21", "segment_id": "A", "direction": "偏强",
             "risk_mode": "Risk-On", "next_return": -0.1},
            {"date": "2026-06-21", "segment_id": "B", "direction": "偏强",
             "risk_mode": "Risk-On", "next_return": -0.1},
        ]
        with patch("backtest_market_agent.build_signal_rows", return_value=samples):
            result = run_backtest([])
        metrics = result["policies"]["current_long_flat"]
        self.assertIsNone(metrics["all"]["total_return"])
        self.assertIsNone(metrics["all"]["max_drawdown"])
        self.assertEqual(metrics["all"]["observations"], 2)
        for segment in ("A", "B"):
            self.assertAlmostEqual(metrics["segments"][segment]["total_return"], -0.1)
            self.assertAlmostEqual(metrics["segments"][segment]["max_drawdown"], -0.1)


if __name__ == "__main__":
    unittest.main()
