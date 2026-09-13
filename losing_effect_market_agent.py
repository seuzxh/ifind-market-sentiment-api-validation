"""验证广度型亏钱效应；仅作解释性研究，不生成自动交易信号。"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from backtest_market_agent import _metrics, build_signal_rows
from breadth_change_market_agent import _breadth_index, _history_dates
from market_agent import (
    LOSS_EFFECT_RULES_VERSION,
    PRIMARY_CODE,
    classify_loss_effect,
    parse_history_response,
    segment_for_date,
)


STATE_ORDER = (
    "loss_strong",
    "loss_moderately_strong",
    "loss_not_obvious",
    "down_dominant_2d",
    "down_dominant_3d",
    "index_up_but_down_dominant",
)
MIN_SEGMENT_SAMPLE = 80


def _same_segment(dates: Sequence[str], segment: str | None) -> bool:
    return bool(segment) and all(segment_for_date(date) == segment for date in dates)


def build_loss_effect_samples(
    samples: Sequence[Mapping[str, Any]],
    history_rows: Sequence[Mapping[str, Any]],
    breadth: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """只用信号日及之前实际交易日，补充固定亏钱效应状态。"""

    dates = _history_dates(history_rows)
    positions = {date: index for index, date in enumerate(dates)}
    result: list[dict[str, Any]] = []
    for sample in samples:
        date = str(sample["date"])
        segment = sample.get("segment_id")
        current = breadth.get(date)
        down_ratio = current.get("down_ratio") if current else None
        intensity = classify_loss_effect(down_ratio)
        states: dict[str, bool | None] = {name: None for name in STATE_ORDER}

        if isinstance(down_ratio, (int, float)):
            states["loss_strong"] = down_ratio >= 0.60
            states["loss_moderately_strong"] = 0.50 < down_ratio < 0.60
            states["loss_not_obvious"] = down_ratio <= 0.50
            market_return = sample.get("features", {}).get("market_return")
            states["index_up_but_down_dominant"] = bool(
                isinstance(market_return, (int, float))
                and market_return > 0
                and down_ratio > 0.50
            )

        index = positions.get(date)
        for window, name in ((2, "down_dominant_2d"), (3, "down_dominant_3d")):
            if index is None or index + 1 < window:
                continue
            window_dates = dates[index - window + 1 : index + 1]
            if not _same_segment(window_dates, segment):
                continue
            ratios = [breadth.get(day, {}).get("down_ratio") for day in window_dates]
            if all(isinstance(value, (int, float)) for value in ratios):
                states[name] = all(value > 0.50 for value in ratios)

        result.append({
            **sample,
            "down_ratio": down_ratio,
            "net_breadth": current.get("net_breadth") if current else None,
            "loss_effect": intensity,
            "loss_effect_states": states,
        })
    return result


def _state_metrics(samples: Sequence[Mapping[str, Any]], state: str | None) -> dict[str, Any]:
    if state is None:
        selected = list(samples)
    else:
        selected = [sample for sample in samples if sample["loss_effect_states"].get(state) is True]
    metric = _metrics(samples, lambda sample: int(state is None or sample["loss_effect_states"].get(state) is True))
    average = sum(float(sample["next_return"]) for sample in selected) / len(selected) if selected else None
    return {
        "observations": len(selected),
        "next_day_up_rate": (
            sum(float(sample["next_return"]) > 0 for sample in selected) / len(selected)
            if selected else None
        ),
        "average_next_return": average,
        "worst_next_return": min((float(sample["next_return"]) for sample in selected), default=None),
        "segment_max_drawdown": metric["max_drawdown"],
        "segment_max_drawdown_date": metric["max_drawdown_date"],
        "dates": [sample["date"] for sample in selected],
    }


def _compare_segment(samples: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    baseline = _state_metrics(samples, None)
    states: dict[str, Any] = {}
    for state in STATE_ORDER:
        metrics = _state_metrics(samples, state)
        metrics["vs_baseline"] = {
            "next_day_up_rate_delta": (
                metrics["next_day_up_rate"] - baseline["next_day_up_rate"]
                if metrics["next_day_up_rate"] is not None and baseline["next_day_up_rate"] is not None
                else None
            ),
            "average_next_return_delta": (
                metrics["average_next_return"] - baseline["average_next_return"]
                if metrics["average_next_return"] is not None and baseline["average_next_return"] is not None
                else None
            ),
        }
        states[state] = metrics
    return {"baseline": baseline, "states": states}


def run_experiment(
    history_rows: Sequence[Mapping[str, Any]], breadth_payload: dict[str, Any]
) -> dict[str, Any]:
    raw_samples = build_signal_rows(history_rows)
    breadth, rejected, raw_count = _breadth_index(breadth_payload)
    samples = build_loss_effect_samples(raw_samples, history_rows, breadth)
    segments: dict[str, Any] = {}
    for segment in ("A", "B"):
        part = [sample for sample in samples if sample["segment_id"] == segment]
        segments[segment] = {
            "sample_count": len(part),
            "sample_status": "可分析" if len(part) >= MIN_SEGMENT_SAMPLE else "样本不足，仅观察",
            **_compare_segment(part),
        }
    return {
        "experiment_version": "loss-effect-v1",
        "rules_version": LOSS_EFFECT_RULES_VERSION,
        "deployment_allowed": False,
        "target": f"信号日广度型亏钱效应状态与次日{PRIMARY_CODE}涨跌",
        "scope": "沪深纯A股（data_pool/p00112，p0=A股），不含北交所要求",
        "definitions": {
            "down_ratio": "下跌家数/(上涨家数+平盘家数+下跌家数)",
            "net_breadth": "(上涨家数-下跌家数)/总家数",
            "loss_strong": "down_ratio >= 0.60",
            "loss_moderately_strong": "0.50 < down_ratio < 0.60",
            "loss_not_obvious": "down_ratio <= 0.50",
            "down_dominant_2d": "截至信号日连续2个实际交易日down_ratio > 0.50",
            "down_dominant_3d": "截至信号日连续3个实际交易日down_ratio > 0.50",
            "index_up_but_down_dominant": "信号日883957.TI收益>0且down_ratio>0.50",
        },
        "threshold_policy": "阈值预先固定，不做网格搜索，也不按结果选优",
        "boundary_rule": "2026-05-25排除；A<=2026-05-22，B>=2026-05-26；20日行情窗口、预测日及连续广度窗口均不得跨段",
        "raw_breadth_count": raw_count,
        "valid_breadth_count": len(breadth),
        "rejected_breadth": rejected,
        "segments": segments,
        "limitations": [
            "只有涨跌家数，无法衡量单只股票的亏损幅度、跌幅分布和持仓实际损失；本实验仅刻画广度型亏钱效应",
            "历史响应不能证明当时发布时间与修订状态，不是严格时点数据库",
            "B段样本不足，结果仅作观察，不用于调整线上方向或交易规则",
            "亏钱效应作为独立解释字段，不生成自动交易信号",
        ],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="离线验证广度型亏钱效应，不读取令牌")
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
