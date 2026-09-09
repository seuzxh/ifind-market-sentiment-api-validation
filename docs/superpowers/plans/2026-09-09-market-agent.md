# 终端市场状态 Agent 开发计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建一个只依赖 Python 标准库的终端 Agent，用 iFinD 日频数据输出当前市场状态和规则型下一交易日判断。

**Architecture:** 单个 `market_agent.py` 负责 CLI、HTTP 请求、响应标准化、分段检查、指标计算和规则输出；纯计算函数与 I/O 分离，便于用标准库 `unittest` 测试。JSON 原始响应和规范化结果按日期保存到本地，但不进入 GitHub Pages；Pages 只展示已经脱敏的静态文档。

**Tech Stack:** Python 3.11+ 标准库（`argparse`、`urllib.request`、`json`、`datetime`、`pathlib`、`hashlib`、`unittest`）。不新增第三方运行依赖。

**Spec:** `docs/superpowers/specs/2026-09-09-market-agent-design.md`

## Global Constraints

- 第一阶段仅实现“日频市场状态识别 + 风格/情绪指数分析”。
- 未来判断只输出规则型标签，不输出未经训练的概率。
- 纯 A 股范围按沪深京处理；不使用 AB 股替代纯 A 股。
- 主基准为 `883957.TI`；核心配对为 SIZE、RISK、MOM。
- 2026-05-25 是全局边界；第一段截至2026-05-22，第二段从2026-05-26开始，任何跨边界计算均拒绝。
- `CPS=1`记录为后复权，`CPS=2`记录为前复权；指数前后复权数值相同可接受，但请求参数必须保留。
- `vwap`按用户确认记录为均价；首版规则不使用其数值做方向判断。
- 令牌只从`IFIND_ACCESS_TOKEN`进程或当前用户环境变量读取，不写入文件、标准输出、异常或 Git。
- 固定访问`https://quantapi.51ifind.com/api/v1/`，禁止自动重定向；HTTP错误、业务错误码非0和结构错误必须显式失败。
- 不引入 SQLite、Web/API 服务、机器学习框架、自动调度或自动交易。

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `market_agent.py` | 唯一运行入口；参数解析、HTTP 客户端、响应解析、标准化、分段、规则和输出 |
| `tests/test_market_agent.py` | 标准库单元测试和一个最小端到端夹具，不访问网络 |
| `docs/agent/规则说明.md` | 规则版本、公式、阈值、标签和边界解释 |
| `README.md` | 安装、环境变量、命令、输出示例、故障排查和数据安全 |
| `.gitignore` | 忽略本地 `data/` 与运行日志 |

## Task 1: 建立纯计算契约与失败测试

**Files:**
- Create: `market_agent.py`
- Create: `tests/test_market_agent.py`

**Interfaces:**
- Produces `parse_history_response(payload: dict) -> list[dict]`、`parse_calendar_response(payload: dict) -> list[str]`、`segment_for_date(date: datetime.date) -> str | None`、`validate_same_segment(dates: list[str]) -> None`。

- [ ] **Step 1: 创建测试目录和失败测试**

在 `tests/test_market_agent.py` 写入以下最小测试；此时导入会失败是预期结果。

