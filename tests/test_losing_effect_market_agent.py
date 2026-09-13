import unittest

from losing_effect_market_agent import build_loss_effect_samples
from market_agent import classify_loss_effect


def sample(date, segment="A", market_return=0.01):
    return {
        "date": date,
        "next_date": date,
        "segment_id": segment,
        "next_return": 0.01,
        "features": {"market_return": market_return},
    }


class LosingEffectTests(unittest.TestCase):
    def test_fixed_intensity_boundaries(self):
        self.assertEqual(classify_loss_effect(None), "数据不足")
        self.assertEqual(classify_loss_effect(float("nan")), "数据不足")
        self.assertEqual(classify_loss_effect(float("inf")), "数据不足")
        self.assertEqual(classify_loss_effect(float("-inf")), "数据不足")
        self.assertEqual(classify_loss_effect(0.50), "不明显")
        self.assertEqual(classify_loss_effect(0.5001), "偏强")
        self.assertEqual(classify_loss_effect(0.5999), "偏强")
        self.assertEqual(classify_loss_effect(0.60), "强")

    def test_missing_breadth_stays_unavailable(self):
        row = sample("2026-05-20")
        result = build_loss_effect_samples(
            [row], [{"instrument_code": "883957.TI", "trade_date": row["date"]}], {}
        )[0]
        self.assertIsNone(result["down_ratio"])
        self.assertEqual(result["loss_effect"], "数据不足")
        self.assertTrue(all(value is None for value in result["loss_effect_states"].values()))

    def test_consecutive_down_dominance_and_divergence(self):
        dates = ["2026-05-20", "2026-05-21", "2026-05-22"]
        samples = [sample(date) for date in dates]
        history = [{"instrument_code": "883957.TI", "trade_date": date} for date in dates]
        breadth = {
            date: {"down_ratio": ratio, "net_breadth": 1 - 2 * ratio}
            for date, ratio in zip(dates, (0.51, 0.55, 0.61))
        }
        result = build_loss_effect_samples(samples, history, breadth)
        self.assertTrue(result[1]["loss_effect_states"]["down_dominant_2d"])
        self.assertTrue(result[2]["loss_effect_states"]["down_dominant_3d"])
        self.assertTrue(result[2]["loss_effect_states"]["index_up_but_down_dominant"])
        self.assertEqual(result[2]["loss_effect"], "强")

    def test_consecutive_window_does_not_cross_boundary(self):
        dates = ["2026-05-22", "2026-05-26"]
        samples = [sample(dates[0], "A"), sample(dates[1], "B")]
        history = [{"instrument_code": "883957.TI", "trade_date": date} for date in dates]
        breadth = {
            date: {"down_ratio": 0.70, "net_breadth": -0.40} for date in dates
        }
        result = build_loss_effect_samples(samples, history, breadth)
        self.assertIsNone(result[1]["loss_effect_states"]["down_dominant_2d"])
        self.assertIsNone(result[1]["loss_effect_states"]["down_dominant_3d"])

    def test_future_value_does_not_change_past_state(self):
        dates = ["2026-05-20", "2026-05-21"]
        samples = [sample(date) for date in dates]
        history = [{"instrument_code": "883957.TI", "trade_date": date} for date in dates]
        before = build_loss_effect_samples(samples, history, {
            dates[0]: {"down_ratio": 0.55, "net_breadth": -0.10},
            dates[1]: {"down_ratio": 0.55, "net_breadth": -0.10},
        })
        after = build_loss_effect_samples(samples, history, {
            dates[0]: {"down_ratio": 0.55, "net_breadth": -0.10},
            dates[1]: {"down_ratio": 0.20, "net_breadth": 0.60},
        })
        self.assertEqual(before[0], after[0])


if __name__ == "__main__":
    unittest.main()
