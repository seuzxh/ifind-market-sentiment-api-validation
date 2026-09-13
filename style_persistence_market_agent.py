"""离线验证风格信号持续性；不自动接入线上规则。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from backtest_market_agent import _group_rows, _metrics, _spread, build_signal_rows
from market_agent import PRIMARY_CODE, STYLE_PAIRS, parse_history_response, segment_for_date
from probability_market_agent import MIN_TRAIN


METHODS = (
    "any2_positive_3d",
    "risk_mom_positive_3d",
    "any2_positive_5d",
    "all_positive_today",
    "any2_negative_to_positive",
)
GROUPS = ("SIZE", "RISK", "MOM")


def _candidate_flags(signs: Mapping[str, Sequence[bool | None]]) -> dict[str, bool | None]:
    """根据截至信号日的固定长度正负序列计算五个候选。"""

    def persistent(group: str, days: int, minimum: int) -> bool | None:
        values = list(signs.get(group, ()))
        if len(values) < days or any(value is None for value in values[-days:]):
            return None
        return sum(value is True for value in values[-days:]) >= minimum

    positive_3d = {group: persistent(group, 3, 2) for group in GROUPS}
    positive_5d = {group: persistent(group, 5, 3) for group in GROUPS}
    today = {group: persistent(group, 1, 1) for group in GROUPS}
    transitions: dict[str, bool | None] = {}
    for group in GROUPS:
        values = list(signs.get(group, ()))
        transitions[group] = (
            None if len(values) < 2 or any(value is None for value in values[-2:])
            else values[-1] is True and values[-2] is False
        )

    def at_least_two(values: Mapping[str, bool | None]) -> bool | None:
        return None if any(value is None for value in values.values()) else sum(values.values()) >= 2

    return {
        "any2_positive_3d": at_least_two(positive_3d),
        "risk_mom_positive_3d": (
            positive_3d["RISK"] and positive_3d["MOM"]
            if positive_3d["RISK"] is not None and positive_3d["MOM"] is not None
            else None
        ),
        "any2_positive_5d": at_least_two(positive_5d),
        "all_positive_today": (
            all(today.values()) if all(value is not None for value in today.values()) else None
        ),
        "any2_negative_to_positive": at_least_two(transitions),
    }


def _enrich_samples(
    samples: Sequence[Mapping[str, Any]], history_rows: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """只读取当前和此前同段交易日的风格价差符号。"""

    grouped = _group_rows(history_rows)
    dates_by_segment: dict[str, list[str]] = {"A": [], "B": []}
    for date in sorted(grouped.get(PRIMARY_CODE, {})):
        segment = segment_for_date(date)
        if segment:
            dates_by_segment[segment].append(date)

    result: list[dict[str, Any]] = []
    for source in samples:
        sample = dict(source)
        date = str(sample["date"])
        segment = str(sample["segment_id"])
        dates = dates_by_segment[segment]
        index = dates.index(date)
        window = dates[max(0, index - 4):index + 1]
        signs = {
            group: [
                None if (value := _spread(grouped, STYLE_PAIRS[group], item)) is None else value > 0
                for item in window
            ]
            for group in GROUPS
        }
        sample["candidate_flags"] = _candidate_flags(signs)
        result.append(sample)
    return result


def _compare(samples: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    current = lambda sample: int(sample["direction"] == "偏强")
    conservative = lambda sample: int(
        sample["direction"] == "偏强" and sample["risk_mode"] != "Risk-Off"
    )
    def measure(policy):
        result = _metrics(samples, policy)
        active_returns = [
            float(sample["next_return"]) for sample in samples if policy(sample)
        ]
        result["average_active_return"] = (
            sum(active_returns) / len(active_returns) if active_returns else None
        )
        return result

    metrics = {
        "current_long_flat": measure(current),
        "conservative_long_flat": measure(conservative),
    }
    candidates: dict[str, Any] = {}
    for method in METHODS:
        flags = [sample["candidate_flags"].get(method) for sample in samples]
        metrics[method] = measure(
            lambda sample, name=method: int(
                conservative(sample) and sample["candidate_flags"].get(name) is True
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
        "experiment_version": "style-persistence-v1",
        "deployment_allowed": False,
        "target": "同花顺全A 883957.TI 下一交易日上涨；固定持续性条件只过滤现行保守多头信号",
        "methods": {
            "any2_positive_3d": "SIZE/RISK/MOM中至少两组在最近3个交易日内至少2日价差为正",
            "risk_mom_positive_3d": "RISK与MOM最近3个交易日内各至少2日价差为正",
            "any2_positive_5d": "SIZE/RISK/MOM中至少两组在最近5个交易日内至少3日价差为正",
            "all_positive_today": "当前交易日SIZE、RISK、MOM三组价差均为正",
            "any2_negative_to_positive": "至少两组价差由前一交易日负值转为当前交易日正值",
        },
        "method_order": list(METHODS),
        "baseline_policies": ["current_long_flat", "conservative_long_flat"],
        "split_rule": "每段max(40,floor(n*0.7))为留出起点；开发期从第40项开始",
        "boundary_rule": "2026-05-25排除；持续性窗口、信号日及预测日均不可跨段",
        "segments": segments,
        "limitations": [
            "五个候选在运行前固定，不做网格搜索，也不按留出结果自动选优",
            "持续性只使用信号日及此前实际交易日，不使用未来数据",
            "B段样本量较小，留出结果只能观察，不能据此部署",
            "即使A段留出改善，也只能标记为待新数据复核",
            "同一历史已用于多轮探索，时间留出不是全新盲测",
            "历史响应不能证明当时发布时间与修订状态",
            "未纳入手续费、滑点和成交约束",
        ],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="离线验证风格信号持续性，不读取令牌")
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