```python
import datetime as dt
import unittest

from market_agent import (
    parse_history_response,
    parse_calendar_response,
    segment_for_date,
    validate_same_segment,
)


class ContractTests(unittest.TestCase):
    def test_history_arrays_are_joined_by_date(self):
        payload = {
            "errorcode": 0,
            "tables": [{
                "thscode": "883957.TI",
                "time": ["2026-05-22"],
                "table": {"pre_close": [100.0], "close": [101.0], "chg": [1.0], "pct_chg": [1.0]},
            }],
        }
        self.assertEqual(parse_history_response(payload)[0]["trade_date"], "2026-05-22")
        self.assertEqual(parse_history_response(payload)[0]["instrument_code"], "883957.TI")

    def test_nonzero_errorcode_fails(self):
        with self.assertRaises(ValueError):
            parse_history_response({"errorcode": -4224, "errmsg": "date index is invalid"})

    def test_calendar_reads_time_array(self):
        payload = {"errorcode": 0, "tables": {"time": ["2026-09-01", "2026-09-02"]}}
        self.assertEqual(parse_calendar_response(payload), ["2026-09-01", "2026-09-02"])

    def test_segment_boundary_is_exclusive(self):
        self.assertEqual(segment_for_date(dt.date(2026, 5, 22)), "A")
        self.assertIsNone(segment_for_date(dt.date(2026, 5, 25)))
        self.assertEqual(segment_for_date(dt.date(2026, 5, 26)), "B")

    def test_cross_segment_window_fails(self):
        with self.assertRaises(ValueError):
            validate_same_segment(["2026-05-22", "2026-05-26"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行失败测试**

运行：`python -m unittest tests.test_market_agent -v`

预期：因 `market_agent` 尚未提供接口而失败。

- [ ] **Step 3: 写最小纯函数实现**

在 `market_agent.py` 实现：

```python
import datetime as dt


def _ok(payload):
    if payload.get("errorcode") != 0:
        raise ValueError(f"iFinD业务错误: {payload.get('errorcode')} {payload.get('errmsg', '')}")


def parse_history_response(payload):
    _ok(payload)
    rows = []
    for table in payload.get("tables", []):
        code = table.get("thscode")
        times = table.get("time", [])
        values = table.get("table", {})
        if any(len(values.get(key, [])) != len(times) for key in values):
            raise ValueError("history_data数组长度不一致")
        for index, trade_date in enumerate(times):
            row = {"instrument_code": code, "trade_date": trade_date}
            row.update({key: vals[index] for key, vals in values.items()})
            rows.append(row)
    return rows


def parse_calendar_response(payload):
    _ok(payload)
    tables = payload.get("tables", {})
    return list(tables.get("time", []))


def segment_for_date(date):
    if isinstance(date, str):
        date = dt.date.fromisoformat(date)
    if date <= dt.date(2026, 5, 22):
        return "A"
    if date >= dt.date(2026, 5, 26):
        return "B"
    return None


def validate_same_segment(dates):
    segments = {segment_for_date(date) for date in dates}
    if None in segments or len(segments) != 1:
        raise ValueError("日期跨越2026-05-25分段边界或包含边界日")
```

- [ ] **Step 4: 运行测试并提交**

运行：`python -m unittest tests.test_market_agent -v`

预期：5个测试通过。

提交：

```bash
git add market_agent.py tests/test_market_agent.py
git commit -m "feat: add market data parsing and segment contract"
```

## Task 2: 实现安全 HTTP 客户端和响应证据

**Files:**
- Modify: `market_agent.py`
- Modify: `tests/test_market_agent.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes Task 1 的 `_ok`。
- Produces `IfindClient(token: str, evidence_dir: pathlib.Path | None = None)`、`IfindClient.post(endpoint: str, body: dict) -> dict`、`load_token() -> str`、`save_evidence(...)`。

- [ ] **Step 1: 增加脱敏和错误测试**

在测试文件中增加不访问网络的测试：

```python
from market_agent import redact_text, load_token_from_values


class SecurityTests(unittest.TestCase):
    def test_redact_removes_token(self):
        self.assertNotIn("secret-token", redact_text('x secret-token y', "secret-token"))

    def test_empty_token_fails_before_request(self):
        with self.assertRaises(RuntimeError):
            load_token_from_values("", "")
```

- [ ] **Step 2: 运行失败测试**

运行：`python -m unittest tests.test_market_agent.SecurityTests -v`

预期：因安全辅助函数尚未定义而失败。

- [ ] **Step 3: 写最小客户端**

使用 `urllib.request.Request` 和 `urlopen`，设置25秒超时、`Content-Type`、`access_token`，并通过自定义 `HTTPRedirectHandler` 拒绝重定向。只允许 `history_data`、`get_trade_dates`、`data_pool`、`basic_data_service`、`smart_stock_picking`、`real_time_quotation`、`high_frequency`。

