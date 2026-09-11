import unittest

from weight_market_agent import WEIGHT_VARIANTS, weighted_decision, run_weight_validation


def _features(size, risk, mom):
    return {"market_return": .01, "ret5": .02, "ret20": .03,
            "size_spread": size, "risk_spread": risk, "mom_spread": mom}


class WeightTests(unittest.TestCase):
    def test_equal_reproduces_original_risk_rule(self):
        features = _features(.1, -.1, .1)
        self.assertEqual(weighted_decision(features, WEIGHT_VARIANTS["equal"])["risk_mode"], "Risk-On")

    def test_risk_priority_filters_size_mom_pair(self):
        features = _features(.1, -.1, .1)
        self.assertEqual(weighted_decision(features, WEIGHT_VARIANTS["equal"])["risk_mode"], "Risk-On")
        self.assertEqual(weighted_decision(features, WEIGHT_VARIANTS["RISK_priority"])["risk_mode"], "中性")

    def test_weights_do_not_change_direction(self):
        features = _features(-.1, -.1, .1)
        directions = {weighted_decision(features, weights)["direction"] for weights in WEIGHT_VARIANTS.values()}
        self.assertEqual(directions, {"偏强"})

    def test_empty_input_has_no_risk_signals(self):
        result = run_weight_validation([])
        self.assertEqual(result["segments"]["A"]["variants"]["equal"]["test"]["observations"], 0)


if __name__ == "__main__":
    unittest.main()
