#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""核验 v3 证据结构与离线 HTML；不联网，也不代替原文和语义人工复核。"""

import argparse
import datetime as dt
from html.parser import HTMLParser
import json
import math
from pathlib import Path
import re
import sys

CORE_METRICS = (
    "rev", "cost", "op", "np", "parent", "ded", "ocf", "capex",
    "assets", "inv", "ar", "ap", "contract_liab", "prepay", "liab",
    "ib_debt", "current_assets", "current_liab",
)
MONEY_METRICS = set(CORE_METRICS) | {"rd", "equity", "cash", "short_ib_debt", "notes_payable", "ebitda"}
STATUSES = {"ok", "not_disclosed", "unavailable", "not_applicable", "not_calculable"}
UNITS = {"currency", "shares", "price", "percent", "ratio", "people"}
SOURCE_KINDS = {"statutory_report", "announcement", "connector", "public", "test_fixture"}
HUMAN_REVIEW = (
    "机器无法验证真实阅读、来源清单完整性或网络事实；须人工复核原文、披露完整性及检索结论。"
)


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and (
        not isinstance(value, float) or math.isfinite(value)
    )


def _enum(value, choices):
    return isinstance(value, str) and value in choices


class _OfflineHTML(HTMLParser):
    RESOURCE_ATTRS = {
        "script": ("src",), "link": ("href",), "img": ("src", "srcset"),
        "iframe": ("src",), "object": ("data",), "embed": ("src",),
        "audio": ("src",), "video": ("src", "poster"),
        "source": ("src", "srcset"), "input": ("src",),
        "image": ("href", "xlink:href"), "use": ("href", "xlink:href"),
    }
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
    REQUIRED = {"module1", "module2", "module3", "module4", "module5", "summary", "risks", "watchlist", "sources"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.errors = []
        self.stack = []
        self.ids = set()
        self.visible = []
        self.interpretations = {"module" + str(i): [] for i in range(1, 6)}
        self.styles = []

    def resource(self, value, path):
        value = value.strip()
        if not value:
            return
        if value.lower().startswith("data:image/") or value.startswith("#"):
            return
        self.errors.append(path + "：离线报告禁止外部或本地相对资源 " + value)

    def css(self, value):
        # 去除注释并解码 CSS 转义，避免常见的外链绕过。
        value = re.sub(r"/\*.*?\*/", "", value, flags=re.S)
        value = re.sub(r"\\([0-9a-fA-F]{1,6})\s?", lambda m: chr(int(m.group(1), 16)) if int(m.group(1), 16) <= 0x10FFFF else "", value)
        value = re.sub(r"\\([^\n])", r"\1", value)
        if re.search(r"@import\b", value, re.I):
            self.errors.append("HTML CSS：离线报告禁止 @import")
        for match in re.finditer(r"url\(\s*(.*?)\s*\)", value, re.I | re.S):
            self.resource(match.group(1).strip("\"' "), "HTML CSS url")

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        parent_hidden = any(frame[2] for frame in self.stack)
        hidden = parent_hidden or tag in {"script", "style", "template", "head"} or "hidden" in attrs or attrs.get("aria-hidden") == "true"
        style = attrs.get("style", "")
        hidden = hidden or bool(re.search(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)", style, re.I))
        if style:
            self.css(style)
        element_id = attrs.get("id")
        if element_id:
            if element_id in self.ids:
                self.errors.append("HTML：重复 id " + element_id)
            self.ids.add(element_id)
        if tag == "base":
            self.errors.append("HTML：离线报告禁止 base 元素")
        if tag == "meta" and attrs.get("http-equiv", "").lower() == "refresh":
            self.errors.append("HTML：离线报告禁止自动跳转")
        if tag == "iframe" and attrs.get("srcdoc"):
            self.errors.append("HTML：不允许未审计的 iframe srcdoc")
        resource_attrs = self.RESOURCE_ATTRS.get(tag, ())
        if tag == "link" and "stylesheet" not in attrs.get("rel", "").lower().split():
            # 图标、预加载等 link 也会触发网络请求，仍须保持离线。
            resource_attrs = ("href",)
        for attr in resource_attrs:
            if attrs.get(attr):
                if attr == "srcset":
                    # data URI 的逗号不是候选项分隔符。
                    value = attrs[attr]
                    if re.fullmatch(r"data:image/[^;,\s]+(?:;[^,\s]*)?,[^\s,]+(?:\s+(?:\d+w|[\d.]+x))?", value.strip(), re.I):
                        continue
                    for item in value.split(","):
                        self.resource(item.strip().split()[0] if item.strip() else "", "HTML " + tag + " " + attr)
                else:
                    self.resource(attrs[attr], "HTML " + tag + " " + attr)
        if tag not in self.VOID:
            self.stack.append((tag, element_id, hidden, "interpretation" in attrs.get("class", "").split()))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        if self.stack and self.stack[-1][0] == "style":
            self.styles.append(data)
        if any(frame[2] for frame in self.stack):
            return
        self.visible.append(data)
        if any(frame[3] for frame in self.stack):
            for frame in self.stack:
                if frame[1] in self.interpretations:
                    self.interpretations[frame[1]].append(data)

    def finish(self):
        self.css("".join(self.styles))
        for element_id in sorted(self.REQUIRED - self.ids):
            self.errors.append("HTML：缺少必需区域 id=" + element_id)
        for element_id, parts in self.interpretations.items():
            if not "".join(parts).strip():
                self.errors.append("HTML：" + element_id + " 缺少非空可见 interpretation 解释")
        visible = re.sub(r"\s+", "", "".join(self.visible))
        if "边际毛利率" in visible:
            self.errors.append("HTML：可见文本禁止使用边际毛利率")
        if re.search(r"A(?:股)?(?:\+|＋|加|与|及|和|/)H(?:股)?(?:总市值|合计市值|市值合计|市值加总|市值之和)", visible, re.I):
            self.errors.append("HTML：禁止 A+H 总市值等加总表述")
        if "免责声明" not in visible or "不构成投资建议" not in visible:
            self.errors.append("HTML：缺少可见免责声明及不构成投资建议声明")
        return self.errors


def audit(data, html=None, allow_test=False):
    """返回 {ok, errors, warnings}；html 为已读取的 HTML 字符串，不自动联网。"""
    errors = []
    warnings = [HUMAN_REVIEW]

    def fail(path, message):
        errors.append(path + "：" + message)

    def date(value, path, cutoff=None):
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            fail(path, "必须为 YYYY-MM-DD 日期")
            return None
        try:
            result = dt.date.fromisoformat(value)
        except ValueError:
            fail(path, "日期无效")
            return None
        if cutoff and result > cutoff:
            fail(path, "日期晚于 analysis_as_of")
        return result

    def mapping(value, path):
        if not isinstance(value, dict):
            fail(path, "必须为对象")
            return {}
        return value

    def sequence(value, path, nonempty=False):
        if not isinstance(value, list):
            fail(path, "必须为数组")
            return []
        if nonempty and not value:
            fail(path, "不得为空")
        return value

    def required_text(obj, key, path):
        if not _text(obj.get(key)):
            fail(path + "." + key, "必须为非空字符串")

    def fiscal(value, path):
        if not isinstance(value, str) or not re.fullmatch(r"FY\d{4}", value):
            fail(path, "必须为 FY2026 形式")
            return None
        return int(value[2:])

    def finite_tree(value, path):
        if isinstance(value, float) and not math.isfinite(value):
            fail(path, "禁止 JSON NaN/Infinity")
        elif isinstance(value, dict):
            for key, child in value.items():
                finite_tree(child, path + "." + str(key))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                finite_tree(child, path + "[" + str(index) + "]")

    if not isinstance(data, dict):
        return {"ok": False, "errors": ["input：必须为 JSON 对象"], "warnings": warnings}
    if type(data.get("schema_version")) is not int or data["schema_version"] != 3:
        fail("schema_version", "必须为整数 3；旧版裸值 schema 不兼容，请迁移为来源 CELL")
    finite_tree(data, "input")
    as_of = date(data.get("analysis_as_of"), "analysis_as_of")
    entity = mapping(data.get("entity"), "entity")
    for key in ("name", "primary_code"):
        required_text(entity, key, "entity")
    target = fiscal(entity.get("target_fiscal_year"), "entity.target_fiscal_year")
    fy_start = entity.get("fiscal_year_start")
    if isinstance(fy_start, str) and re.fullmatch(r"\d{2}-\d{2}", fy_start):
        date("2000-" + fy_start, "entity.fiscal_year_start")
    else:
        date(fy_start, "entity.fiscal_year_start", as_of)

    sources = {}
    validated_cells = set()
    for index, raw in enumerate(sequence(data.get("sources"), "sources", True)):
        path = "sources[" + str(index) + "]"
        source = mapping(raw, path)
        for key in ("id", "title", "url"):
            required_text(source, key, path)
        source_id = source.get("id")
        if _text(source_id):
            if source_id in sources:
                fail(path + ".id", "来源 id 重复")
            sources[source_id] = source
        if not _enum(source.get("kind"), SOURCE_KINDS):
            fail(path + ".kind", "来源类型不合法")
        if source.get("kind") == "test_fixture" and not allow_test:
            fail(path + ".kind", "生产审计禁止 test_fixture；仅独立测试可启用 --allow-test")
        if not allow_test and _text(source.get("url")) and source["url"].strip().lower().startswith("test_fixture://"):
            fail(path + ".url", "生产审计禁止 test_fixture:// 测试来源伪装为真实来源")
        for key in ("opened", "read"):
            if not isinstance(source.get(key), bool):
                fail(path + "." + key, "必须为 bool")
        if not _enum(source.get("report_type"), {"annual", "interim", "announcement"}):
            fail(path + ".report_type", "报告类型不合法")
        fiscal(source.get("fiscal_year"), path + ".fiscal_year")
        start = date(source.get("period_start"), path + ".period_start", as_of)
        end = date(source.get("period_end"), path + ".period_end", as_of)
        published = date(source.get("published_at"), path + ".published_at", as_of)
        if start and end and start > end:
            fail(path, "period_start 晚于 period_end")
        if published and end and published < end:
            fail(path, "来源发布时间早于其数据期末")

    def references(value, path, required=False):
        refs = sequence(value, path, required)
        known = []
        seen = set()
        for source_id in refs:
            if not _text(source_id):
                fail(path, "source_ids 必须由非空来源 id 字符串组成")
                continue
            if source_id in seen:
                fail(path, "重复来源 id " + source_id)
            seen.add(source_id)
            source = sources.get(source_id)
            if source is None:
                fail(path, "来源不存在 " + source_id)
            else:
                known.append(source)
                if source.get("opened") is not True or source.get("read") is not True:
                    fail(path, "引用来源须已 opened/read：" + source_id)
        return known

    def cell(value, path, expected_unit=None, statutory=False, period_end=None, require_as_of=False, expected_currency=None):
        item = mapping(value, path)
        if isinstance(value, dict):
            validated_cells.add(id(value))
        status = item.get("status")
        if not _enum(status, STATUSES):
            fail(path + ".status", "状态不合法")
        if "value" not in item:
            fail(path + ".value", "CELL 必须显式提供 value")
        number = item.get("value")
        if status == "ok":
            if not _number(number):
                fail(path + ".value", "ok 必须为有限数字，bool 不算数值")
        else:
            if number is not None:
                fail(path + ".value", "非 ok 状态必须为 null，不可填零或其它值")
            required_text(item, "reason", path)
        if not _enum(item.get("unit"), UNITS):
            fail(path + ".unit", "单位不合法")
        if expected_unit and item.get("unit") != expected_unit:
            fail(path + ".unit", "本字段必须使用 " + expected_unit)
        if _enum(item.get("unit"), {"currency", "price"}) and number is not None:
            if not isinstance(item.get("currency"), str) or not re.fullmatch(r"[A-Z]{3}", item["currency"]):
                fail(path + ".currency", "非空金额/价格必须给出三位大写币种")
        if expected_currency and number is not None and item.get("currency") != expected_currency:
            fail(path + ".currency", "本字段币种必须为 " + expected_currency)
        refs = references(item.get("source_ids"), path + ".source_ids", number is not None)
        if "locator" not in item or not isinstance(item.get("locator"), str):
            fail(path + ".locator", "CELL 必须提供定位字符串")
        if number is not None:
            locator = item.get("locator", "")
            if not _text(locator) or locator.strip().lower() in {"unknown", "n/a", "todo", "待核实", "待补", "暂无", "来源"} or re.search(r"\{\{.*?\}\}", locator):
                fail(path + ".locator", "非空数据必须提供明确 PDF 页码/表格/JSON 路径定位")
            valid_kinds = {"statutory_report"} | ({"test_fixture"} if allow_test else set())
            if statutory and not any(_enum(source.get("kind"), valid_kinds) for source in refs):
                fail(path, "核心财务非空值须引用 statutory_report 原文")
            if period_end:
                for source in refs:
                    try:
                        published = dt.date.fromisoformat(source.get("published_at", ""))
                    except (TypeError, ValueError):
                        continue
                    if published < period_end:
                        fail(path, "引用来源发布时间早于数据期末")
        if "as_of" in item or require_as_of:
            cell_date = date(item.get("as_of"), path + ".as_of", as_of)
            if period_end and cell_date and cell_date > period_end:
                fail(path + ".as_of", "数据时点晚于所属记录/行情时点")
        return item

    total_shares = cell(entity.get("total_shares"), "entity.total_shares", "shares")
    if total_shares.get("status") == "ok" and _number(total_shares.get("value")) and total_shares["value"] <= 0:
        fail("entity.total_shares.value", "已披露总股数必须大于零")
    records = []
    record_ids = set()
    for index, raw in enumerate(sequence(data.get("records"), "records", True)):
        path = "records[" + str(index) + "]"
        record = mapping(raw, path)
        records.append(record)
        for key in ("id", "label"):
            required_text(record, key, path)
        record_id = record.get("id")
        if _text(record_id):
            if record_id in record_ids:
                fail(path + ".id", "记录 id 重复")
            record_ids.add(record_id)
        if not _enum(record.get("kind"), {"annual", "ytd", "quarter"}):
            fail(path + ".kind", "必须为 annual/ytd/quarter")
        fiscal(record.get("fiscal_year"), path + ".fiscal_year")
        start = date(record.get("start"), path + ".start", as_of)
        end = date(record.get("end"), path + ".end", as_of)
        if start and end and start > end:
            fail(path, "start 晚于 end")
        refs = references(record.get("source_ids"), path + ".source_ids", True)
        for source in refs:
            try:
                published = dt.date.fromisoformat(source.get("published_at", ""))
            except (TypeError, ValueError):
                continue
            if end and published < end:
                fail(path, "记录引用来源发布时间早于数据期末")
        metrics = mapping(record.get("metrics"), path + ".metrics")
        for key in CORE_METRICS:
            if key not in metrics:
                fail(path + ".metrics." + key, "核心字段必须出现，可用 CELL 明确声明缺失")
        for key, value in metrics.items():
            unit = "currency" if key in MONEY_METRICS else ("people" if key == "employees" else None)
            cell(value, path + ".metrics." + str(key), unit, key in CORE_METRICS, end,
                 expected_currency="CNY" if key in MONEY_METRICS else None)

    if "financials" in data:
        financials = mapping(data["financials"], "financials")
        for key, value in financials.items():
            cell(value, "financials." + str(key), "currency", expected_currency="CNY")

    coverage = mapping(data.get("coverage"), "coverage")
    annuals = sequence(coverage.get("annual_periods"), "coverage.annual_periods", True)
    annual_numbers = [fiscal(value, "coverage.annual_periods") for value in annuals]
    if len(annuals) != 5 or any(value is None for value in annual_numbers):
        fail("coverage.annual_periods", "必须覆盖最近五个连续完整财年，不可仅给最新一期")
    elif annual_numbers != list(range(annual_numbers[0], annual_numbers[0] + 5)):
        fail("coverage.annual_periods", "五个财年必须连续且由旧到新")
    if target and any(value is not None and value > target for value in annual_numbers):
        fail("coverage.annual_periods", "财年不得晚于 target_fiscal_year")
    searched = date(coverage.get("searched_at"), "coverage.searched_at", as_of)
    channels = sequence(coverage.get("searched_channels"), "coverage.searched_channels", True)
    if any(not _text(channel) for channel in channels):
        fail("coverage.searched_channels", "检索渠道必须为非空字符串")
    missing = mapping(coverage.get("missing_annual_reasons", {}), "coverage.missing_annual_reasons")
    for year in missing:
        if year not in annuals:
            fail("coverage.missing_annual_reasons", "缺口财年必须在 annual_periods 检查范围内")
    statutory_kinds = {"statutory_report"} | ({"test_fixture"} if allow_test else set())
    disclosed_annuals = []
    for source in sources.values():
        if source.get("report_type") == "annual" and _enum(source.get("kind"), statutory_kinds) and source.get("opened") is True and source.get("read") is True:
            year = source.get("fiscal_year")
            if isinstance(year, str) and re.fullmatch(r"FY\d{4}", year):
                disclosed_annuals.append(int(year[2:]))
    if disclosed_annuals and annual_numbers and all(value is not None for value in annual_numbers) and max(disclosed_annuals) > max(annual_numbers):
        fail("coverage.annual_periods", "遗漏本次来源已披露的最近年度")
    for year in annuals:
        if not _text(year):
            continue
        annual_source_ids = {
            source_id for source_id, source in sources.items()
            if source.get("fiscal_year") == year and source.get("report_type") == "annual"
            and _enum(source.get("kind"), statutory_kinds)
            and source.get("opened") is True and source.get("read") is True
        }
        matching_records = [
            record for record in records
            if record.get("kind") == "annual" and record.get("fiscal_year") == year
            and isinstance(record.get("source_ids"), list)
            and any(
                source_id in annual_source_ids
                and record.get("start") == sources[source_id].get("period_start")
                and record.get("end") == sources[source_id].get("period_end")
                for source_id in record["source_ids"] if isinstance(source_id, str)
            )
        ]
        if annual_source_ids and matching_records:
            continue
        reason = missing.get(year)
        if isinstance(reason, dict) and _text(reason.get("reason")):
            scope_path = "coverage.missing_annual_reasons." + year
            scope_obj = mapping(reason["scope"], scope_path + ".scope") if "scope" in reason else reason
            scope = sequence(scope_obj.get("searched_channels"), scope_path + ".scope.searched_channels", True)
            date(reason.get("searched_at"), scope_path + ".searched_at", as_of)
            if "fiscal_year" in scope_obj and scope_obj["fiscal_year"] != year:
                fail(scope_path + ".scope.fiscal_year", "检索范围财年必须匹配缺口财年")
            if "report_type" in scope_obj and scope_obj["report_type"] != "annual":
                fail(scope_path + ".scope.report_type", "缺口检索必须核实 annual 原文")
            if any(not _text(channel) for channel in scope):
                fail(scope_path, "检索范围必须明确")
            warnings.append(year + " 原文/记录缺口（须人工核实未公开结论）：" + reason["reason"])
        else:
            fail("coverage.annual_periods." + year, "缺少已读 annual 原文和对应 annual record；未公开须明确原因、检索时间与渠道")

    interim_ids = sequence(coverage.get("interim_report_ids"), "coverage.interim_report_ids")
    references(interim_ids, "coverage.interim_report_ids")
    valid_interim_ids = [source_id for source_id in interim_ids if _text(source_id)]
    for source_id, source in sources.items():
        if source.get("fiscal_year") != entity.get("target_fiscal_year") or source.get("report_type") != "interim":
            continue
        try:
            published = dt.date.fromisoformat(source.get("published_at", ""))
        except (TypeError, ValueError):
            continue
        if as_of and published <= as_of and source_id not in valid_interim_ids:
            fail("coverage.interim_report_ids", "遗漏当年已公布 interim 来源 " + source_id)
    for source_id in valid_interim_ids:
        source = sources.get(source_id)
        if source is None:
            continue
        if source.get("fiscal_year") != entity.get("target_fiscal_year") or source.get("report_type") != "interim":
            fail("coverage.interim_report_ids", "必须为 target_fiscal_year 的 interim 来源：" + source_id)
        matching = [
            record for record in records
            if record.get("fiscal_year") == entity.get("target_fiscal_year")
            and _enum(record.get("kind"), {"ytd", "quarter"})
            and isinstance(record.get("source_ids"), list) and source_id in record["source_ids"]
            and record.get("end") == source.get("period_end")
        ]
        if not matching:
            fail("coverage.interim_report_ids", "来源无对应当年 record：" + source_id)
    if not interim_ids:
        required_text(coverage, "no_interims_reason", "coverage")
        if not searched or not channels:
            fail("coverage", "当年无中报必须给出检索渠道、时间和 no_interims_reason")
        warnings.append("当年无 interim 的声明须人工复核全部披露渠道。")
    if "latest_quarter" in data:
        warnings.append("latest_quarter 为旧格式，不计入任何 v3 覆盖检查。")

    inventory_raw = data.get("connector_inventory", [])
    inventory = sequence(inventory_raw, "connector_inventory")
    used_inventory = {}
    names = set()
    for index, raw in enumerate(inventory):
        path = "connector_inventory[" + str(index) + "]"
        item = mapping(raw, path)
        required_text(item, "name", path)
        required_text(item, "status", path)
        for key in ("enabled", "tools_read"):
            if not isinstance(item.get(key), bool):
                fail(path + "." + key, "必须为 bool")
        name = item.get("name")
        if _text(name):
            if name in names:
                fail(path + ".name", "连接器名称重复")
            names.add(name)
        tools = sequence(item.get("used_tools"), path + ".used_tools")
        if any(not _text(tool) for tool in tools):
            fail(path + ".used_tools", "必须填写实际使用的工具名称")
        if tools:
            if _text(name):
                used_inventory[name] = item
            if item.get("status") != "connected" or item.get("enabled") is not True or item.get("tools_read") is not True:
                fail(path, "被使用连接器必须 connected、enabled=true、tools_read=true")
    for source_id, source in sources.items():
        if source.get("kind") == "connector":
            if not used_inventory:
                fail("sources." + source_id, "connector 来源必须有实际使用的 connector_inventory")
            connector_name = source.get("connector_name")
            if connector_name is not None and (not _text(connector_name) or connector_name not in used_inventory):
                fail("sources." + source_id + ".connector_name", "来源连接器不在已使用清单内")
    if used_inventory:
        warnings.append("连接器实际工具名称、阅读行为及来源归属仍须人工复核。")

    markets = sequence(data.get("markets"), "markets", True)
    market_codes = set()
    for index, raw in enumerate(markets):
        path = "markets[" + str(index) + "]"
        market = mapping(raw, path)
        for key in ("code", "currency"):
            required_text(market, key, path)
        currency = market.get("currency")
        if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
            fail(path + ".currency", "币种必须为三位大写字母")
        code = market.get("code")
        if _text(code):
            if code in market_codes:
                fail(path + ".code", "市场代码重复")
            market_codes.add(code)
        close = date(market.get("close_date"), path + ".close_date", as_of)
        if market.get("price_state") != "close":
            fail(path + ".price_state", "必须声明 close，不能使用盘中或未来行情")
        underlying = market.get("underlying_per_quote")
        if not _number(underlying) or underlying <= 0:
            fail(path + ".underlying_per_quote", "必须为大于零的有限数字")
        price = cell(market.get("price"), path + ".price", "price", period_end=close, require_as_of=True, expected_currency=currency)
        fx = cell(market.get("fx_to_cny"), path + ".fx_to_cny", "ratio", require_as_of=True)
        shares = cell(market.get("venue_shares"), path + ".venue_shares", "shares")
        if close and price.get("as_of") != market.get("close_date"):
            fail(path + ".price.as_of", "价格日期必须等于 close_date")
        for key, item in (("price", price), ("fx_to_cny", fx), ("venue_shares", shares)):
            if item.get("status") == "ok" and _number(item.get("value")) and item["value"] <= 0:
                fail(path + "." + key + ".value", "价格、汇率和市场股数必须大于零")

    # 生产模式强制同行对照层；测试夹具保持宽松，避免影响既有合成回归。
    if not allow_test:
        peer_list = sequence(data.get("peers", []), "peers", True)
        peer_ids = set()
        core_count = 0
        for index, raw in enumerate(peer_list):
            path = "peers[" + str(index) + "]"
            peer = mapping(raw, path)
            for key in ("id", "name", "code", "listing", "business_model"):
                required_text(peer, key, path)
            peer_id = peer.get("id")
            if _text(peer_id):
                if peer_id in peer_ids:
                    fail(path + ".id", "同行 id 重复")
                peer_ids.add(peer_id)
            if not _enum(peer.get("relevance"), {"core", "reference"}):
                fail(path + ".relevance", "必须为 core/reference")
            if peer.get("relevance") == "core":
                core_count += 1
            references(peer.get("source_ids"), path + ".source_ids", True)
            cell(peer.get("core_business_weight"), path + ".core_business_weight", "percent")
            periods = sequence(peer.get("periods"), path + ".periods", True)
            for number, raw_period in enumerate(periods):
                ppath = path + ".periods[" + str(number) + "]"
                per = mapping(raw_period, ppath)
                required_text(per, "label", ppath)
                start = date(per.get("start"), ppath + ".start", as_of)
                end = date(per.get("end"), ppath + ".end", as_of)
                if start and end and start > end:
                    fail(ppath, "start 晚于 end")
                for key, item in mapping(per.get("metrics"), ppath + ".metrics").items():
                    metric_cell = cell(item, ppath + ".metrics." + str(key))
                    if metric_cell.get("unit") == "currency" and metric_cell.get("currency") != "CNY":
                        fail(ppath + ".metrics." + str(key), "同行金额须先折算为 CNY")
        if core_count < 3:
            fail("peers", "核心可比同行至少 3 家，当前 " + str(core_count) + " 家")
        required_sections = {"4.1", "4.2", "4.3", "4.4", "4.5", "4.6", "4.7"}
        sections = sequence(coverage.get("peer_comparison_sections"), "coverage.peer_comparison_sections", True)
        seen = set()
        for index, raw in enumerate(sections):
            path = "coverage.peer_comparison_sections[" + str(index) + "]"
            item = mapping(raw, path)
            name = item.get("section")
            if not _text(name) or name not in required_sections:
                fail(path + ".section", "必须为 4.1-4.7 之一")
                continue
            if name in seen:
                fail(path + ".section", "章节重复")
            seen.add(name)
            if not _enum(item.get("status"), STATUSES - {"not_calculable"}):
                fail(path + ".status", "状态不合法")
            if item.get("status") != "ok":
                required_text(item, "reason", path)
            else:
                for peer_id in sequence(item.get("peer_ids"), path + ".peer_ids", True):
                    if not _text(peer_id) or peer_id not in peer_ids:
                        fail(path + ".peer_ids", "引用了未定义的同行 id")
        for missing in sorted(required_sections - seen):
            fail("coverage.peer_comparison_sections", "缺少模块四同行对照章节 " + missing)
        warnings.append("同行是否真的可比、财年/币种对齐与解读是否引用同行数字，机器无法判断，须人工复核。")

    # 扩展 CELL 同样受来源、日期和非有限数约束，避免新增字段绕过证据审计。
    def extra_cells(value, path):
        if isinstance(value, dict):
            if "value" in value or "status" in value and "unit" in value:
                if id(value) not in validated_cells:
                    cell(value, path)
                return
            for key, child in value.items():
                extra_cells(child, path + "." + str(key))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                extra_cells(child, path + "[" + str(index) + "]")
    extra_cells(data, "input")

    if html is not None:
        if not isinstance(html, str):
            fail("HTML", "html 参数必须为 HTML 文本字符串")
        else:
            if re.search(r"\{\{[\s\S]*?\}\}", html):
                fail("HTML", "禁止未替换的 {{...}} 占位符")
            parser = _OfflineHTML()
            try:
                parser.feed(html)
                parser.close()
                errors.extend(parser.finish())
            except (ValueError, OverflowError, RecursionError) as exc:
                fail("HTML", "解析失败：" + str(exc))
            warnings.append("HTML 语义、页面填充完成度、CSS 隐藏规则、内联脚本网络行为与结论一致性须人工质检；未填版式模板不得交付，静态检查不证明内容完整或执行脚本。")
    return {"ok": not errors, "errors": errors, "warnings": warnings}


def _reject_constant(value):
    raise ValueError("禁止 JSON " + value)


def main(argv=None):
    parser = argparse.ArgumentParser(description="审计 v3 财务证据与离线 HTML，不联网。")
    parser.add_argument("input", help="v3 证据 JSON 文件")
    parser.add_argument("--html", help="可选离线 HTML 文件")
    parser.add_argument("--allow-test", action="store_true", help="仅独立测试允许合成 test_fixture；生产禁止使用")
    args = parser.parse_args(argv)
    try:
        data = json.loads(Path(args.input).read_text(encoding="utf-8-sig"), parse_constant=_reject_constant)
        html = Path(args.html).read_text(encoding="utf-8-sig") if args.html else None
        result = audit(data, html, args.allow_test)
    except (OSError, UnicodeError, ValueError, RecursionError) as exc:
        result = {"ok": False, "errors": ["读取或解析失败：" + str(exc)], "warnings": [HUMAN_REVIEW]}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