响应保存规则：请求体保存脱敏JSON；响应体先用令牌和 URL 编码令牌替换为`<REDACTED>`，再保存；元数据记录HTTP状态、业务错误码、请求/响应SHA-256和schema摘要。不得把异常文本原样写入证据。

关键实现约束：

```python
def load_token_from_values(process_value, user_value):
    token = process_value or user_value
    if not token or not token.strip():
        raise RuntimeError("缺少IFIND_ACCESS_TOKEN，未发送请求")
    return token


def load_token():
    import os
    return load_token_from_values(os.environ.get("IFIND_ACCESS_TOKEN", ""), "")
```

测试中把 `urllib.request.urlopen` 替换为内存假响应，断言请求方法为POST、URL固定、令牌不出现在保存文本中；不在测试中使用真实令牌。

- [ ] **Step 4: 运行测试并提交**

运行：`python -m unittest tests.test_market_agent -v`

预期：契约和安全测试全部通过。

提交：

```bash
git add market_agent.py tests/test_market_agent.py .gitignore
git commit -m "feat: add secure ifind http client"
```

## Task 3: 标准化行情、广度和快照数据

**Files:**
- Modify: `market_agent.py`
- Modify: `tests/test_market_agent.py`

**Interfaces:**
- Consumes `IfindClient.post`。
- Produces `fetch_history(codes, start, end, cps=None) -> list[dict]`、`fetch_calendar(start, end) -> list[str]`、`fetch_breadth(start, end) -> list[dict]`、`fetch_realtime(code) -> dict`。

- [ ] **Step 1: 写固定响应夹具测试**

测试以下响应规则：`history_data`的`tables[]`按代码拆分；`p00112`的数组按`p00112_f001`日期对齐；`real_time_quotation`的`tables[]`首行含`riseCount`、`fallCount`、`upLimitCount`、`downLimitCount`；`null`保留为`None`。

```python
def test_breadth_preserves_null_and_percent_units(self):
    payload = {"errorcode": 0, "tables": [{"table": {
        "p00112_f001": ["2026/09/08"], "p00112_f002": [3257],
        "p00112_f003": [91], "p00112_f004": [1859],
    }]}
    row = normalize_breadth(payload)[0]
    self.assertEqual(row["up"], 3257)
    self.assertEqual(row["flat"], 91)
```

- [ ] **Step 2: 运行失败测试**

运行：`python -m unittest tests.test_market_agent -v`

预期：因标准化函数尚未实现而失败。

- [ ] **Step 3: 实现最小标准化**

只映射首版用到的字段，未知字段原样保留在`raw`中。`p00112`固定记录`scope="A_candidate_SH_SZ"`，直到供应商确认沪深京纯A语义；不把候选结果伪装成已验收纯A广度。实时快照记录`scope="883957.TI"`和`fetched_at`。

实现三个派生值：`up_ratio = up / (up + flat + down)`、`down_ratio = down / total`、`net_breadth = (up - down) / total`；总数为零或字段缺失时返回`None`并记录质量标记。

- [ ] **Step 4: 运行测试并提交**

运行：`python -m unittest tests.test_market_agent -v`

预期：固定响应标准化测试通过。

提交：

```bash
git add market_agent.py tests/test_market_agent.py
git commit -m "feat: normalize market breadth and quote data"
```

## Task 4: 实现同段指标和规则判断

**Files:**
- Modify: `market_agent.py`
- Modify: `tests/test_market_agent.py`
- Create: `docs/agent/规则说明.md`

**Interfaces:**
- Consumes标准化日线和广度行。
- Produces `compute_features(rows, breadth_rows) -> dict`、`classify_market(features) -> dict`。

- [ ] **Step 1: 写规则失败测试**

