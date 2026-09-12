"""离线验证涨跌家数变化信号；只用于研究，不自动接入线上规则。"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from backtest_market_agent import _metrics, build_signal_rows
from market_agent import PRIMARY_CODE, normalize_breadth, parse_history_response, segment_for_date
from probability_market_agent import MIN_TRAIN


# 变化方向只使用固定的零阈值，不进行阈值网格搜索。
METHODS = (
    "up_ratio_change_1d",
    "net_breadth_change_1d",
    "up_ratio_change_3d",
    "up_ratio_diffusion_3d",
    "up_ratio_contraction_3d",
)

BASE_POLICIES = {
    "current_long_flat": lambda sample: int(sample["direction"] == "偏强"),
    "conservative_long_flat": lambda sample: int(
        sample["direction"] == "偏强" and sample["risk_mode"] != "Risk-Off"
    ),
}


def _breadth_index(payload: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], int]:
    """标准化并筛选广度，重复日期全部排除，不填补缺失日期。"""

    rows = normalize_breadth(payload)
    frequencies: dict[str, int] = {}
    for row in rows:
        date = str(row.get("trade_date") or "")
        frequencies[date] = frequencies.get(date, 0) + 1

    valid: dict[str, dict[str, Any]] = {}
    rejected: list[dict[str, Any]] = []
    for row in rows:
        date = str(row.get("trade_date") or "")
        reasons: list[str] = []
        try:
            if dt.date.fromisoformat(date).isoformat() != date:
                raise ValueError
        except ValueError:
            reasons.append("无效日期")
        if frequencies[date] > 1:
            reasons.append("重复日期，全部排除")
        values = [row.get(key) for key in ("up", "flat", "down")]
        if any(
            value is None
            or not math.isfinite(float(value))
            or float(value) < 0
            or not float(value).is_integer()
            for value in values
        ):
            reasons.append("家数缺失、非有限值、负数或非整数")
        elif sum(float(value) for value in values) == 0:
            reasons.append("总家数为零")
        if not reasons and segment_for_date(date) is None:
            reasons.append("跳变隔离日期")
        if reasons:
            rejected.append({"date": date, "reasons": reasons})
        else:
            valid[date] = row
    return valid, rejected, len(rows)


def _history_dates(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    dates = {
        str(row.get("trade_date") or "")[:10]
        for row in rows
        if row.get("instrument_code") == PRIMARY_CODE and row.get("trade_date")
    }
    return sorted(date for date in dates if date)


def _same_segment(prior_dates: Sequence[str], sample: Mapping[str, Any]) -> bool:
    segment = sample.get("segment_id")
    return bool(segment) and all(segment_for_date(date) == segment for date in prior_dates)


def _event_values(
    samples: Sequence[Mapping[str, Any]],
    history_rows: Sequence[Mapping[str, Any]],
    breadth: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """仅使用当前日及其之前的实际交易日广度计算固定变化事件。"""

    dates = _history_dates(history_rows)
    positions = {date: index for index, date in enumerate(dates)}
    result: list[dict[str, Any]] = []
    for sample in samples:
        date = str(sample["date"])
        events: dict[str, bool | None] = {method: None for method in METHODS}
        index = positions.get(date)
        current = breadth.get(date)
        if index is not None and current is not None:
            previous_1 = dates[index - 1 : index] if index >= 1 else []
            previous_3 = dates[index - 3 : index] if index >= 3 else []
            one_day = previous_1 + [date]
            three_day = previous_3 + [date]
            if len(one_day) == 2 and _same_segment(one_day, sample):
                prior = breadth.get(previous_1[0])
                if prior is not None:
                    events["up_ratio_change_1d"] = current["up_ratio"] > prior["up_ratio"]
                    events["net_breadth_change_1d"] = current["net_breadth"] > prior["net_breadth"]
            if len(three_day) == 4 and _same_segment(three_day, sample):
                prior = breadth.get(previous_3[0])
                ratios = [breadth.get(day, {}).get("up_ratio") for day in three_day]
                if prior is not None and all(value is not None for value in ratios):
                    events["up_ratio_change_3d"] = current["up_ratio"] > prior["up_ratio"]
                    events["up_ratio_diffusion_3d"] = all(
                        left < right for left, right in zip(ratios, ratios[1:])
                    )
                    events["up_ratio_contraction_3d"] = all(
                        left > right for left, right in zip(ratios, ratios[1:])
                    )
        result.append({**sample, "events": events})
    return result


def _candidate_policy(base_policy, method: str):
    return lambda sample: int(base_policy(sample) and sample["events"].get(method) is True)


def _compare(part: Sequence[Mapping[str, Any]], breadth: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    paired = [sample for sample in part if sample["date"] in breadth]
    metrics: dict[str, dict[str, Any]] = {}
    for base_name, base_policy in BASE_POLICIES.items():
        metrics[base_name] = _metrics(paired, base_policy)
        for method in METHODS:
            metrics[f"{base_name}__{method}"] = _metrics(
                paired, _candidate_policy(base_policy, method)
            )

    candidates: dict[str, dict[str, Any]] = {}
    for method in METHODS:
        available = [sample for sample in paired if sample["events"].get(method) is not None]
        event_rows = [sample for sample in available if sample["events"][method]]
        candidates[method] = {
            "available_count": len(available),
            "event_count": len(event_rows),
            "event_dates": [sample["date"] for sample in event_rows],
            "unavailable_count": len(paired) - len(available),
        }
    return {
        "sample_count": len(part),
        "matched_count": len(paired),
        "missing_dates": [sample["date"] for sample in part if sample["date"] not in breadth],
        "candidates": candidates,
        "metrics": metrics,
    }


def run_experiment(history_rows: Sequence[Mapping[str, Any]], breadth_payload: dict[str, Any]) -> dict[str, Any]:
    """比较固定广度变化事件与两条现行基准，不选择、不部署任何候选。"""

    raw_samples = build_signal_rows(history_rows)
    breadth, rejected, raw_count = _breadth_index(breadth_payload)
    samples = _event_values(raw_samples, history_rows, breadth)
    segments: dict[str, Any] = {}
    for segment in ("A", "B"):
        part = [sample for sample in samples if sample["segment_id"] == segment]
        split = max(MIN_TRAIN, int(len(part) * 0.7))
        segments[segment] = {
            "sample_count": len(part),
            "split_index": split,
            "test_start": part[split]["date"] if split < len(part) else None,
            "all": _compare(part, breadth),
            "development": _compare(part[MIN_TRAIN:split], breadth),
            "test": _compare(part[split:], breadth),
        }
    sample_dates = {sample["date"] for sample in samples}
    return {
        "experiment_version": "breadth-change-v1",
        "scope": "A_candidate_SH_SZ",
        "scope_status": "沪深纯A股广度；用户已确认无需北交所",
        "deployment_allowed": False,
        "target": "现行偏强/保守多头信号叠加广度变化条件后的次日上涨表现",
        "methods": {
            "up_ratio_change_1d": "上涨比例 U_t > U_(t-1)，零为固定阈值",
            "net_breadth_change_1d": "净广度 NB_t > NB_(t-1)，零为固定阈值",
            "up_ratio_change_3d": "上涨比例 U_t > U_(t-3)，零为固定阈值",
            "up_ratio_diffusion_3d": "最近4个交易日上涨比例连续严格上升",
            "up_ratio_contraction_3d": "最近4个交易日上涨比例连续严格下降",
        },
        "method_order": list(METHODS),
        "baseline_policies": list(BASE_POLICIES),
        "split_rule": "每段沿用原样本max(40,floor(n*0.7))留出起点；开发期从第40项开始；缺失不移动切分",
        "boundary_rule": "2026-05-25排除；A<=2026-05-22，B>=2026-05-26；所有变化窗口均不得跨段",
        "raw_breadth_count": raw_count,
        "valid_breadth_count": len(breadth),
        "rejected_breadth": rejected,
        "unmatched_breadth_dates": sorted(set(breadth) - sample_dates),
        "segments": segments,
        "limitations": [
            "变化条件只使用当前日和之前实际交易日，不前填、不跳过缺失日",
            "现行基准与候选均在同一可匹配广度日期上比较；变化窗口不可用时候选保持空仓",
            "B段样本量较小，留出结果只能观察，不能据此部署",
            "多个固定候选仍存在多重比较，本次不按结果自动选优",
            "历史响应不能证明当时发布时间与修订状态，不是严格时点数据库",
            "未纳入手续费、滑点和成交约束",
        ],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="离线验证涨跌家数变化条件，不读取令牌")
    parser.add_argument("--history-response", required=True, type=Path)
    parser.add_argument("--breadth-response", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    history_raw = args.history_response.read_bytes()
    breadth_raw = args.breadth_response.read_bytes()
    result = run_experiment(
        parse_history_response(json.loads(history_raw.decode("utf-8-sig"))),
        json.loads(breadth_raw.decode("utf-8-sig")),
    )
    result["source_sha256"] = {
        "history_response": hashlib.sha256(history_raw).hexdigest(),
        "breadth_response": hashlib.sha256(breadth_raw).hexdigest(),
    }
    text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
