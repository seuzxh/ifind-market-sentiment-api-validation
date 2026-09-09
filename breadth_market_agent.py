"""离线比较固定广度确认门槛；条件命中率不等于校准后的上涨概率。"""

import argparse
import datetime as dt
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

from backtest_market_agent import build_signal_rows, _metrics
from market_agent import PRIMARY_CODE, normalize_breadth, parse_history_response, segment_for_date
from probability_market_agent import MIN_TRAIN


def _breadth_index(payload):
    rows = normalize_breadth(payload)
    frequencies = Counter(row["trade_date"] for row in rows)
    valid, rejected = {}, []
    for row in rows:
        date = row["trade_date"]
        reasons = []
        try:
            if dt.date.fromisoformat(date).isoformat() != date:
                raise ValueError
        except ValueError:
            reasons.append("无效日期")
        if frequencies[date] > 1:
            reasons.append("重复日期，全部排除")
        values = [row[key] for key in ("up", "flat", "down")]
        if any(value is None or not math.isfinite(value) or value < 0
               or not float(value).is_integer() for value in values):
            reasons.append("家数缺失、非有限值、负数或非整数")
        elif sum(values) == 0:
            reasons.append("总家数为零")
        if not reasons and segment_for_date(date) is None:
            reasons.append("跳变隔离日期")
        if reasons:
            rejected.append({"date": date, "reasons": reasons})
        else:
            valid[date] = row
    return valid, rejected, len(rows)


def _compare(part, breadth):
    paired = [{**sample, "up_ratio": breadth[sample["date"]]["up_ratio"]}
              for sample in part if sample["date"] in breadth]
    original = lambda s: int(s["direction"] == "偏强")
    confirmed = lambda s: int(original(s) and s["up_ratio"] > 0.5)
    conservative = lambda s: int(original(s) and s["risk_mode"] != "Risk-Off")
    policies = {"original": original, "breadth_confirm": confirmed,
                "conservative": conservative,
                "conservative_breadth_confirm": lambda s: int(conservative(s) and s["up_ratio"] > 0.5)}
    return {
        "sample_count": len(part), "matched_count": len(paired),
        "matched_dates": [s["date"] for s in paired],
        "missing_dates": [s["date"] for s in part if s["date"] not in breadth],
        "confirmed_dates": [s["date"] for s in paired if confirmed(s)],
        "filtered_dates": [s["date"] for s in paired if original(s) and not confirmed(s)],
        "metrics": {name: {**_metrics(paired, policy),
                           "coverage_denominator": "本阶段同日广度有效的成对样本",
                           "active_dates": [s["date"] for s in paired if policy(s)]}
                    for name, policy in policies.items()},
    }


def run_experiment(history_rows, breadth_payload):
    samples = build_signal_rows(history_rows)
    breadth, rejected, raw_count = _breadth_index(breadth_payload)
    segments = {}
    for segment in ("A", "B"):
        part = [s for s in samples if s["segment_id"] == segment]
        # ponytail: 固定门槛和原样本时间切分，缺失数据不触发调参或重新分割。
        split = max(MIN_TRAIN, int(len(part) * 0.7))
        segments[segment] = {
            "sample_count": len(part), "split_index": split,
            "test_start": part[split]["date"] if split < len(part) else None,
            "all": _compare(part, breadth),
            "development": _compare(part[MIN_TRAIN:split], breadth),
            "test": _compare(part[split:], breadth),
        }
    sample_dates = {s["date"] for s in samples}
    return {
        "experiment_version": "breadth-confirm-v1",
        "scope": "A_candidate_SH_SZ", "deployment_allowed": False,
        "scope_status": "候选沪深A股，未证实覆盖沪深京；仅研究",
        "target": "偏强信号筛选后次日上涨（收益>0）的条件命中率，不是校准后的上涨概率",
        "confirmation_rule": "direction=偏强 且 up/(up+flat+down)>0.5",
        "min_train": MIN_TRAIN,
        "split_rule": "每段原样本max(40,floor(n*0.7))开始留出；开发期从第40项开始；all仅段内描述",
        "raw_breadth_count": raw_count, "valid_breadth_count": len(breadth),
        "rejected_breadth": rejected,
        "unmatched_breadth_dates": sorted(set(breadth) - sample_dates),
        "segments": segments,
        "limitations": ["严格同日连接，不前填，不跨跳变日期计算特征或净值",
                        "历史响应不能证明当时发布时间和修订状态，不是严格时点数据库",
                        "缺失日期不参与双方成对比较；净值仅反映这些可用日期的假设暴露",
                        "既有历史曾用于探索，时间留出并非全新盲测",
                        "未纳入手续费、滑点和成交约束"],
    }


EXPLORE_METHODS = (
    "rising_1", "rising_3", "positive_divergence", "negative_divergence",
    "washout", "broad_rally", "rebound_from_washout",
)