```python
def test_bullish_rule_needs_two_style_spreads(self):
    features = {"market_return": 0.01, "ret5": 0.02, "ret20": 0.03,
                "size_spread": 0.01, "risk_spread": 0.02, "mom_spread": -0.01,
                "net_breadth": 0.20, "up_ratio": 0.60, "segment_id": "B"}
    result = classify_market(features)
    self.assertEqual(result["direction"], "偏强")
    self.assertEqual(result["risk_mode"], "Risk-On")
    self.assertGreaterEqual(len(result["reasons"]), 2)

def test_cross_segment_features_are_rejected(self):
    with self.assertRaises(ValueError):
        compute_features([
            {"trade_date": "2026-05-22", "close": 100, "pre_close": 99},
            {"trade_date": "2026-05-26", "close": 101, "pre_close": 100},
        ], [])
```

- [ ] **Step 2: 运行失败测试**

运行：`python -m unittest tests.test_market_agent -v`

预期：因规则函数尚未实现而失败。

- [ ] **Step 3: 写最小规则实现**

指标只包含：主基准当日收益、Ret5、Ret20、Ret60（窗口不足为空）、SIZE/RISK/MOM Spread、上涨比例、净广度和四个补充情绪指数收益。所有窗口先调用`validate_same_segment`；2026-05-25或跨段数据直接失败。

规则固定为：

- 主基准收益与Ret5/Ret20同向且为正，方向候选为偏强；同向为负，方向候选为偏弱；否则为震荡。
- SIZE、RISK、MOM中至少两组为正时提示Risk-On，至少两组为负时提示Risk-Off，否则中性。
- `up_ratio >= 0.5`且`net_breadth > 0`支持普涨；主基准上涨但`net_breadth <= 0`增加“权重托举”原因。
- 缺失指标不按零计算；信号不足时输出“证据不足”。

在`docs/agent/规则说明.md`写明规则版本`rules-v1`、公式、阈值、示例和分段边界。文档同时记录不使用`vwap`、不跨2026-05-25、没有概率含义。

- [ ] **Step 4: 运行测试并提交**

运行：`python -m unittest tests.test_market_agent -v`

预期：规则和分段测试通过。

提交：

```bash
git add market_agent.py tests/test_market_agent.py docs/agent/规则说明.md
git commit -m "feat: add same-segment market state rules"
```

## Task 5: 接入 CLI、保存结果和可读输出

**Files:**
- Modify: `market_agent.py`
- Modify: `tests/test_market_agent.py`
- Modify: `README.md`
- Modify: `.gitignore`

**Interfaces:**
- Consumes `fetch_history`、`fetch_calendar`、`fetch_breadth`、`fetch_realtime`、`compute_features`、`classify_market`。
- Produces命令 `status`、`forecast`，选项`--date YYYY-MM-DD`、`--json`、`--no-save`。

- [ ] **Step 1: 写 CLI 失败测试**

使用`subprocess.run([sys.executable, "market_agent.py", "status"], env={...})`测试缺令牌时退出码为2、标准输出不含令牌；使用内存客户端夹具测试`--json`包含`as_of_date`、`segment_id`、`quality_status`、`features`、`decision`。

- [ ] **Step 2: 运行失败测试**

运行：`python -m unittest tests.test_market_agent -v`

预期：CLI入口尚未完成的测试失败。

- [ ] **Step 3: 实现 CLI**

`argparse`只定义两个子命令和三个选项。默认查询固定的18条正式指数，行情日期使用指定日期；未指定时取本地日期并以交易日历回退到明确返回的最新交易日。未来日期和周末不替换为旧数据，输出状态后退出码为1。

文本输出示例：

```text
数据日期：2026-09-08  分段：B  质量：有限可用
市场：偏弱  风险模式：Risk-Off
依据：主基准收益为负；Ret5/Ret20同向为负；RISK与MOM为负
广度：上涨比例=...  净广度=...
限制：p00112纯A范围尚未完成供应商确认
```

`forecast`使用同一套特征，输出“下一交易日倾向”并明确这是规则判断。`--json`输出稳定键名；`--no-save`不写`data/`。默认保存到`data/YYYY-MM-DD/<request_id>/`，`.gitignore`忽略整个`data/`。

