import unittest

from ablation_market_agent import _reclassify, run_ablation


def _sample(risk=0.1):
    return {"date": "2025-01-30", "next_date": "2025-01-31", "segment_id": "A",
            "direction": "偏强", "risk_mode": "Risk-On", "drawdown_control": "谨慎偏多",
            "next_return": 0.01,
            "features": {"market_return": 0.01, "ret5": 0.02, "ret20": 0.03,
                          "size_spread": -0.1, "risk_spread": risk, "mom_spread": 0.1,
                          "supplemental_returns": {"high_dividend": 0.01}}}


class AblationTests(unittest.TestCase):
    def test_drop_risk_changes_only_risk_gate(self):
        sample = _sample(risk=-0.1)
        kept = _reclassify([sample], ()) [0]
        dropped = _reclassify([sample], ("risk_spread",)) [0]
        self.assertEqual(kept["direction"], dropped["direction"])
        self.assertEqual(kept["risk_mode"], "Risk-Off")
        self.assertEqual(dropped["risk_mode"], "中性")
        self.assertNotEqual(kept["drawdown_control"], dropped["drawdown_control"])

    def test_drop_supplemental_is_exactly_inert(self):
        sample = _sample()
        self.assertEqual(_reclassify([sample], ()), _reclassify([sample], ("supplemental_returns",)))

    def test_empty_rows_have_no_selected_variant(self):
        result = run_ablation([])
        self.assertEqual(result["segments"]["A"]["variants"]["baseline"]["all"]["observations"], 0)


if __name__ == "__main__":
    unittest.main()
