"""离线验证趋势强度和波动率过滤条件；不自动接入线上规则。"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from pathlib import Path
from typing import Any, Mapping, Sequence

from backtest_market_agent import _metrics, _return, build_signal_rows
from market_agent import PRIMARY_CODE, parse_history_response, segment_for_date
from probability_market_agent import MIN_TRAIN


METHODS = (
    "trend_strength",
    "trend_acceleration",
    "low_vol",
    "high_vol",
    "trend_low_vol",
)


def _volatility_by_date(history_rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    """计算各分段内包含当前日的20日收益率总体标准差。"""

    by_segment: dict[str, list[tuple[str, float | None]]] = {"A": [], "B": []}
    for row in history_rows:
        if row.get("instrument_code") != PRIMARY_CODE:
            continue
        date = str(row.get("trade_date") or "")[:10]
        if not date:
            continue
        segment = segment_for_date(date)
        value = _return(row)
        if segment:
            by_segment[segment].append(
                (date, value if value is not None and math.isfinite(value) else None)
            )

    result: dict[str, float] = {}
    for values in by_segment.values():
        values.sort()
        for index in range(19, len(values)):
            window = [value for _, value in values[index - 19:index + 1]]
            if all(value is not None for value in window):
                result[values[index][0]] = statistics.pstdev(window)
    return result


def _candidate_flags(sample: dict[str, Any]) -> dict[str, bool | None]:
    features = sample.get("features", {})
    ret5 = features.get("ret5")
    ret20 = features.get("ret20")
    vol20 = sample.get("vol20")
    median = sample.get("prior_vol20_median")
    valid_returns = all(isinstance(value, (int, float)) and math.isfinite(value)
                        for value in (ret5, ret20))
    valid_vol = isinstance(vol20, (int, float)) and math.isfinite(vol20) and vol20 > 0
    valid_median = isinstance(median, (int, float)) and math.isfinite(median)
    sample["z5"] = ret5 / vol20 if valid_returns and valid_vol else None
    sample["z20"] = ret20 / vol20 if valid_returns and valid_vol else None
    strength = (sample["z5"] >= 0.5 and sample["z20"] >= 0.5) if valid_vol and valid_returns else None
    acceleration = (ret5 / 5 > ret20 / 20) if valid_returns else None
    low_vol = (vol20 <= median) if valid_vol and valid_median else None
    high_vol = (vol20 > median) if valid_vol and valid_median else None
    return {
        "trend_strength": strength,
        "trend_acceleration": acceleration,
        "low_vol": low_vol,
        "high_vol": high_vol,
        "trend_low_vol": (strength and low_vol) if strength is not None and low_vol is not None else None,
    }


def _enrich_samples(
    samples: Sequence[Mapping[str, Any]], history_rows: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """只用当前及更早的同段收益，为样本补充波动率和固定候选。"""

    volatilities = _volatility_by_date(history_rows)
    dates_by_segment: dict[str, list[str]] = {"A": [], "B": []}
    for date in sorted(volatilities):
        segment = segment_for_date(date)
        if segment:
            dates_by_segment[segment].append(date)

    result: list[dict[str, Any]] = []
    for source in samples:
        sample = dict(source)
        date = str(sample["date"])
        segment = str(sample["segment_id"])
        previous = [volatilities[item] for item in dates_by_segment[segment]
                    if item < date]
        sample["vol20"] = volatilities.get(date)
        sample["prior_vol20_median"] = statistics.median(previous) if previous else None
        sample["candidate_flags"] = _candidate_flags(sample)
        result.append(sample)
    return result


def _compare(samples: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    current = lambda sample: int(sample["direction"] == "偏强")
    baseline = lambda sample: int(
        sample["direction"] == "偏强" and sample["risk_mode"] != "Risk-Off"
    )
    metrics = {
        "current_long_flat": _metrics(samples, current),
        "baseline": _metrics(samples, baseline),
    }
    candidates: dict[str, Any] = {}
    for method in METHODS:
        flags = [sample["candidate_flags"].get(method) for sample in samples]
        metrics[method] = _metrics(
            samples,
            lambda sample, name=method: int(
                baseline(sample) and sample["candidate_flags"].get(name) is True
            ),
        )
        candidates[method] = {
            "available_count": sum(flag is not None for flag in flags),
            "event_count": sum(flag is True for flag in flags),
            "event_dates": [sample["date"] for sample, flag in zip(samples, flags) if flag is True],
        }
    return {"sample_count": len(samples), "candidates": candidates, "metrics": metrics}


def run_experiment(history_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    samples = _enrich_samples(build_signal_rows(history_rows), history_rows)
    segments: dict[str, Any] = {}
    for segment in ("A", "B"):
        part = [sample for sample in samples if sample["segment_id"] == segment]
        split = max(MIN_TRAIN, int(len(part) * 0.7))
        segments[segment] = {
            "sample_count": len(part),
            "split_index": split,
            "test_start": part[split]["date"] if split < len(part) else None,
            "all": _compare(part),
            "development": _compare(part[MIN_TRAIN:split]),
            "test": _compare(part[split:]),
        }
    return {
        "experiment_version": "trend-vol-v1",
        "deployment_allowed": False,
        "target": "同花顺全A 883957.TI 下一交易日上涨；固定条件只过滤现行保守多头信号",
        "methods": {
            "trend_strength": "ret5/vol20>=0.5 且 ret20/vol20>=0.5",
            "trend_acceleration": "ret5/5 > ret20/20",
            "low_vol": "当前20日波动率不高于此前同段滚动波动率中位数",
            "high_vol": "当前20日波动率高于此前同段滚动波动率中位数",
            "trend_low_vol": "trend_strength 与 low_vol 同时成立",
        },
        "method_order": list(METHODS),
        "baseline_policies": ["current_long_flat", "baseline"],
        "split_rule": "每段max(40,floor(n*0.7))为留出起点；开发期从第40项开始",
        "boundary_rule": "2026-05-25排除；20日收益、20日波动率、历史中位数、信号日及预测日均不可跨段",
        "segments": segments,
        "limitations": [
            "固定候选不做网格搜索，留出结果不用于回调阈值",
            "波动率为同段日收益的20日总体标准差，中位数只使用信号日前数据",
            "B段样本量较小，留出结果只能观察，不能据此部署",
            "同一历史已用于多轮探索，时间留出不是全新盲测",
            "历史响应不能证明当时发布时间与修订状态",
            "未纳入手续费、滑点和成交约束",
        ],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="离线验证趋势强度和波动率条件，不读取令牌")
    parser.add_argument("--response", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    raw = args.response.read_bytes()
    result = run_experiment(parse_history_response(json.loads(raw.decode("utf-8-sig"))))
    result["source_sha256"] = hashlib.sha256(raw).hexdigest()
    text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
