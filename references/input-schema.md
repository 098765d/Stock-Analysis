# v3 证据账本与计算接口

## 命令与边界

- `python scripts/calc_metrics.py --template`：输出空结构，不是可交付底稿。
- `python scripts/calc_metrics.py input.json`：输出带source_ids、formula、unit、status的派生JSON；stdout可由调用方保存为工作底稿。
- `python scripts/audit_evidence.py input.json --html 报告.html`：0=结构审计通过，2=失败；仍需人工原文与语义核验。
- `python -B -m unittest discover -s scripts -p 'test_*.py'`：仅技能维护测试，使用显式test_fixture合成来源，不用于正式研究。

v2裸数和latest_quarter旧结构不兼容，不能把旧数据无证据搬过来；重新按法定披露写CELL。财报计算器不采数、不生造缺项、不写报告叙述；负责源数据→确定性指标，报告编写由主流程完成。人工先按治理文件确认周期、股本及原文，机器只检验声明一致性。

## 顶层数据

必填 `schema_version:3`、`analysis_as_of`(YYYY-MM-DD)、`entity`、`coverage`、`sources`、`records`、`markets`；`connector_inventory`可空。计算选填 `financials`、`ttm`、`histories`、`macro`、`peers`、`peer_comparison_specs`。`coverage.evidence_mode` 缺省视为 `api_first`，此时 `coverage.source_strategy` 在审计生产模式下必填。

`entity`：name、primary_code、target_fiscal_year(如FY2026)、fiscal_year_start(财年起始日期或MM-DD)、total_shares(CELL，已发行同权基础普通股总股数)。法定身份证据应另在sources中可追溯，不能取公司概况里未经核实的其它代码。

`coverage`：annual_periods(连续最近五年FY标签，从旧到新)、interim_report_ids(目标财年已披露来源ID，可为空)、searched_at、searched_channels、evidence_mode(`api_first`默认/`document_first`)。无已披露中期填写no_interims_reason并人工核对；缺年度来源可在missing_annual_reasons[财年]写reason/searched_at/searched_channels，不填伪造年度。机器无法发现根本未录入sources的遗漏公告，必须人工对官方目录。

`coverage.source_strategy`（api_first 必填）：
- `api_fields`：由结构化接口/公示取得的字段清单，如「五年营收/成本/营业利润/归母/扣非/OCF/资产/存货/应收/应付/合同负债」「各地收盘价与汇率」「PE/PB/PS」「一致预期」。
- `document_fields`：因接口无字段或需附注语境而必须读原文的字段清单；确无则给空数组但须在 `anchor_check.result` 说明。
- `anchor_check`：`performed:true`、`source_ids`(须含 statutory_report)、`fields_verified`(实际比对字段数，≥1)、`result`(核对结论)、可选 `mismatches`(每项 field/interface_value/document_value/resolution)。没有这一项，接口数据不能作主证据，审计会失败。
- `fallbacks`（连接器缺数据时必填，其余可空）：每项写 `field`（受影响的字段或字段组）、`primary_connector`（原计划使用的连接器）、`fallback_source`（实际降级到的来源）、`reason`（枚举：no_market_coverage / no_field / not_authorized / user_declined / rate_limited / definition_mismatch）、`impact`（对报告结论的影响）。**降级不是静默替换**：有降级却不在 `fallbacks` 中声明，等于把二手源伪装成一手源，审计会失败。

## 来源 SOURCES

每条：id、kind(`statutory_report`/announcement/connector/public/test_fixture)、title、url、period_start、period_end、published_at、opened(bool)、read(bool)、report_type(annual/interim/announcement)、fiscal_year(FY标签)。注：`statutory_report`是一整个字符串，不含空格。

