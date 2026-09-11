"""逐组剔除指数，比较rules-v1的方向、风险和保守暴露；只读取本地历史响应。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from backtest_market_agent import _metrics, build_signal_rows
from market_agent import STYLE_PAIRS, SUPPLEMENTAL_CODES, classify_market, parse_history_response


ABLATIONS = {
    "baseline": (),
    "drop_SIZE": ("size_spread",),
    "drop_RISK": ("risk_spread",),
    "drop_MOM": ("mom_spread",),
    "drop_supplemental": ("supplemental_returns",),
}


def _reclassify(samples: Sequence[Mapping[str, Any]], dropped: Sequence[str]) -> list[dict[str, Any]]:
    result = []
    for sample in samples:
        features = dict(sample["features"])
        for key in dropped:
            features[key] = None
        decision = classify_market(features)
        result.append({**sample, "direction": decision["direction"], "risk_mode": decision["risk_mode"],
                       "drawdown_control": decision["drawdown_control"], "decision_reasons": decision["reasons"]})
    return result


def _summary(samples: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    counts = {"偏强": 0, "震荡": 0, "偏弱": 0, "证据不足": 0}
    risks = {"Risk-On": 0, "中性": 0, "Risk-Off": 0}
    for sample in samples:
        counts[sample["direction"]] = counts.get(sample["direction"], 0) + 1
        risks[sample["risk_mode"]] = risks.get(sample["risk_mode"], 0) + 1
    policies = {
        "long_flat": lambda s: int(s["direction"] == "偏强"),
        "conservative_long_flat": lambda s: int(s["drawdown_control"] == "谨慎偏多"),
    }
    return {"observations": len(samples), "directions": counts, "risk_modes": risks,
            "policies": {name: _metrics(samples, policy) for name, policy in policies.items()}}


def run_ablation(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    base = build_signal_rows(rows)
    segments: dict[str, Any] = {}
    for segment in ("A", "B"):
        part = [sample for sample in base if sample["segment_id"] == segment]
        split = max(40, int(len(part) * 0.7))
        variants = {}
        for name, dropped in ABLATIONS.items():
            transformed = _reclassify(part, dropped)
            variants[name] = {
                "dropped_features": list(dropped),
                "all": _summary(transformed),
                "development": _summary(transformed[40:split]),
                "test": _summary(transformed[split:]),
            }
        segments[segment] = {"sample_count": len(part), "split_index": split,
                             "test_start": part[split]["date"] if split < len(part) else None,
                             "variants": variants}
    return {
        "experiment_version": "ablation-v1",
        "scope": "rules-v1日频18指数",
        "target": "检查剔除风格组是否改变预测分布、命中率和保守暴露回撤",
        "ablations": {name: list(dropped) for name, dropped in ABLATIONS.items()},
        "split_rule": "各分段沿用原样本max(40,floor(n*0.7))留出起点；净值分段统计",
        "segments": segments,
        "decision": "仅作消融诊断；本轮不自动选择或替换规则",
        "limitations": ["辅助指数当前不参与classify_market的方向或风险投票，剔除预期不改变结果",
                        "同一历史已用于规则探索，时间留出不是全新盲测",
                        "不含手续费、滑点和成交约束；回撤为简化指数暴露模拟"],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="离线消融rules-v1指数，不读取令牌")
    parser.add_argument("--response", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    raw = args.response.read_bytes()
    result = run_ablation(parse_history_response(json.loads(raw.decode("utf-8-sig"))))
    result["source_sha256"] = hashlib.sha256(raw).hexdigest()
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
