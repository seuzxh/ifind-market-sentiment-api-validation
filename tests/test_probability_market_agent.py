import unittest
from unittest.mock import patch

from probability_market_agent import estimate_probability, _score, run_experiment


def sample(index, outcome=.01, segment="A"):
    return {"date": f"2025-{index // 28 + 1:02d}-{index % 28 + 1:02d}",
            "next_date": f"2025-{(index + 1) // 28 + 1:02d}-{(index + 1) % 28 + 1:02d}",
            "segment_id": segment, "next_return": outcome, "direction": "偏强",
            "risk_mode": "Risk-On", "features": {"market_return": .01}}


class ProbabilityTests(unittest.TestCase):
    def test_future_labels_and_other_segment_cannot_change_probability(self):
        history = [sample(i) for i in range(45)]
        target = sample(45)
        expected = estimate_probability(history, target, "direction")
        contaminated = history + [sample(46, -1), sample(2, -1, "B")]
        self.assertEqual(expected, estimate_probability(contaminated, target, "direction"))
        unresolved = sample(1, -1)
        unresolved["next_date"] = "2026-01-01"
        self.assertEqual(expected, estimate_probability(history + [unresolved], target, "direction"))
        self.assertIsNone(estimate_probability(history, sample(45, segment="B"), "direction"))

    def test_brier_and_coverage(self):
        records = [{**sample(1), "p": .75, "y": True}, {**sample(2, 0), "p": .25, "y": False}]
        result = _score(records)
        self.assertEqual(result["brier"], .0625)
        self.assertEqual(result["accuracy"], 1)
        self.assertEqual(result["high_probability_coverage"], .5)

    def test_test_labels_cannot_change_development_selection(self):
        samples = []
        for index in range(100):
            row = sample(index, .01 if index % 2 else -.01)
            row["direction"] = "偏强" if index % 2 else "偏弱"
            samples.append(row)
        changed = [dict(row) for row in samples]
        # 70% 分割点之后反转全部标签，开发期和方法选择必须保持不变。
        for row in changed[70:]:
            row["next_return"] *= -1
        with patch("probability_market_agent.build_signal_rows", return_value=samples):
            original = run_experiment([])["segments"]["A"]
        with patch("probability_market_agent.build_signal_rows", return_value=changed):
            perturbed = run_experiment([])["segments"]["A"]
        self.assertEqual(original["selected_on_development"], "direction")
        self.assertEqual(original["selected_on_development"], perturbed["selected_on_development"])
        self.assertEqual(original["development"], perturbed["development"])
        self.assertNotEqual(original["test"]["direction"]["brier"], perturbed["test"]["direction"]["brier"])

    def test_empty_experiment_is_not_a_pass(self):
        result = run_experiment([])
        self.assertFalse(result["segments"]["B"]["candidate_passed_screen"])


if __name__ == "__main__":
    unittest.main()
