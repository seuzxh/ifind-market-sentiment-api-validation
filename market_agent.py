"""终端市场状态 Agent 的纯计算与数据访问基础。

首期只依赖 Python 标准库。网络客户端和 CLI 会在后续任务中逐步加入；
本文件中的解析、日期分段函数不在导入时读取环境变量或发起请求。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
import hashlib
import json
import os
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request
import uuid
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


def redact_text(text: str, token: str = "") -> str:
    """从待保存文本中移除令牌及其 URL 编码形式。"""

    result = str(text)
    if token:
        result = result.replace(token, "<REDACTED>")
        result = result.replace(urllib.parse.quote(token, safe=""), "<REDACTED>")
    return result


def load_token_from_values(process_value: str, user_value: str) -> str:
    """按进程环境优先、用户环境其次读取令牌。"""

    token = process_value or user_value
    if not token or not token.strip():
        raise RuntimeError("缺少IFIND_ACCESS_TOKEN，未发送请求")
    return token.strip()


def load_token() -> str:
    """从当前进程环境读取令牌；不在模块导入时调用。"""

    return load_token_from_values(os.environ.get("IFIND_ACCESS_TOKEN", ""), "")


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: N802
        raise urllib.error.HTTPError(req.full_url, code, "禁止自动重定向", headers, fp)


class IfindClient:
    """iFinD REST 客户端，只允许首期使用的固定端点。"""

    BASE_URL = "https://quantapi.51ifind.com/api/v1/"
    ALLOWED_ENDPOINTS = frozenset({
        "history_data",
        "get_trade_dates",
        "data_pool",
        "basic_data_service",
        "smart_stock_picking",
        "real_time_quotation",
        "high_frequency",
    })

    def __init__(self, token: str, evidence_dir: Path | None = None):
        if not token or not token.strip():
            raise RuntimeError("缺少IFIND_ACCESS_TOKEN，未发送请求")
        self.token = token.strip()
        self.evidence_dir = Path(evidence_dir) if evidence_dir else None
        self._opener = urllib.request.build_opener(_NoRedirectHandler())

    @staticmethod
    def _sha256(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _write_evidence(
        self,
        endpoint: str,
        request_body: dict,
        response_text: str,
        http_status: int,
        business_errorcode: Any,
    ) -> None:
        if self.evidence_dir is None:
            return
        request_text = json.dumps(request_body, ensure_ascii=False, sort_keys=True)
        safe_response = redact_text(response_text, self.token)
        request_id = f"{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
        folder = self.evidence_dir / f"{endpoint}-{request_id}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "request.json").write_text(request_text, encoding="utf-8")
        (folder / "response.json").write_text(safe_response, encoding="utf-8")
        metadata = {
            "endpoint": endpoint,
            "http_status": http_status,
            "errorcode": business_errorcode,
            "request_sha256": self._sha256(request_text),
            "response_sha256": self._sha256(safe_response),
            "response_type": "json" if safe_response.lstrip().startswith(("{", "[")) else "text",
        }
        (folder / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def post(self, endpoint: str, body: dict) -> dict:
        """向固定 iFinD 端点发送 JSON POST，并显式处理错误。"""

        if endpoint not in self.ALLOWED_ENDPOINTS:
            raise ValueError(f"不允许访问的iFinD端点: {endpoint}")
        url = f"{self.BASE_URL}{endpoint}"
        request_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=request_bytes,
            headers={"Content-Type": "application/json", "access_token": self.token},
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=25) as response:
                status = response.getcode()
                response_text = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            self._write_evidence(endpoint, body, "", exc.code, None)
            raise RuntimeError(f"iFinD HTTP错误: {exc.code}") from None
        except urllib.error.URLError as exc:
            self._write_evidence(endpoint, body, "", 0, None)
            raise RuntimeError("iFinD网络请求失败") from None
        try:
            payload = json.loads(response_text)
        except json.JSONDecodeError:
            self._write_evidence(endpoint, body, response_text, status, None)
            raise ValueError("iFinD响应不是合法JSON") from None
        self._write_evidence(endpoint, body, response_text, status, payload.get("errorcode"))
        _ok(payload)
        return payload

    def fetch_history(
        self, codes: str | Sequence[str], start: str, end: str, cps: str | None = None
    ) -> list[dict]:
        return fetch_history(codes, start, end, cps=cps, client=self)

    def fetch_calendar(self, start: str, end: str) -> list[str]:
        return fetch_calendar(start, end, client=self)

    def fetch_breadth(self, start: str, end: str) -> list[dict]:
        return fetch_breadth(start, end, client=self)

    def fetch_realtime(self, code: str = "883957.TI") -> dict:
        return fetch_realtime(code, client=self)


def _get_client(client: IfindClient | None) -> IfindClient:
    return client if client is not None else IfindClient(load_token())


def _join_codes(codes: str | Sequence[str]) -> str:
    return codes if isinstance(codes, str) else ",".join(codes)


def fetch_history(
    codes: str | Sequence[str], start: str, end: str, cps: str | None = None,
    client: IfindClient | None = None,
) -> list[dict]:
    req_body: dict[str, Any] = {
        "codes": _join_codes(codes),
        "startdate": start,
        "enddate": end,
        "indicators": "pre_close,open,high,low,close,vwap,chg,pct_chg,volume,amt,turn",
    }
    if cps is not None:
        req_body["functionpara"] = {"CPS": str(cps)}
    payload = _get_client(client).post("history_data", {"reqBody": req_body})
    return parse_history_response(payload)


def fetch_calendar(start: str, end: str, client: IfindClient | None = None) -> list[str]:
    body = {
        "marketcode": "212001",
        "functionpara": {"mode": "1", "dateType": "0", "period": "D", "dateFormat": "0"},
        "startdate": start,
        "enddate": end,
    }
    return parse_calendar_response(_get_client(client).post("get_trade_dates", body))


def _rows_from_table_payload(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    tables = payload.get("tables", [])
    if isinstance(tables, Mapping):
        tables = [tables]
    rows: list[dict[str, Any]] = []
    for table in tables or []:
        if not isinstance(table, Mapping):
            continue
        values = table.get("table", table)
        if not isinstance(values, Mapping):
            continue
        arrays = {
            key: value for key, value in values.items()
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes))
        }
        if not arrays:
            rows.append(dict(values))
            continue
        size = max((len(value) for value in arrays.values()), default=0)
        for index in range(size):
            rows.append({key: (value[index] if index < len(value) else None) for key, value in arrays.items()})
    return rows


def _number(value: Any) -> float | int | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_breadth(payload: dict) -> list[dict]:
    """标准化 p00112，保留候选范围和缺失质量信息。"""

    _ok(payload)
    result: list[dict] = []
    for raw in _rows_from_table_payload(payload):
        up = _number(raw.get("p00112_f002"))
        flat = _number(raw.get("p00112_f003"))
        down = _number(raw.get("p00112_f004"))
        total = sum(value for value in (up, flat, down) if value is not None)
        complete = all(value is not None for value in (up, flat, down)) and total > 0
        row = {
            "trade_date": str(raw.get("p00112_f001", "")).replace("/", "-")[:10],
            "up": up,
            "flat": flat,
            "down": down,
            "scope": "A_candidate_SH_SZ",
            "quality_status": "有限可用" if complete else "缺失",
            "raw": dict(raw),
        }
        row["up_ratio"] = up / total if complete else None
        row["down_ratio"] = down / total if complete else None
        row["net_breadth"] = (up - down) / total if complete else None
        result.append(row)
    return result


def normalize_realtime(payload: dict, code: str = "883957.TI") -> dict:
    """标准化实时行情快照；接口返回的 null 继续保持 None。"""

    _ok(payload)
    rows = _rows_from_table_payload(payload)
    raw = rows[0] if rows else {}
    normalized = dict(raw)
    normalized.update({"instrument_code": code, "scope": code, "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat()})
    normalized["raw"] = raw
    return normalized


def fetch_breadth(start: str, end: str, client: IfindClient | None = None) -> list[dict]:
    body = {
        "reportname": "p00112",
        "functionpara": {"sdate": start.replace("-", ""), "edate": end.replace("-", ""), "p0": "A股"},
        "outputpara": ",".join(f"p00112_f{i:03d}" for i in range(1, 15)),
    }
    return normalize_breadth(_get_client(client).post("data_pool", body))


def fetch_realtime(code: str = "883957.TI", client: IfindClient | None = None) -> dict:
    indicators = (
        "riseCount,fallCount,upLimitCount,downLimitCount,suspensionCount,tradeDate,tradeTime,"
        "preClose,open,high,low,latest,latestAmount,latestVolume,volume,amount,changeRatio,change,swing"
    )
    return normalize_realtime(
        _get_client(client).post("real_time_quotation", {"codes": code, "indicators": indicators}),
        code=code,
    )


PRIMARY_CODE = "883957.TI"
STYLE_PAIRS = {
    "SIZE": ("700050.TI", "700047.TI"),
    "RISK": ("700035.TI", "700034.TI"),
    "MOM": ("700038.TI", "700039.TI"),
}
SUPPLEMENTAL_CODES = {
    "high_dividend": "883927.TI",
    "yesterday_limit_up": "883958.TI",
    "yesterday_first_board": "883979.TI",
    "yesterday_limit_up_performance": "883900.TI",
}
RULES_VERSION = "rules-v1"


def _daily_return(row: Mapping[str, Any]) -> float | None:
    close = _number(row.get("close"))
    pre_close = _number(row.get("pre_close"))
    if close is not None and pre_close not in (None, 0):
        return close / pre_close - 1
    pct = _number(row.get("pct_chg"))
    return pct / 100 if pct is not None else None


def _series_by_code(rows: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        code = str(row.get("instrument_code") or row.get("thscode") or "")
        if not code:
            continue
        grouped.setdefault(code, []).append(dict(row))
    for values in grouped.values():
        values.sort(key=lambda row: str(row.get("trade_date", "")))
        validate_same_segment([str(row["trade_date"]) for row in values if row.get("trade_date")])
    return grouped


def _close_return(series: Sequence[Mapping[str, Any]], periods: int) -> float | None:
    if len(series) <= periods:
        return None
    latest = _number(series[-1].get("close"))
    earlier = _number(series[-1 - periods].get("close"))
    if latest is None or earlier in (None, 0):
        return None
    return latest / earlier - 1


def _latest_return(series: Sequence[Mapping[str, Any]], as_of: str) -> float | None:
    for row in reversed(series):
        if str(row.get("trade_date", ""))[:10] == as_of:
            return _daily_return(row)
    return None


def _spread(grouped: Mapping[str, Sequence[Mapping[str, Any]]], pair: tuple[str, str], as_of: str) -> float | None:
    positive = _latest_return(grouped.get(pair[0], []), as_of)
    negative = _latest_return(grouped.get(pair[1], []), as_of)
    return positive - negative if positive is not None and negative is not None else None


def compute_features(rows: list[dict], breadth_rows: list[dict]) -> dict:
    """计算同一分段内的首版市场特征；不足窗口返回 None。"""

    grouped = _series_by_code(rows)
    primary = grouped.get(PRIMARY_CODE, [])
    if not primary:
        raise ValueError("缺少主基准883957.TI行情")
    as_of = str(primary[-1].get("trade_date", ""))[:10]
    segment_id = segment_for_date(as_of)
    if segment_id is None:
        raise ValueError("主基准日期位于2026-05-25分析边界")
    breadth = [dict(row) for row in breadth_rows if row.get("trade_date")]
    validate_same_segment([str(row["trade_date"]) for row in breadth])
    breadth_latest = next((row for row in reversed(breadth) if str(row.get("trade_date"))[:10] == as_of), {})
    features: dict[str, Any] = {
        "as_of_date": as_of,
        "segment_id": segment_id,
        "market_return": _latest_return(primary, as_of),
        "ret5": _close_return(primary, 5),
        "ret20": _close_return(primary, 20),
        "ret60": _close_return(primary, 60),
        "size_spread": _spread(grouped, STYLE_PAIRS["SIZE"], as_of),
        "risk_spread": _spread(grouped, STYLE_PAIRS["RISK"], as_of),
        "mom_spread": _spread(grouped, STYLE_PAIRS["MOM"], as_of),
        "up_ratio": breadth_latest.get("up_ratio"),
        "net_breadth": breadth_latest.get("net_breadth"),
        "breadth_scope": breadth_latest.get("scope"),
        "breadth_quality_status": breadth_latest.get("quality_status", "缺失"),
        "supplemental_returns": {
            name: _latest_return(grouped.get(code, []), as_of)
            for name, code in SUPPLEMENTAL_CODES.items()
        },
    }
    return features


def classify_market(features: Mapping[str, Any]) -> dict:
    """按 rules-v1 输出方向和风险模式，不输出概率。"""

    reasons: list[str] = []
    market_return = features.get("market_return")
    ret5 = features.get("ret5")
    ret20 = features.get("ret20")
    direction = "证据不足"
    if all(isinstance(value, (int, float)) for value in (market_return, ret5, ret20)):
        if market_return > 0 and ret5 > 0 and ret20 > 0:
            direction = "偏强"
            reasons.extend(["主基准收益为正", "Ret5/Ret20与当日方向同向为正"])
        elif market_return < 0 and ret5 < 0 and ret20 < 0:
            direction = "偏弱"
            reasons.extend(["主基准收益为负", "Ret5/Ret20与当日方向同向为负"])
        else:
            direction = "震荡"
            reasons.append("主基准与5/20日趋势方向不一致")
    else:
        reasons.append("主基准或Ret5/Ret20数据不足")

    spreads = [features.get(key) for key in ("size_spread", "risk_spread", "mom_spread")]
    positive = sum(isinstance(value, (int, float)) and value > 0 for value in spreads)
    negative = sum(isinstance(value, (int, float)) and value < 0 for value in spreads)
    if positive >= 2:
        risk_mode = "Risk-On"
        reasons.append("SIZE/RISK/MOM中至少两组Spread为正")
    elif negative >= 2:
        risk_mode = "Risk-Off"
        reasons.append("SIZE/RISK/MOM中至少两组Spread为负")
    else:
        risk_mode = "中性"
        if any(value is None for value in spreads):
            reasons.append("风格Spread数据不足")

    up_ratio = features.get("up_ratio")
    net_breadth = features.get("net_breadth")
    if isinstance(up_ratio, (int, float)) and isinstance(net_breadth, (int, float)):
        if up_ratio >= 0.5 and net_breadth > 0:
            reasons.append("上涨比例不低于50%且净广度为正，支持普涨")
        elif isinstance(market_return, (int, float)) and market_return > 0 and net_breadth <= 0:
            reasons.append("指数上涨但净广度不为正，存在权重托举风险")

    if direction == "证据不足" and risk_mode == "中性":
        reasons.append("信号不足，不能形成方向判断")
    return {
        "direction": direction,
        "risk_mode": risk_mode,
        "reasons": reasons,
        "rules_version": RULES_VERSION,
    }