def _event_values(samples, history_rows, breadth):
    # ponytail: 七个预先固定的条件直接计算，不增加调参框架。
    dates = sorted({r["trade_date"] for r in history_rows
                    if r.get("instrument_code") == PRIMARY_CODE})
    positions = {date: i for i, date in enumerate(dates)}
    result = []
    for sample in samples:
        date = sample["date"]
        events = dict.fromkeys(EXPLORE_METHODS)
        if date not in breadth or date not in positions:
            result.append({**sample, "events": events})
            continue
        ratio = breadth[date]["up_ratio"]
        index = positions[date]
        market_return = sample.get("features", {}).get("market_return")
        events["washout"] = ratio <= 0.2
        events["broad_rally"] = ratio >= 0.8
        if market_return is not None and math.isfinite(market_return):
            events["positive_divergence"] = market_return < 0 and ratio > 0.5
            events["negative_divergence"] = market_return > 0 and ratio < 0.5
        for lookback in (1, 3):
            window = dates[index-lookback:index+1] if index >= lookback else []
            if len(window) != lookback+1 or any(
                d not in breadth or segment_for_date(d) != sample["segment_id"]
                for d in window
            ):
                continue
            ratios = [breadth[d]["up_ratio"] for d in window]
            events[f"rising_{lookback}"] = all(a < b for a, b in zip(ratios, ratios[1:]))
            if lookback == 1:
                events["rebound_from_washout"] = ratios[0] <= 0.2 and ratios[1] > ratios[0]
        result.append({**sample, "events": events})
    return result


def _event_summary(eligible, selected):
    returns = [s["next_return"] for s in selected]
    active_dates = {s["date"] for s in selected}
    return {
        "eligible_count": len(eligible), "event_count": len(selected),
        "coverage": len(selected)/len(eligible) if eligible else 0,
        "hit_rate": sum(r > 0 for r in returns)/len(returns) if returns else None,
        "avg_next_return": sum(returns)/len(returns) if returns else None,
        "worst_next_return": min(returns, default=None),
        "eligible_dates": [s["date"] for s in eligible],
        "event_dates": [s["date"] for s in selected],
        "long_exposure_metrics": _metrics(eligible, lambda s: int(s["date"] in active_dates)),
    }


def _explore_part(part, method, conservative):
    candidates = [s for s in part if not conservative or
                  (s["direction"] == "偏强" and s["risk_mode"] != "Risk-Off")]
    eligible = [s for s in candidates if s["events"][method] is not None]
    selected = [s for s in eligible if s["events"][method]]
    return {**_event_summary(eligible, selected),
            "sample_count": len(candidates), "unavailable_count": len(candidates)-len(eligible),
            "coverage_denominator": "原保守信号中该条件可评估的日期" if conservative else "本阶段该条件可评估的日期",
            "baseline": _event_summary(eligible, eligible)}


def run_exploration(history_rows, breadth_payload):
    breadth, rejected, raw_count = _breadth_index(breadth_payload)
    samples = _event_values(build_signal_rows(history_rows), history_rows, breadth)
    segments = {}
    for segment in ("A", "B"):
        part = [s for s in samples if s["segment_id"] == segment]
        split = max(MIN_TRAIN, int(len(part)*0.7))
        methods = {}
        for method in EXPLORE_METHODS:
            methods[method] = {
                view: {phase: _explore_part(stage, method, conservative)
                       for phase, stage in (("development", part[MIN_TRAIN:split]),
                                            ("test", part[split:]))}
                for view, conservative in (("standalone", False), ("conservative", True))
            }
        candidates = [method for method in EXPLORE_METHODS
                      if methods[method]["standalone"]["development"]["event_count"] >= 20
                      and methods[method]["standalone"]["development"]["coverage"] >= 0.1]
        selected = max(candidates, key=lambda method:
                       methods[method]["standalone"]["development"]["hit_rate"], default=None)
        segments[segment] = {
            "sample_count": len(part), "split_index": split,
            "test_start": part[split]["date"] if split < len(part) else None,
            "selected": selected, "methods": methods,
        }
    return {
        "experiment_version": "breadth-explore-v1", "deployment_allowed": False,
        "scope": "A_candidate_SH_SZ", "scope_status": "候选沪深A股，未证实覆盖沪深京；仅研究",
        "target": "七种条件下次日上涨（收益>0）的发生率，均不预设做空方向",
        "selection_rule": "每段仅开发期standalone事件>=20且覆盖>=10%参与选择；命中率最高，平手按定义顺序",
        "split_rule": "每段原样本max(40,floor(n*0.7))开始留出；开发期从第40项开始；缺失不移动切分",
        "method_order": list(EXPLORE_METHODS), "comparison_count": 14,
        "raw_breadth_count": raw_count, "valid_breadth_count": len(breadth),
        "rejected_breadth": rejected, "segments": segments,
        "limitations": ["同一历史已被重复探索，留出期不是全新盲测；14种视角存在多重比较",
                        "基准与条件仅比较同组可评估日期；conservative视角只在原保守偏强日期内比较",
                        "前日按主基准原始日期查找，缺失不跳过，不跨段连接",
                        "long_exposure_metrics仅是假设做多暴露，净值按段独立计算，不自动交易",
                        "历史响应无法证明当时发布时间及修订状态；不自动提升权重",
                        "未纳入手续费、滑点和成交约束"],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="离线验证广度确认门槛，不读取令牌")
    parser.add_argument("--history-response", required=True, type=Path)
    parser.add_argument("--breadth-response", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--explore", action="store_true", help="探索七种固定广度条件，仅研究")
    args = parser.parse_args(argv)
    history_raw = args.history_response.read_bytes()
    breadth_raw = args.breadth_response.read_bytes()
    runner = run_exploration if args.explore else run_experiment
    result = runner(parse_history_response(json.loads(history_raw.decode("utf-8-sig"))),
                            json.loads(breadth_raw.decode("utf-8-sig")))
    result["source_sha256"] = {"history_response": hashlib.sha256(history_raw).hexdigest(),
                               "breadth_response": hashlib.sha256(breadth_raw).hexdigest()}
    text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
