# v3 证据账本与计算接口

## 命令与边界

- `python scripts/calc_metrics.py --template`：输出空结构，不是可交付底稿。
- `python scripts/calc_metrics.py input.json`：输出带source_ids、formula、unit、status的派生JSON；stdout可由调用方保存为工作底稿。
- `python scripts/audit_evidence.py input.json --html 报告.html`：0=结构审计通过，2=失败；仍需人工原文与语义核验。
- `python -B -m unittest discover -s scripts -p 'test_*.py'`：仅技能维护测试，使用显式test_fixture合成来源，不用于正式研究。

v2裸数和latest_quarter旧结构不兼容，不能把旧数据无证据搬过来；重新按法定披露写CELL。财报计算器不采数、不生造缺项、不写报告叙述；负责源数据→确定性指标，报告编写由主流程完成。人工先按治理文件确认周期、股本及原文，机器只检验声明一致性。

## 顶层数据

必填 `schema_version:3`、`analysis_as_of`(YYYY-MM-DD)、`entity`、`coverage`、`sources`、`records`、`markets`；`connector_inventory`可空。计算选填 `financials`、`ttm`、`histories`、`macro`。

`entity`：name、primary_code、target_fiscal_year(如FY2026)、fiscal_year_start(财年起始日期或MM-DD)、total_shares(CELL，已发行同权基础普通股总股数)。法定身份证据应另在sources中可追溯，不能取公司概况里未经核实的其它代码。

`coverage`：annual_periods(连续最近五年FY标签，从旧到新)、interim_report_ids(全部目标财年已披露原文ID)、searched_at、searched_channels。无已披露中期填写no_interims_reason并人工核对；缺年度原文可在missing_annual_reasons[财年]写reason/searched_at/searched_channels，不填伪造年度。机器无法发现根本未录入sources的遗漏公告，必须人工对官方目录。

## 来源 SOURCES

每条：id、kind(`statutory_report`/announcement/connector/public/test_fixture)、title、url、period_start、period_end、published_at、opened(bool)、read(bool)、report_type(annual/interim/announcement)、fiscal_year(FY标签)。注：`statutory_report`是一整个字符串，不含空格。

- annual/interim仅指法定报告，非财报数据源的report_type用announcement归入其它观察资料。
- period_start/end表示来源已覆盖的观测期；未来预测财年另写forecast_period和forecast_basis。禁止把未来预测作为已发生财务。来源published_at以实际发布日期，不晚于分析日。
- connector来源另写connector_name、actual_tool、参数与JSON路径；用真实工具响应时间，不得invent URL。url可填写可核对的数据供应商页面/该次结果URI；原文必须法定披露稳定链接。
- opened/read是实际行为声明，必须真实；PDF阅读定位和截图/文字核对由调用方保留。

## 所有数值用 CELL

```json
{"value": null, "status": "not_disclosed", "unit": "currency", "currency": "CNY", "source_ids": [], "locator": "", "reason": "实际读过的报告未单列此项，已查范围记录在source目录"}
```

非空需status=ok、数值finite且非bool、source_ids存在、明确locator(PDF页码/表格/JSON路径)。unit取currency/shares/price/percent/ratio/people。金额计算输入统一元CNY、股数为股(不是亿股)、价格为原币每报价单位、百分数为数值(20表示20%)。来源原币与单位、转换FX、转换公式可在CELL的original_value/original_currency/normalization等元数据记录；norm后的财务CELL仍引用法定报告和汇率来源。价格和汇率必须as_of；其它余额明确随record期末。

缺失四类status为not_disclosed/unavailable/not_applicable/not_calculable，必须value=null、reason。不能用缺失0。分析派生CELL带computed=true和formula、inputs/source_ids，不冒充公告原值。生产禁止test_fixture来源；测试传--allow-test仅用于检验，不是绕过披露验收。

## RECORDS

每条：id、label、kind(annual/ytd/quarter)、start、end、fiscal_year、source_ids、metrics。核心metrics键必须出现：rev营收、cost成本、op营业利润、np合并净利、parent归母、ded扣非、ocf经营现金流、capex购建长期资产现金、assets资产、inv存货、ar应收、ap应付、contract_liab合同负债、prepay预付款、liab总负债、ib_debt有息债、current_assets、current_liab。未披露/不适用用缺失CELL，不强算金融公司库存。

选填rd费用化研发、equity同口径权益、cash可自由现金、short_ib_debt、notes_payable经营应付票据等。prior_id指同区间上一年record，begin_id指start前一天余额record。计算器检验起止，禁止跨期直接同比；年度首年如需同比和平均余额，另收前一年基数。额外余额底稿也用完整record与缺失流量CELL，不要复制他期利润。

当前财年各份原文至少一个同报告期record。YTD流量必须各期区分，余额永远期末数；记录可以附带source原报披露YoY作对照。单季优先原文，只有累计时由calc还原，最近四单季和环比仅连续相邻季有效。不能跨缺失中期强行还原。

## TTM与财务估值分母

`ttm: {annual_id, current_ytd_id, prior_ytd_id}`：最新完整FY+本期YTD−上年同区间YTD，自动产生TTM rev/parent/ded/ocf/capex等；必须财年边界相连。财年最新值与前一年末不能任意混。

`financials`选填CELL：ttm_parent、ttm_ded、ttm_rev、parent_equity、annual_parent、forward_parent、ocf_ttm、capex_ttm、ib_debt、cash、ebitda。TTM算出的对应项只在financials未提供该键时使用；生产人工核验显式提供值与还原值一致，不能静默覆盖分歧。未能还原扣非保持缺失，不用扣非率外推。

## MARKETS

每条code、currency、close_date、price_state='close'、price(CELL price原币)、fx_to_cny(CELL ratio，1原币单位对应CNY金额及as_of)、venue_shares(CELL shares，该类已发行报价单位数量)、underlying_per_quote(每报价单位基础股数，普通股为1，ADR依据官方比例)、has_equal_economic_rights(bool，需原文核实)。若普通股则venue_shares为该类发行股数，ADR为存托报价单位数量；ADR权利重复的本地股已在total_shares计入，不能重复。

计算逐地返回venue_share_value_cny与price_equivalent_equity_cny，两者不同，前者不用于集团全部利润倍数。价差以输入列表第一市场为基准，同权且第一市场有效才计算；不选择“最便宜”交易路线。所有交易日/汇率异步差异人工说明。

选填peg_growth_cases是具名G CELL映射，percent单位，说明起止、正利润和可比口径。计算只检查正值，低基数/并表是否适用需人工核实；主G优先三年CAGR而不是最新季度暴涨。

## 宏观与历史

macro us3m/us10y/cn10y均percent CELL、as_of与收益率定义。期限差只在两美债as_of相同输出；正差不能自动称陡峭化。

histories每条code、point_in_time、frequency、start/end和observations:[{date,pe:CELL,pb:CELL,ps:CELL}]、current:{pe,pb,ps}。每观察CELL需要ratio和当时来源；正有效值计算经验分位与线性25/50/75分位。人工确认是五年还是上市以来、亏损剔除、拆股/股本/实际公告日，机器不会创建PIT证据；缺完整PIT不能调用结果当“官方五年分位”。

## 离线HTML验收

audit_evidence检查资源链接与visible文本、五模块及总结/风险/看板/来源id、各模块interpretation文本、禁止边际毛利率及加总表述、免责声明。不执行浏览器JavaScript，不逐个读取SVG数据，也不验证解释正确性。版式模板有待填正文且无图，是示范资产，禁止直接当报告交付。
