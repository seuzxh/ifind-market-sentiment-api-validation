"""终端市场状态 Agent 的纯计算与数据访问基础。

首期只依赖 Python 标准库。网络客户端和 CLI 会在后续任务中逐步加入；
本文件中的解析、日期分段函数不在导入时读取环境变量或发起请求。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from typing import Any


def _ok(payload: Mapping[str, Any]) -> None:
    """检查 iFinD 顶层业务错误码。"""

    if payload.get("errorcode") != 0:
        raise ValueError(
            f"iFinD业务错误: {payload.get('errorcode')} {payload.get('errmsg', '')}"
        )


def parse_history_response(payload: dict) -> list[dict]:
    """把 ``history_data`` 的列式 tables 响应展开为行字典。"""

    _ok(payload)
    rows: list[dict] = []
    tables = payload.get("tables", [])
    if tables is None:
        return rows
    if not isinstance(tables, Sequence) or isinstance(tables, (str, bytes)):
        raise ValueError("history_data响应结构错误: tables应为列表")

    for table in tables:
        if not isinstance(table, Mapping):
            raise ValueError("history_data响应结构错误: table应为对象")
        code = table.get("thscode")
        times = table.get("time", [])
        values = table.get("table", {})
        if not isinstance(times, Sequence) or isinstance(times, (str, bytes)):
            raise ValueError("history_data响应结构错误: time应为列表")
        if not isinstance(values, Mapping):
            raise ValueError("history_data响应结构错误: table.table应为对象")
        for key, vals in values.items():
            if not isinstance(vals, Sequence) or isinstance(vals, (str, bytes)):
                raise ValueError(f"history_data字段{key}应为数组")
            if len(vals) != len(times):
                raise ValueError("history_data数组长度不一致")
        for index, trade_date in enumerate(times):
            row = {"instrument_code": code, "trade_date": trade_date}
            row.update({key: vals[index] for key, vals in values.items()})
            rows.append(row)
    return rows


def _extract_calendar_times(value: Any) -> list[str] | None:
    """递归提取日历响应中名为 time/date 的日期数组。"""

    if isinstance(value, Mapping):
        for key in ("time", "date", "dates", "tradeDate", "trade_date"):
            candidate = value.get(key)
            if isinstance(candidate, Sequence) and not isinstance(candidate, (str, bytes)):
                if all(isinstance(item, str) for item in candidate):
                    return list(candidate)
        for child in value.values():
            found = _extract_calendar_times(child)
            if found is not None:
                return found
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if value and all(isinstance(item, str) for item in value):
            return list(value)
        for child in value:
            found = _extract_calendar_times(child)
            if found is not None:
                return found
    return None


def parse_calendar_response(payload: dict) -> list[str]:
    """读取 ``get_trade_dates`` 响应中的交易日期并标准化为 ISO 字符串。"""

    _ok(payload)
    values = _extract_calendar_times(payload.get("tables", {}))
    if values is None:
        return []
    normalized: list[str] = []
    for value in values:
        text = value.strip()
        if not text:
            continue
        # 接口可能返回带时间的 ISO 值；日历契约只保留日期。
        try:
            parsed = dt.date.fromisoformat(text[:10])
        except ValueError as exc:
            raise ValueError(f"交易日历日期格式错误: {value}") from exc
        normalized.append(parsed.isoformat())
    return normalized


_BOUNDARY_START = dt.date(2026, 5, 26)
_BOUNDARY_END = dt.date(2026, 5, 22)


def segment_for_date(date: dt.date | str) -> str | None:
    """返回异常跳变边界两侧的分析分段。"""

    if isinstance(date, str):
        try:
            date = dt.date.fromisoformat(date[:10])
        except ValueError as exc:
            raise ValueError(f"日期格式错误: {date}") from exc
    if not isinstance(date, dt.date):
        raise TypeError("date必须是datetime.date或YYYY-MM-DD字符串")
    if date <= _BOUNDARY_END:
        return "A"
    if date >= _BOUNDARY_START:
        return "B"
    return None


def validate_same_segment(dates: list[str]) -> None:
    """拒绝跨越 2026-05-25 或包含边界空档日的分析窗口。"""

    segments = {segment_for_date(date) for date in dates}
    if None in segments or len(segments) > 1:
        raise ValueError("日期跨越2026-05-25分段边界或包含边界日")
