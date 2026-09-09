"""用历史 history_data 响应验证 rules-v1，并比较保守暴露策略。

该脚本只做诊断回测，不连接网络、不读取 token，也不代表交易建议。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from market_agent import (
    PRIMARY_CODE,
    STYLE_PAIRS,
    classify_market,
    parse_history_response,
    segment_for_date,
)


def _return(row: Mapping[str, Any] | None) -> float | None:
    if not row:
        return None
    close = row.get("close")
    pre_close = row.get("pre_close")
    if close is None or pre_close in (None, 0):
        return None
    try:
        return float(close) / float(pre_close) - 1
    except (TypeError, ValueError):
        return None


def _group_rows(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        code = str(row.get("instrument_code") or "")
        date = str(row.get("trade_date") or "")[:10]
        if code and date:
            grouped.setdefault(code, {})[date] = dict(row)
    return grouped


def _spread(
    grouped: Mapping[str, Mapping[str, Mapping[str, Any]]],
    pair: tuple[str, str],
    date: str,
) -> float | None:
    positive = _return(grouped.get(pair[0], {}).get(date))
    negative = _return(grouped.get(pair[1], {}).get(date))
    return positive - negative if positive is not None and negative is not None else None


def build_signal_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """按收盘后信号、下一交易日收盘收益建立无前视样本。"""

    grouped = _group_rows(rows)
    primary = grouped.get(PRIMARY_CODE, {})
    dates = sorted(primary)
    samples: list[dict[str, Any]] = []
    for index in range(20, len(dates) - 1):
        date = dates[index]
        next_date = dates[index + 1]
        segment = segment_for_date(date)
        if segment is None or segment != segment_for_date(next_date):
            continue
        current = primary[date]
        current_close = current.get("close")
        prior5 = primary[dates[index - 5]].get("close")
        prior20 = primary[dates[index - 20]].get("close")
        if current_close in (None, "") or prior5 in (None, 0, "") or prior20 in (None, 0, ""):
            continue
        features = {
            "market_return": _return(current),
            "ret5": float(current_close) / float(prior5) - 1,
            "ret20": float(current_close) / float(prior20) - 1,
            "size_spread": _spread(grouped, STYLE_PAIRS["SIZE"], date),
            "risk_spread": _spread(grouped, STYLE_PAIRS["RISK"], date),
            "mom_spread": _spread(grouped, STYLE_PAIRS["MOM"], date),
            "segment_id": segment,
        }
        decision = classify_market(features)
        next_return = _return(primary[next_date])
        if next_return is None:
            continue
        samples.append({
            "date": date,
            "next_date": next_date,
            "segment_id": segment,
            "direction": decision["direction"],
            "risk_mode": decision["risk_mode"],
            "next_return": next_return,
            "features": features,
        })
    return samples


POLICIES: dict[str, Callable[[Mapping[str, Any]], int]] = {
    "current_long_flat": lambda sample: 1 if sample["direction"] == "偏强" else 0,
    "current_long_short": lambda sample: 1 if sample["direction"] == "偏强" else (-1 if sample["direction"] == "偏弱" else 0),
    "conservative_long_flat": lambda sample: 1 if sample["direction"] == "偏强" and sample["risk_mode"] != "Risk-Off" else 0,
}


def _metrics(samples: Sequence[Mapping[str, Any]], policy: Callable[[Mapping[str, Any]], int]) -> dict[str, Any]:
    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    drawdown_date = None
    active: list[Mapping[str, Any]] = []
    for sample in samples:
        position = policy(sample)
        realized = position * float(sample["next_return"])
        equity *= 1 + realized
        if equity > peak:
            peak = equity
        drawdown = equity / peak - 1
        if drawdown < max_drawdown:
            max_drawdown = drawdown
            drawdown_date = sample["date"]
        if position:
            active.append({**sample, "position": position, "realized": realized})
    hit_rate = sum(item["realized"] > 0 for item in active) / len(active) if active else None
    return {
        "observations": len(samples),
        "active_observations": len(active),
        "exposure": len(active) / len(samples) if samples else 0,
        "total_return": equity - 1,
        "max_drawdown": max_drawdown,
        "max_drawdown_date": drawdown_date,
        "hit_rate": hit_rate,
        "worst_active_return": min((item["realized"] for item in active), default=None),
    }


def _segment_metrics(samples: Sequence[Mapping[str, Any]], policy: Callable[[Mapping[str, Any]], int]) -> dict[str, dict[str, Any]]:
    return {
        segment: _metrics([sample for sample in samples if sample["segment_id"] == segment], policy)
        for segment in ("A", "B")
    }


def run_backtest(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    samples = build_signal_rows(rows)
    policies = {
        name: {"all": _metrics(samples, policy), "segments": _segment_metrics(samples, policy)}
        for name, policy in POLICIES.items()
    }
    policies["buy_and_hold_reference"] = {
        "all": _metrics(samples, lambda sample: 1),
        "segments": _segment_metrics(samples, lambda sample: 1),
    }
    signal_accuracy: dict[str, dict[str, Any]] = {}
    for direction in ("偏强", "偏弱"):
        selected = [sample for sample in samples if sample["direction"] == direction]
        expected_positive = direction == "偏强"
        hits = [sample for sample in selected if (sample["next_return"] > 0) == expected_positive]
        signal_accuracy[direction] = {
            "observations": len(selected),
            "hit_rate": len(hits) / len(selected) if selected else None,
            "average_next_return": sum(sample["next_return"] for sample in selected) / len(selected) if selected else None,
            "worst_next_return": min((sample["next_return"] for sample in selected), default=None),
        }
    return {
        "rules_version": "rules-v1",
        "sample_start": samples[0]["date"] if samples else None,
        "sample_end": samples[-1]["date"] if samples else None,
        "observations": len(samples),
        "boundary_rule": "2026-05-25 excluded; A<=2026-05-22, B>=2026-05-26",
        "signal_accuracy": signal_accuracy,
        "policies": policies,
        "recommended_policy": "conservative_long_flat",
        "recommendation_reason": "偏弱信号不做空；偏强且Risk-Off时空仓，以降低回撤。",
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="离线验证market_agent规则并比较回撤")
    parser.add_argument("--response", required=True, type=Path, help="脱敏history_data响应JSON路径")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    payload = json.loads(args.response.read_text(encoding="utf-8"))
    result = run_backtest(parse_history_response(payload))
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"样本：{result['sample_start']} 至 {result['sample_end']}，有效观察 {result['observations']} 条")
        for name, values in result["policies"].items():
            metrics = values["all"]
            print(f"{name}: 收益={metrics['total_return']:.2%} 最大回撤={metrics['max_drawdown']:.2%} 命中率={metrics['hit_rate']:.2%}")
        print(f"建议策略：{result['recommended_policy']}（{result['recommendation_reason']}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