- annual/interim仅指法定报告，非财报数据源的report_type用announcement归入其它观察资料。
- api_first 模式下，接口取到的年报/中报也可按对应 report_type 建来源（kind=connector 或 public），但须在 source_strategy.anchor_check 中体现已与原表比对，不能只声明 opened/read 就当作读过全文。
- period_start/end表示来源已覆盖的观测期；未来预测财年另写forecast_period和forecast_basis。禁止把未来预测作为已发生财务。来源published_at以实际发布日期，不晚于分析日。
- connector来源另写connector_name、actual_tool、参数与JSON路径；用真实工具响应时间，不得invent URL。url可填写可核对的数据供应商页面/该次结果URI；锚定核对所用的原文必须是法定披露稳定链接。
- opened/read是实际行为声明，必须真实；「read=true」指确实读取了该来源将引用的内容，可以是接口返回体，也可以是原文关键页，不能事后追认。

## 所有数值用 CELL

```json
{"value": null, "status": "not_disclosed", "unit": "currency", "currency": "CNY", "source_ids": [], "locator": "", "reason": "实际读过的报告未单列此项，已查范围记录在source目录"}
```

非空需status=ok、数值finite且非bool、source_ids存在、明确locator(PDF页码/表格/JSON路径)。unit取currency/shares/price/percent/ratio/people。金额计算输入统一元CNY、股数为股(不是亿股)、价格为原币每报价单位、百分数为数值(20表示20%)。来源原币与单位、转换FX、转换公式可在CELL的original_value/original_currency/normalization等元数据记录；norm后的财务CELL仍引用法定报告和汇率来源。价格和汇率必须as_of；其它余额明确随record期末。api_first 下接口 CELL 的 locator 写 JSON 路径或字段ID，锚定核对过的写「原文页码 + 接口字段ID」双定位。

缺失四类status为not_disclosed/unavailable/not_applicable/not_calculable，必须value=null、reason。不能用缺失0。分析派生CELL带computed=true和formula、inputs/source_ids，不冒充公告原值。生产禁止test_fixture来源；测试传--allow-test仅用于检验，不是绕过披露验收。

## RECORDS

每条：id、label、kind(annual/ytd/quarter)、start、end、fiscal_year、source_ids、metrics。核心metrics键必须出现：rev营收、cost成本、op营业利润、np合并净利、parent归母、ded扣非、ocf经营现金流、capex购建长期资产现金、assets资产、inv存货、ar应收、ap应付、contract_liab合同负债、prepay预付款、liab总负债、ib_debt有息债、current_assets、current_liab。未披露/不适用用缺失CELL，不强算金融公司库存。

选填rd费用化研发、equity同口径权益、cash可自由现金、short_ib_debt、notes_payable经营应付票据等。prior_id指同区间上一年record，begin_id指start前一天余额record。计算器检验起止，禁止跨期直接同比；年度首年如需同比和平均余额，另收前一年基数。额外余额底稿也用完整record与缺失流量CELL，不要复制他期利润。

当前财年各期至少一个同报告期record。YTD流量必须各期区分，余额永远期末数；记录可以附带来源披露的YoY作对照。单季优先原文或接口直接返回，只有累计时由calc还原，最近四单季和环比仅连续相邻季有效。不能跨缺失中期强行还原。

## TTM与财务估值分母

`ttm: {annual_id, current_ytd_id, prior_ytd_id}`：最新完整FY+本期YTD−上年同区间YTD，自动产生TTM rev/parent/ded/ocf/capex等；必须财年边界相连。财年最新值与前一年末不能任意混。

`financials`选填CELL：ttm_parent、ttm_ded、ttm_rev、parent_equity、annual_parent、forward_parent、ocf_ttm、capex_ttm、ib_debt、cash、ebitda。TTM算出的对应项只在financials未提供该键时使用；生产人工核验显式提供值与还原值一致，不能静默覆盖分歧。未能还原扣非保持缺失，不用扣非率外推。

## MARKETS

每条code、currency、close_date、price_state='close'、price(CELL price原币)、fx_to_cny(CELL ratio，1原币单位对应CNY金额及as_of)、venue_shares(CELL shares，该类已发行报价单位数量)、underlying_per_quote(每报价单位基础股数，普通股为1，ADR依据官方比例)、has_equal_economic_rights(bool，需原文核实)。若普通股则venue_shares为该类发行股数，ADR为存托报价单位数量；ADR权利重复的本地股已在total_shares计入，不能重复。

