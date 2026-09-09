import datetime as dt
import unittest

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


if __name__ == "__main__":
    unittest.main()
