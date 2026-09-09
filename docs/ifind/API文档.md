# iFinD API 文档

内部契约版本：v0.1（待鉴权复验）；最后核对：2026-09-09。

## 1. 状态与接入边界

本轮已取得 REST 成功响应并完成第一轮结构检查；字段单位、复权和历史异常仍待数据验收。`history_data`、`get_trade_dates`、`data_pool`、`basic_data_service`、`smart_stock_picking` 均返回过 HTTP 200 / `errorcode=0`；无令牌的401基线也保留。

| 能力 | HTTP端点，均以 `/api/v1/` 开头 | 本轮可证明的范围 |
|---|---|---|
| 历史行情 | `history_data` | 无鉴权请求可收到供应商格式错误；成功请求结构及数据未复验 |
| 交易日历 | `get_trade_dates` | 同上；官方有该端点示例 |
| 指数基本信息 | `basic_data_service` | 历史对话有样例描述，本轮REST未验证 |
| 广度和成分等报表 | `data_pool` | 通用报表端点有官方示例；`p00112/p03473`仅有历史契约 |
| 特色指数目录 | `smart_stock_picking` | 官方有stock示例；index来自历史请求，未复验 |
| MCP指数查询 | `index_data`，工具接口 | 6次成功响应；发现对象扩张、日期元数据和单位标签问题 |

错误响应可在统一鉴权层产生，因此401也不证明业务路由、请求包装和参数已经通过解析。

## 2. 通用HTTP约定

基础地址：`https://quantapi.51ifind.com/api/v1/`。使用 HTTPS、POST、UTF-8 JSON。

请求头：

```text
Content-Type: application/json
access_token: <运行时读取 IFIND_ACCESS_TOKEN>
```

本文中的JSON均是HTTP请求体。示例中的`formData`是原工具展示标签，不据此改用表单编码。不同端点的包装方式不同；不得统一套一层`reqBody`。

| 契约项 | 当前结论 |
|---|---|
| 凭证 | 已从Windows当前用户环境变量读取；令牌值不写入证据；MCP连接凭证与HTTP令牌不等价 |
| 超时和重试 | 建议客户端单次25秒；401、-1302、-1303停止并检查凭证/权益，避免重复消耗 |
| 并发和限额 | HTTP批量、频率、数据量和分页上限未知；复验脚本串行且至少间隔1秒，这是本地策略，不是供应商配额 |
| MCP并发 | 技能给出未知权益时按免费档、每秒最多2个并发；该限制不能套用到HTTP |
| 日期 | 行情示例`YYYY-MM-DD`；报表历史样例存在`YYYYMMDD`；返回格式按端点核对 |
| 时区 | 本地审计时间带UTC偏移，业务交易日按`Asia/Shanghai`；不把日期当UTC午夜时间戳换日 |
| 空值 | 缺字段、`null`、空数组、空字符串分别记录；不能默认为零 |
| 凭证记录 | 请求证据不含认证头；拒绝跨域重定向；日志不保存令牌 |

### 2.1 本轮实测的错误响应

两个无令牌POST均返回HTTP 401：

```json
{
  "errorcode": -1302,
  "errmsg": "Access_Token is expired or ilegal.",
  "tables": [],
  "datatype": [],
  "inputParams": {},
  "perf": 0,
  "dataVol": 0
}
```

| 字段 | 本次错误样本的JSON类型 | 能证明的内容 |
|---|---|---|
| `errorcode` | number | 本次为-1302，未鉴权；不是行情数据为空的成功结果 |
| `errmsg` | string | 错误消息原文；缺令牌也返回expired or ilegal，不能据此判断某个已配置令牌过期 |
| `tables/datatype` | array | 本次为空，不能推断成功响应的内部结构 |
| `inputParams` | object | 本次为空，不证明服务器接受了请求参数 |
| `perf/dataVol` | number | 本次为0，单位和计费含义未知 |

处理顺序：检查HTTP状态→解析JSON→检查业务错误码→检查表结构→检查代码和日期覆盖→检查字段语义与质量。HTTP 200或`errorcode=0`本身不等于验收通过。历史`-1303 Device exceed limit`只能说明那次供应商报错；不能确定当前权益、并发额度或设备数。

## 3. history_data：通用历史行情

用途：指数日线、收益、趋势、窗口位置与同日风格差值。其他品种需要单独验收。

### 3.1 请求模板

来源：用户附件与历史对话；本轮仅发送过无令牌最小请求，尚未证明包装正确。

