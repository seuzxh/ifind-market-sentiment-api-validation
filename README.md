# iFinD 大势分析终端 Agent

这是一个只使用 Python 标准库的命令行工具，用 iFinD 日频数据回答两个问题：指定日期的市场状态，以及基于固定规则的下一交易日倾向。

## 使用

需要 Python 3.11 或更高版本，并在当前进程环境中设置令牌：

```powershell
$env:IFIND_ACCESS_TOKEN = "你的 iFinD token"
python market_agent.py status
python market_agent.py status --date 2026-09-08 --json
python market_agent.py forecast --date 2026-09-08
```

`--json` 输出稳定的 `as_of_date`、`fetched_at`、`segment_id`、`quality_status`、`rules_version`、`source_versions`、`features` 和 `decision` 字段。默认把脱敏证据和结果保存到 `data/`，临时查看可加 `--no-save`。令牌不会写入结果、证据、日志或 Git。

指定的周末、未来日期或没有收盘数据的日期会返回“无行情”，不会静默替换为旧日期。2026-05-25 是已记录的异常跳变边界：边界日不参与分析，A 段到 2026-05-22，B 段从 2026-05-26 开始，任何跨段窗口都会失败。

## 输出含义

方向标签是“偏强 / 震荡 / 偏弱”，风险标签是“Risk-On / 中性 / Risk-Off”。规则只使用主基准收益、Ret5/20/60、SIZE/RISK/MOM Spread 和候选广度；缺失信号不会被当成零。`vwap` 按接口定义记录为均价，但首版不拿它决定方向。结论是规则提示，不是投资建议，也不包含概率预测或交易执行。

广度使用 `data_pool/p00112` 的 `p0=A股` 参数。2026-09-10用户确认涨跌家数只需沪深纯A股，不要求北交所，不再以缺少北交所数据阻塞研究。来源标签 `A_candidate_SH_SZ` 和历史质量标记保留用于追溯，不表示仍需补北交所。主基准代码保持 `883957.TI`。接口请求、字段、复权口径和已知限制见 [iFinD API 文档](docs/ifind/API文档.md)，规则细节见 [规则说明](docs/agent/规则说明.md)。

## 历史回测

使用脱敏的 `history_data` 响应做离线验证，不需要令牌：

```powershell
$response = Get-ChildItem data/backtest_evidence -Recurse -Filter response.json | Select-Object -First 1
python backtest_market_agent.py --response $response.FullName
```

回测会比较当前规则、做空版本和保守回撤门禁，并排除 2026-05-25 边界。当前采用的保守规则是“偏强且非 Risk-Off 才做多，偏弱不做空”；完整结果见 [历史回测报告](docs/agent/历史回测报告.md)。

## 开发与验证

次日上涨概率研究使用同一份本地响应：

```powershell
python probability_market_agent.py --response $response.FullName --output data/probability-results.json
```

首轮候选尚未胜过简单基准，未接入默认预测；结果和后续方向见[概率验证报告](docs/agent/概率验证报告.md)。手续费、滑点和成交约束按当前优先级暂缓。

已增加[广度确认实验](docs/agent/广度确认实验.md)：用`breadth_market_agent.py --history-response <行情响应> --breadth-response <广度响应>`对照上涨占比大于50%的过滤效果。沪深数据留出表现未改善，默认规则不变；不要求补齐北交所历史广度。

同一命令追加`--explore`，可复现7种广度形态、14个视角的[扩展探索](docs/agent/广度扩展探索.md)。候选只在开发期选择，所有结果保留供回溯，不能把小样本命中率当作实时上涨概率。

指数是否为噪音可用`ablation_market_agent.py --response <行情响应>`逐组剔除验证，详见[指数剔除消融](docs/agent/指数剔除消融.md)。本轮RISK和MOM对回撤门禁有保护作用，辅助指数剔除不改变结果。

```powershell
python -m unittest discover -s tests -v
python -m py_compile market_agent.py
```

首期采用单文件 CLI。SQLite（方案二）和计算服务/Web API（方案三）只保留在设计记录中，等到历史检索或多人访问成为实际需求再单独立项。