- [ ] **Step 4: 运行本地测试和无令牌检查**

运行：

```bash
python -m unittest discover -s tests -v
python market_agent.py status
```

预期：测试全部通过；第二条因缺令牌在网络请求前退出，退出码2且无令牌回显。

- [ ] **Step 5: 更新 README 并提交**

README 写明 Python 版本、环境变量配置、命令、输出字段、分段规则、错误码处理和“规则结论不是投资建议”。

提交：

```bash
git add market_agent.py tests/test_market_agent.py README.md .gitignore
git commit -m "feat: expose terminal market agent commands"
```

## Task 6: 使用真实接口做最小验收并更新回溯文档

**Files:**
- Modify: `docs/ifind/验证报告.md`
- Modify: `docs/ifind/API文档.md`
- Modify: `README.md`
- Create: `docs/agent/验收记录.md`

**Interfaces:**
- Consumes用户环境变量中的令牌和已验证端点。
- Produces脱敏的命令输出摘要、请求时间、数据日期、规则版本和验收状态。

- [ ] **Step 1: 做固定日期真实冒烟**

只执行以下三条，不在 CI 中使用真实令牌：

```bash
python market_agent.py status --date 2026-09-08 --json
python market_agent.py forecast --date 2026-09-08 --json
python market_agent.py status --date 2026-05-25 --json
```

预期：前两条给出分段B结果；第三条明确边界日不参与分析，不补旧数据。

- [ ] **Step 2: 校验输出和本地数据**

检查JSON含实际数据日期、`segment_id`、质量标记、规则版本和每个Spread的来源代码；确认`data/`中没有`access_token`、令牌原文或认证头。

- [ ] **Step 3: 记录验收结果**

在`docs/agent/验收记录.md`记录运行时间、代码版本、用例、结果、已知限制和下一步，不复制原始令牌或完整响应。把 `history_data` 的单位、CPS、广度候选范围、实时字段空值和2026-05-25边界分别列出。

- [ ] **Step 4: 更新 API 回溯文档并提交**

将实现中的请求体、响应字段、规则版本和命令示例链接回`docs/ifind/API文档.md`，保持“接口请求成功”和“业务数据准入”两种状态分开。

提交：

```bash
git add docs/agent/验收记录.md docs/ifind/验证报告.md docs/ifind/API文档.md README.md
git commit -m "docs: record market agent acceptance"
```

## Task 7: 最终检查和 GitHub Pages 更新

**Files:**
- Modify: `index.html`
- Modify: `README.md`

- [ ] **Step 1: 执行完整本地检查**

运行：

```bash
python -m unittest discover -s tests -v
python -m py_compile market_agent.py
git diff --check
rg -n "0ab4fe|ea5ec|access_token\s*[:=]\s*['\"]" --glob '!docs/ifind/evidence/**' .
```

预期：测试、编译和空白检查通过；敏感值搜索无命中。

- [ ] **Step 2: 更新 Pages 入口**

在`index.html`增加终端 Agent、规则说明和验收记录链接；不把令牌输入框、实时接口调用或任何需要 Secret 的逻辑放到 Pages 静态站点。

- [ ] **Step 3: 提交和推送**

```bash
git add index.html README.md
git commit -m "docs: publish terminal agent guide"
git push origin master
```

- [ ] **Step 4: 验证 Pages workflow**

用 `gh run list --repo seuzxh/ifind-market-sentiment-api-validation --limit 1` 检查最新 workflow 为 success，并访问：

`https://seuzxh.github.io/ifind-market-sentiment-api-validation/`

## 明确不在本计划中的工作

SQLite、Web/API 服务、前端图表、自动调度、概念归因、历史成分还原、T+1 概率模型、分钟历史回放和自动交易分别留待独立规格和计划。只有当历史查询、多人访问、概念数据契约、样本数量、供应商高频回放或交易执行需求达到设计规格中记录的触发条件，才启动对应子项目。