```json
{
  "reqBody": {
    "codes": "883957.TI",
    "startdate": "2026-09-04",
    "enddate": "2026-09-04",
    "indicators": "pre_close,open,high,low,close,vwap,chg,pct_chg,volume,amt,turn"
  }
}
```

| 参数 | 示例类型 | 用法 | 未确认项 |
|---|---|---|---|
| `reqBody` | object | 附件包装 | 与顶层JSON的关系、是否必需 |
| `codes` | string | 逗号分隔带后缀代码 | 最大代码数、无效代码和部分返回行为 |
| `startdate/enddate` | string | 固定起止日期 | 包含性、最大跨度、未来日与无数据行为 |
| `indicators` | string | 逗号分隔指标 | 逐品种支持范围、缺失指标行为 |
| `functionpara` | object，可省略 | 指定CPS等函数参数 | 省略时的实际默认值及全部枚举 |
| `functionpara.CPS` | string | 先分别测试`1`和`2` | 此端点的编号含义待确认 |

以上为已知示例用法，不是供应商完整必填性规范。

### 3.2 CPS端点差异：禁止共用枚举

| 来源/端点 | 不复权 | 前复权（分红再投） | 后复权（分红再投） | 证据 |
|---|---|---|---|---|
| 附件`history_data` | 不传functionpara | CPS=2 | CPS=1 | 用户提供，未独立验证 |
| 官方`cmd_history_quotation` | CPS=1，手册默认值 | CPS=2 | CPS=3 | 官方公开手册，未在本轮调用 |

