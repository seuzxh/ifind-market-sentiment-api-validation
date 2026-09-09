"""分段、逐日向前验证次日上涨概率；仅使用标准库和本地脱敏响应。"""

import argparse
import hashlib
import json
from pathlib import Path

from backtest_market_agent import build_signal_rows, _metrics
from market_agent import parse_history_response


METHODS = ("base_rate", "direction", "risk_mode", "daily_sign")
MIN_TRAIN = 40
SHRINKAGE = 10


def _key(sample, method):
    if method == "base_rate":
        return "all"
    if method == "daily_sign":
        value = sample["features"].get("market_return")
        return "missing" if value is None else "up" if value > 0 else "down" if value < 0 else "flat"
    return sample[method]


def estimate_probability(history, target, method):
    """只用同段、预测时点已经收盘的标签。平盘属于非上涨。"""
    eligible = [s for s in history if s["segment_id"] == target["segment_id"]
                and s["date"] < target["date"] and s["next_date"] <= target["date"]]
    if len(eligible) < MIN_TRAIN:
        return None
    base = (sum(s["next_return"] > 0 for s in eligible) + 1) / (len(eligible) + 2)
    matching = [s for s in eligible if _key(s, method) == _key(target, method)]
    p = base if method == "base_rate" else (
        sum(s["next_return"] > 0 for s in matching) + SHRINKAGE * base
    ) / (len(matching) + SHRINKAGE)
    return {"up_probability": p, "training_observations": len(eligible),
            "matching_observations": len(matching), "trained_through": max(s["next_date"] for s in eligible)}


def _score(records):
    if not records:
        return {"observations": 0, "accuracy": None, "brier": None}
    n = len(records)
    selected = [r for r in records if r["p"] >= 0.6]
    buckets = []
    for low, high in ((0, .4), (.4, .5), (.5, .6), (.6, .7), (.7, 1.01)):
        group = [r for r in records if low <= r["p"] < high]
        if group:
            buckets.append({"range": [low, min(high, 1)], "observations": len(group),
                            "mean_probability": sum(r["p"] for r in group) / len(group),
                            "actual_up_rate": sum(r["y"] for r in group) / len(group)})
    return {"observations": n,
            "accuracy": sum((r["p"] >= .5) == r["y"] for r in records) / n,
            "brier": sum((r["p"] - r["y"]) ** 2 for r in records) / n,
            "always_up_accuracy": sum(r["y"] for r in records) / n,
            "high_probability_count": len(selected), "high_probability_coverage": len(selected) / n,
            "high_probability_hit_rate": sum(r["y"] for r in selected) / len(selected) if selected else None,
            "high_probability_worst_return": min((r["next_return"] for r in selected), default=None),
            "high_probability_exposure": _metrics(records, lambda r: int(r["p"] >= .6)),
            "calibration_buckets": buckets}


def run_experiment(rows):
    samples = build_signal_rows(rows)
    segments = {}
    for segment in ("A", "B"):
        part = [s for s in samples if s["segment_id"] == segment]
        # ponytail: 固定四种分组、固定预热和分割；数据不足时报告不足，不搜索参数。
        split = max(MIN_TRAIN, int(len(part) * .7))
        predictions = {method: [] for method in METHODS}
        for index, target in enumerate(part):
            for method in METHODS:
                estimate = estimate_probability(part[:index], target, method)
                if estimate is not None:
                    predictions[method].append({**target, "p": estimate["up_probability"],
                                                "y": target["next_return"] > 0,
                                                "phase": "development" if index < split else "test"})
        development = {m: _score([r for r in predictions[m] if r["phase"] == "development"]) for m in METHODS}
        eligible = [m for m in METHODS if development[m]["observations"] >= 20]
        selected = min(eligible, key=lambda m: development[m]["brier"]) if eligible else "base_rate"
        testing = {m: _score([r for r in predictions[m] if r["phase"] == "test"]) for m in METHODS}
        candidate, baseline = testing[selected], testing["base_rate"]
        passed = (selected != "base_rate" and candidate["observations"] >= 30
                  and candidate["brier"] < baseline["brier"]
                  and candidate["accuracy"] > max(baseline["accuracy"], candidate["always_up_accuracy"]))
        segments[segment] = {"sample_count": len(part), "sample_start": part[0]["date"] if part else None,
                             "test_start": part[split]["date"] if split < len(part) else None,
                             "selected_on_development": selected, "development": development, "test": testing,
                             "candidate_passed_screen": passed,
                             "selection_status": "开发样本不足，使用基准" if not eligible else "仅依据开发期Brier选择"}
    return {"experiment_version": "probability-v1", "target": "下一交易日上涨（收益>0），平盘计非上涨",
            "min_train": MIN_TRAIN, "shrinkage": SHRINKAGE, "segments": segments,
            "deployment_status": "研究结果，未替换线上规则；需新数据复核",
            "limitations": ["现有数据曾用于规则探索，本次时间留出并非全新盲测",
                            "分段独立训练，不跨2026-05-25计算特征、净值或概率",
                            "不纳入手续费、滑点和成交约束；概率不是保证"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description="分段验证次日上涨概率，不读取令牌")
    parser.add_argument("--response", required=True, type=Path)
    parser.add_argument("--output", type=Path, help="保存汇总实验结果，不包含原始行情")
    args = parser.parse_args(argv)
    raw = args.response.read_bytes()
    result = run_experiment(parse_history_response(json.loads(raw.decode("utf-8"))))
    result["source_sha256"] = hashlib.sha256(raw).hexdigest()
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
