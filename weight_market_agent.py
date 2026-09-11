"""固定风格权重下的rules-v1风险门禁验证；不读取令牌或连接网络。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from backtest_market_agent import _metrics, build_signal_rows
from market_agent import classify_market, parse_history_response


# ponytail: 固定五种可解释权重，阈值始终为2分，不进行网格搜索。
WEIGHT_VARIANTS = {
    "equal": {"SIZE": 1.0, "RISK": 1.0, "MOM": 1.0},
    "SIZE_priority": {"SIZE": 1.5, "RISK": 1.0, "MOM": 0.5},
    "RISK_priority": {"SIZE": 0.5, "RISK": 1.5, "MOM": 1.0},
    "MOM_priority": {"SIZE": 0.5, "RISK": 1.0, "MOM": 1.5},
    "RISK_MOM_consensus": {"SIZE": 0.5, "RISK": 1.0, "MOM": 1.0},
}
RISK_THRESHOLD = 2.0
SPREAD_KEYS = {"SIZE": "size_spread", "RISK": "risk_spread", "MOM": "mom_spread"}


def weighted_decision(features: Mapping[str, Any], weights: Mapping[str, float]) -> dict[str, Any]:
    """保留原方向规则，仅以固定加权票数决定风险模式。"""
    base = classify_market(features)
    positive = sum(weights[name] for name, key in SPREAD_KEYS.items()
                   if isinstance(features.get(key), (int, float)) and features[key] > 0)
    negative = sum(weights[name] for name, key in SPREAD_KEYS.items()
                   if isinstance(features.get(key), (int, float)) and features[key] < 0)
    if positive >= RISK_THRESHOLD:
        risk_mode = "Risk-On"
    elif negative >= RISK_THRESHOLD:
        risk_mode = "Risk-Off"
    else:
        risk_mode = "中性"
    return {"direction": base["direction"], "risk_mode": risk_mode,
            "drawdown_control": "谨慎偏多" if base["direction"] == "偏强" and risk_mode != "Risk-Off" else "观望"}


def _apply(samples: Sequence[Mapping[str, Any]], weights: Mapping[str, float]) -> list[dict[str, Any]]:
    transformed = []
    for sample in samples:
        decision = weighted_decision(sample["features"], weights)
        transformed.append({**sample, **decision})
    return transformed


def _summary(samples: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    risks = {"Risk-On": 0, "中性": 0, "Risk-Off": 0}
    for sample in samples:
        risks[sample["risk_mode"]] += 1
    policy = lambda s: int(s["drawdown_control"] == "谨慎偏多")
    return {"observations": len(samples), "risk_modes": risks,
            "conservative_long_flat": _metrics(samples, policy)}


def run_weight_validation(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    base = build_signal_rows(rows)
    segments = {}
    for segment in ("A", "B"):
        part = [sample for sample in base if sample["segment_id"] == segment]
        split = max(40, int(len(part) * 0.7))
        variants = {}
        for name, weights in WEIGHT_VARIANTS.items():
            transformed = _apply(part, weights)
            variants[name] = {
                "weights": weights,
                "development": _summary(transformed[40:split]),
                "test": _summary(transformed[split:]),
            }
        segments[segment] = {"sample_count": len(part), "split_index": split,
                             "test_start": part[split]["date"] if split < len(part) else None,
                             "variants": variants}
    return {
        "experiment_version": "weights-v1",
        "target": "同花顺全A883957.TI下一交易日涨跌；权重仅影响风险门禁",
        "threshold": RISK_THRESHOLD,
        "variants": WEIGHT_VARIANTS,
        "split_rule": "各分段沿用max(40,floor(n*0.7))留出起点；先看开发期，留出期只验证",
        "segments": segments,
        "deployment_allowed": False,
        "limitations": ["方向标签不随权重变化，命中率只衡量谨慎偏多信号的次日上涨率",
                        "固定少量权重，不做网格搜索或基于留出结果调参",
                        "同一历史已用于探索，时间留出不是全新盲测；净值为简化指数暴露"],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="离线验证风格权重，不读取令牌")
    parser.add_argument("--response", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    raw = args.response.read_bytes()
    result = run_weight_validation(parse_history_response(json.loads(raw.decode("utf-8-sig"))))
    result["source_sha256"] = hashlib.sha256(raw).hexdigest()
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