旧端点官方示例采用顶层JSON，没有`reqBody`。不能把旧端点重命名成新端点，也不能直接用任一映射覆盖另一映射。[官方历史行情手册](https://quantapi.51ifind.com/gwstatic/static/ds_web/quantapi-web/help-center/manual.html#历史行情-ths-hq)

必须以`endpoint + request_shape + CPS原值`作为参数版本。指数不同CPS返回相同数值时，结论只能是“本区间相同”；要证明复权含义，需要供应商新端点说明，以及明确发生分红/除权的证券区间对照。

### 3.3 字段映射

下表是接入目标，不表示本轮REST已经返回或确认了这些字段。

| 外部字段 | 内部字段 | 解释与转换 | 当前状态 |
|---|---|---|---|
| 代码字段，成功样本待复验 | `instrument_code` | 保留`.TI`等后缀；按代码关联，不按名称/行号 | REST字段路径未知 |
| 日期字段，成功样本待复验 | `trade_date` | 按响应格式严格解析 | REST字段路径未知 |
| `pre_close` | `prev_close` | 供应商昨收；不得擅自换成上一条缓存close | REST待验 |
| `open/high/low/close` | 同名 | 指数应有明确点位语义；MCP“元”标签不足以定单位 | REST待验 |
| `chg` | `change` | 预期与close-prev_close一致，按精度容差比较 | MCP主基准两日算术对照通过 |
| `pct_chg` | `return_decimal` | 只有百分数单位确认后才除100 | MCP表头为%，不能证明REST单位 |
| `volume` | `volume_raw` | 股/手/其他单位待确认，原值保留 | 未验证 |
| `amt` | `amount_raw` | 元/万元等单位待确认 | 未验证 |
| `vwap` | `vwap_raw` | 计算对象、量额单位、均价定义待确认 | 未验证 |
| `turn` | `turnover_raw` | 百分数/小数、股本分母及指数聚合规则待确认 | 未验证 |

本轮成功响应确认`tables[]`按`thscode`分组，组内`time[]`与指标数组等长；`datatype[]`提供指标类型。实测`vwap`为16.7188而收盘为1911.648，不能直接当作点位均价，单位和计算对象需供应商确认。

### 3.4 数据校验与准入

1. 必须返回目标代码，额外代码隔离；缺少任一正式代码明确列出。禁止按请求顺序和返回顺序直接配对。
2. 日期去重、排序、范围包含性、指标数组等长。用已验收日历判断缺口，非交易日不补零。
3. 数值有限且`high >= max(open,close)`、`low <= min(open,close)`、`high >= low`；价格精度决定容差。
4. 比较`chg`与`close-prev_close`、`pct_chg/100`与`close/prev_close-1`；复权模式不同要逐字段检验，零昨收不做除法。
5. 18条正式对象覆盖至少60交易日；计算Ret60至少需61个价格观测，另给预热期。样例2026-04-01至09-08只是待执行区间，不能预先宣称覆盖足够。
6. 周末、未来日、无效代码、历史起点和部分返回分别测试；一个空日期不能证明完整历史边界。
7. 同日重取只能测短时一致性，不能证明次日不修订。跨日复核另存revision，保留旧数据。

单日收益优先按已冻结契约使用供应商昨收。SIZE=`r(700050)-r(700047)`；RISK=`r(700035)-r(700034)`；MOM=`r(700038)-r(700039)`。必须同日期、同来源、同复权；任一端缺失，结果为空。MCP样例不直接进入生产评分。

## 4. get_trade_dates：交易日历

用途：日线缺口校验、交易日窗口、T+1顺延。

```json
{
  "marketcode": "212001",
  "functionpara": {"mode":"1","dateType":"0","period":"D","dateFormat":"0"},
  "startdate": "2026-09-01",
  "enddate": "2026-10-23"
}
```

顶层JSON有官方端点示例支持。本轮返回33个日期，`inputParams.exchange`回显为`212001.JYS`；这证明本次请求取得日历，仍不能单独证明数字代码覆盖沪深北。[官方HTTP示例](https://quantapi.51ifind.com/gwstatic/static/ds_web/quantapi-web/example.html)

| 参数 | 已知说明 | 边界 |
|---|---|---|
| `marketcode` | 官方出现字符串`212001` | 数字市场字典和沪深北覆盖未确认 |
| `mode` | 官方英文示例出现1 | 完整枚举未知 |
| `dateType` | 官方日期查询手册0=交易日，1=日历日 | 此HTTP组合待实测 |
| `period` | 官方日期查询手册D=日 | 其他周期不在本轮范围 |
| `dateFormat` | 官方日期查询手册0/1/2=YYYY-MM-DD/YYYY/MM/DD/YYYYMMDD | 此HTTP输出待核对 |
| `startdate/enddate` | 用户样例日期字符串 | 包含性、历史及未来边界待测 |

上述枚举来自[官方日期查询手册](https://quantapi.51ifind.com/gwstatic/static/ds_web/quantapi-web/help-center/manual.html#日期查询-ths-datequery)，不要将SDK的SSE/SZSE直接替代HTTP数字值。

成功响应层级、交易日列表位置和空值未知。内部建议字段：`calendar_id, market_scope, trade_date, is_trading_day, fetched_at, source_version`。如果只返回交易日列表，仅在已确认完整的查询范围内派生非交易日，范围外保持未知。周末、节假日、跨节假日T+1与沪深北适用性逐项核对，不能通过上海市场样本就宣布覆盖北交所。

## 5. basic_data_service：指数基本信息

用途：区分代码、名称、编制和加权口径。历史对话记录`codes + indipara数组`，29条对象成功；本轮未重放，完整`indipara`元素结构未取得，不提供伪造的可运行请求。

历史字段线索：`ths_index_short_name_index`、`ths_index_full_name_index`、`ths_index_introduction_index`、`ths_wgt_method_index`、`ths_constituent_num_index`。成分数使用目标日期参数，具体格式待超级命令导出确认。

历史描述称`tables`按`thscode`组织，`table`内字段为数组；仅能作复验预期。MCP查得`883957.TI`名称为“同花顺全A(沪深京)”，`700008.TI`为“同花顺沪深全A”，不能合并代码。名称含沪深京并不单独证明历史成分覆盖规则。

## 6. data_pool：通用报表

一个端点承载多个报表，不能给所有报表套统一业务字段。官方提供`reportname`、`functionpara`、`outputpara`结构，但公开示例不是本轮两个报表。[官方HTTP报表示例](https://quantapi.51ifind.com/gwstatic/static/ds_web/quantapi-web/example.html)

### 6.1 p00112：市场涨跌家数

历史参数线索：`reportname=p00112`，函数参数包含`sdate, edate, p0`；`p0=AB股`仅为历史样例。**纯A股枚举未知，不猜写p0=A股为已验证调用，也不以AB替代。**

| 历史字段 | 历史解释 | 本轮状态 |
|---|---|---|
| `p00112_f001` | 日期，曾返回YYYY/MM/DD | 未复验 |
| `p00112_f002/f003/f004` | 沪深上涨/平盘/下跌 | 仅AB历史描述 |
| `p00112_f005/f006/f007` | 沪市上涨/平盘/下跌 | 同上 |
| `p00112_f008/f009` | 上综指涨跌幅/收盘 | 同上，单位需复核 |
| `p00112_f010/f011/f012` | 深市上涨/平盘/下跌 | 同上 |
| `p00112_f013/f014` | 深成指涨跌幅/收盘 | 同上 |
| `p00112_f021/f022/f023` | 创业板上涨/平盘/下跌 | 深市子集，不能重复加总 |
| `p00112_f024/f025` | 创业板指涨跌幅/收盘 | 未复验 |

历史2026-09-04样本U=2290、F=199、D=2795，合计5284，仅作历史追溯，不是纯A数据。本轮没有原始响应、没有纯A范围验证，也没有北交所字段证据。

纯A范围确认后才定义`UpRatio=U/(U+F+D)`、`NetBreadth=(U-D)/(U+F+D)`；分母为零置空。平盘是否含停牌、总数是否含新股或其他类别需供应商说明。不能从这些字段推导涨停/跌停家数。请求截止日不等于实际数据日期，禁止把旧日广度拼成新日特征。

### 6.2 p03473：指数成分场景

下列模板根据最初需求正文重建，未在本轮验证：

```json
{
  "reportname": "p03473",
  "functionpara": {"iv_date":"20260907","iv_zsdm":"883926.TI"},
  "outputpara": "p03473_f001,p03473_f002,p03473_f003"
}
```

历史描述：f001=日期，f002=证券代码，f003=证券名称；高贝塔当期返回100只。没有权重、生效时间或当时可见时间的证据。`iv_date`请求值不能自行标成成分生效日。

后续验证：重放历史样例、两个确有调样差异的历史日、非交易日、去重与成员数。两天成员相同不能单独证明日期被忽略；不同也不能证明不存在事后修订。缺权重仅可做成员统计，不能计算真实指数贡献。本轮成分归因暂缓，不阻塞当前纯日线观察的范围，但阻塞固定T日成员的T+1研究。

## 7. smart_stock_picking：特色指数目录

```json
{"searchstring":"同花顺特色指数","searchtype":"index"}
```

官方示例确认顶层`searchstring/searchtype`和stock用法；index用法来自历史用户样例。本轮未重放。[官方智能选股示例](https://quantapi.51ifind.com/gwstatic/static/ds_web/quantapi-web/example.html)

历史返回线索：中文“指数代码”“指数简称”，其他列可能含日期后缀。清单数量不能写死，分页与总量完整性未知。搜索词可另测“涨停”“涨停板”“高股息”；目录名称不等于编制规则。历史`-1303`不代表本轮仍报相同错误。

保存`query, fetched_at, code, name, source_version`。使用代码主键，记录新增/删除快照。三个昨日类指数可能重叠，不能当三份独立投票；它们描述前一交易日强势样本当日表现，不是当日涨停家数或连板成功率。

## 8. MCP index_data：辅助核对通道

工具：`mcp__hexin_ifind_ds_index_mcp__index_data`；技能脚本中对应`server_type=index, tool_name=index_data`。这不是上述HTTP端点的别名。

```json
{"query":"仅查询883957.TI在2026-09-04的证券代码、指数全称、收盘价和涨跌幅，保留数据日期。"}
```

本轮实际层级：工具结果`content[]`的text→解析JSON，得到`code,msg,data`→`data`还是JSON字符串→再次解析得到`answer`（Markdown表格）和`indicators_params`。本轮6次外层`code=1,msg=success`；该成功码与HTTP的`errorcode=0`不同。

已观察到：

- MCP-01只请求883957却额外返回700001；MCP-02～05未发现额外对象。必须按代码白名单过滤。
- MCP-04行情列顺序与其他批次不同；不能固定第三列就是收盘。
- 指数收盘、昨收、涨跌被标“元”；原标签留存，单位标记待确认，不能作为股票价格直接消费。
- MCP-06表内是20260903/20260904，顺序倒序，但`indicators_params`全部写“最新”。日期元数据冲突应产生质量标记，不能忽略。
- 涨跌幅只展示4位小数，不能和REST历史描述5位小数做精确相等比较。

因此MCP只作为身份和数值的辅助证据，不作为本轮历史行情生产契约或REST验证的替代。

## 9. 跨项目复用规则

请求指纹至少包含端点、包装、代码集合、日期、指标、原始CPS、报表号及函数参数。供应商参数默认值未冻结前，省略参数与显式参数是不同版本。

原始文件只读保留；派生数据携带`request_id, fetched_at, data_date, endpoint, contract_version, source_hash, quality_status, revision`。主键建议为`instrument_code + trade_date + source + parameter_version + revision`。上游缺失、语义未知、部分返回和元数据冲突均显式标记，不静默填零、补旧日期或替换指数。

维护职责：数据接入维护者更新字段契约和错误处理；研究负责人确认纯A、复权、风格用途及准入；供应商确认单位、市场范围、发布时间和修订规则。任何后续项目只可直接复用已证明的部分；“未验证”项必须完成对应复验。