计算逐地返回venue_share_value_cny与price_equivalent_equity_cny，两者不同，前者不用于集团全部利润倍数。价差以输入列表第一市场为基准，同权且第一市场有效才计算；不选择“最便宜”交易路线。所有交易日/汇率异步差异人工说明。

选填peg_growth_cases是具名G CELL映射，percent单位，说明起止、正利润和可比口径。计算只检查正值，低基数/并表是否适用需人工核实；主G优先三年CAGR而不是最新季度暴涨。

## 同行对照层（生产必填）

`peers` 是独立必填数组，至少 3 家 `relevance:"core"` 进入中位数计算；权重不足或模式不同者标 `reference` 并写明原因，不参与中位数。每家：`id`、`name`、`code`、`listing`、`business_model`、`relevance`、`core_business_weight`(CELL percent，业务收入权重)、`source_ids`、`periods` 数组。每个 period 含 `label`(如FY2025/2026Q3)、`start`、`end`、`metrics`；metrics 键对应 `report-blueprint.md` 同行对照层逐节清单，值一律 CELL，金额单位currency/CNY，比率可用percent/ratio，同行币种与财年先折算对齐，原值另存元数据。所有同行来源必须真实 opened/read；缺指标用缺失CELL+原因，不能拿目标公司值或年度值填季度。

```json
"peers": [{"id":"peer_a","name":"...","code":"...","listing":"SZ","business_model":"ODM",
  "relevance":"core","core_business_weight":CELL,
  "source_ids":["..."],
  "periods":[{"label":"FY2025","start":"2025-01-01","end":"2025-12-31",
    "metrics":{"gross_margin":CELL,"roe":CELL,"net_margin":CELL}}]}]
```

`coverage.peer_comparison_sections` 必须覆盖 `4.1`–`4.7` 七节，逐项写 `section`、`status`(ok/not_disclosed/unavailable/not_applicable)、`reason`(非ok必填)、`peer_ids`(ok至少一家且必须在peers中)。`peer_comparison_specs` 用于计算：每项含 `section`、`label`、`metric`、`period`、`unit`、`target`(目标公司CELL)、可选 `peer_ids`；计算器据此输出目标值、同行有效值、样本数、中位数、均值、目标−中位数差、经验分位及来源链。

生产模式 `audit_evidence.py` 拒绝缺少 peers、核心同行不足 3 家、或七节对照未声明的底稿；`api_first` 模式还要求 `source_strategy` 与锚定核对记录齐备。测试模式（--allow-test）为兼容既有合成夹具不强制，但不能把测试账本当生产研报交付。机器只检查结构与声明，同行是否真的可比、财年/币种是否对齐、解读是否引用同行数字，仍需人工核对。

## 宏观与历史

macro us3m/us10y/cn10y均percent CELL、as_of与收益率定义。期限差只在两美债as_of相同输出；正差不能自动称陡峭化。

histories每条code、point_in_time、frequency、start/end和observations:[{date,pe:CELL,pb:CELL,ps:CELL}]、current:{pe,pb,ps}。每观察CELL需要ratio和当时来源；正有效值计算经验分位与线性25/50/75分位。人工确认是五年还是上市以来、亏损剔除、拆股/股本/实际公告日，机器不会创建PIT证据；缺完整PIT不能调用结果当“官方五年分位”。

## 离线HTML验收

audit_evidence检查资源链接与visible文本、五模块及总结/风险/看板/来源id、各模块interpretation文本、生产模式同行池与4.1–4.7对照覆盖、禁止边际毛利率及加总表述、免责声明。不执行浏览器JavaScript，不逐个读取SVG数据，也不验证解释正确性。版式模板有待填正文且无图，是示范资产，禁止直接当报告交付。
