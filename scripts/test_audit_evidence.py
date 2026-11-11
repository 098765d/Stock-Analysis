#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全部数值均为明确 test_fixture 合成数据，不对应真实公司或行情。"""

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

_SPEC = importlib.util.spec_from_file_location("audit_evidence_under_test", Path(__file__).with_name("audit_evidence.py"))
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
audit = _MODULE.audit
CORE_METRICS = _MODULE.CORE_METRICS


def fixture_cell(source_id, unit="currency", as_of=None, value=10):
    result = {
        "value": value, "status": "ok", "unit": unit,
        "source_ids": [source_id], "locator": "test_fixture JSON $.synthetic.value",
    }
    if unit in {"currency", "price"}:
        result["currency"] = "CNY"
    if as_of:
        result["as_of"] = as_of
    return result


def fixture_source(source_id, year, start, end, published, report_type):
    return {
        "id": source_id, "kind": "test_fixture", "title": "显式合成测试来源 " + source_id,
        "url": "test_fixture://" + source_id, "period_start": start, "period_end": end,
        "published_at": published, "opened": True, "read": True,
        "report_type": report_type, "fiscal_year": year,
    }


def fixture_record(source, kind):
    return {
        "id": "record_" + source["id"], "label": "合成测试记录 " + source["id"],
        "kind": kind, "start": source["period_start"], "end": source["period_end"],
        "fiscal_year": source["fiscal_year"], "source_ids": [source["id"]],
        "metrics": {key: fixture_cell(source["id"]) for key in CORE_METRICS},
    }


def fixture_strategy(anchor_source_id="annual_2025"):
    return {
        "api_fields": ["五年营收/成本/营业利润/归母/扣非/OCF/资产/存货/应收/应付/合同负债", "各地收盘价与汇率"],
        "document_fields": ["分部拆分"],
        "anchor_check": {
            "performed": True, "source_ids": [anchor_source_id], "fields_verified": 5,
            "result": "test_fixture 合成核对：接口与原表五个核心科目一致", "mismatches": [],
        },
    }


def fixture_data():
    data = {
        "schema_version": 3, "analysis_as_of": "2026-11-10",
        "entity": {"name": "纯合成测试主体", "primary_code": "TEST.FIXTURE", "target_fiscal_year": "FY2026", "fiscal_year_start": "01-01", "total_shares": fixture_cell("quote_fixture", "shares")},
        "coverage": {"annual_periods": ["FY" + str(year) for year in range(2021, 2026)], "interim_report_ids": ["interim_q1", "interim_q2", "interim_q3"], "searched_at": "2026-11-10", "searched_channels": ["test_fixture 合成公告目录"]},
        "sources": [], "records": [], "connector_inventory": [],
        "markets": [{"code": "TEST.FIXTURE", "currency": "CNY", "close_date": "2026-11-10", "price_state": "close", "underlying_per_quote": 1, "price": fixture_cell("quote_fixture", "price", "2026-11-10"), "fx_to_cny": fixture_cell("quote_fixture", "ratio", "2026-11-10", 1), "venue_shares": fixture_cell("quote_fixture", "shares")}],
    }
    for year in range(2021, 2026):
        source = fixture_source("annual_" + str(year), "FY" + str(year), str(year) + "-01-01", str(year) + "-12-31", str(year + 1) + "-03-31", "annual")
        data["sources"].append(source)
        data["records"].append(fixture_record(source, "annual"))
    for quarter, end, published in ((1, "2026-03-31", "2026-04-20"), (2, "2026-06-30", "2026-08-20"), (3, "2026-09-30", "2026-10-20")):
        source = fixture_source("interim_q" + str(quarter), "FY2026", "2026-01-01", end, published, "interim")
        data["sources"].append(source)
        data["records"].append(fixture_record(source, "ytd"))
    data["sources"].append(fixture_source("quote_fixture", "FY2026", "2026-11-10", "2026-11-10", "2026-11-10", "announcement"))
    data["coverage"]["evidence_mode"] = "api_first"
    data["coverage"]["source_strategy"] = fixture_strategy()
    return data


def fixture_html():
    return "<!doctype html><html><head><style>body{color:#111}</style></head><body>" + "".join(
        '<section id="module' + str(i) + '"><p class="interpretation">合成测试解释 ' + str(i) + '</p></section>'
        for i in range(1, 6)
    ) + "".join('<section id="' + element_id + '">合成测试文本</section>' for element_id in ("summary", "risks", "watchlist", "sources")) + '<p>免责声明：合成数据仅用于测试，不构成投资建议。</p></body></html>'


class EvidenceAuditTests(unittest.TestCase):
    def setUp(self):
        self.data = fixture_data()

    def assert_invalid(self, fragment=None, html=None):
        result = audit(self.data, html, allow_test=True)
        self.assertFalse(result["ok"], result)
        if fragment:
            self.assertTrue(any(fragment in error for error in result["errors"]), result)
        return result

    def test_complete_synthetic_fixture_passes_only_in_test_mode(self):
        result = audit(self.data, fixture_html(), allow_test=True)
        self.assertTrue(result["ok"], result)
        self.assertTrue(any("机器无法验证真实阅读" in item for item in result["warnings"]))

    def test_production_rejects_test_fixture(self):
        result = audit(self.data)
        self.assertFalse(result["ok"])
        self.assertTrue(any("生产审计禁止 test_fixture" in item for item in result["errors"]))

    def test_only_latest_period_fails(self):
        self.data["records"] = self.data["records"][-1:]
        self.data["coverage"]["annual_periods"] = ["FY2025"]
        self.data["coverage"]["interim_report_ids"] = ["interim_q3"]
        self.data["latest_quarter"] = {"period": "2026Q3"}
        self.assert_invalid("最近五个")

    def test_omitted_current_interim_fails_reverse_check(self):
        self.data["coverage"]["interim_report_ids"].remove("interim_q2")
        self.assert_invalid("遗漏当年已公布 interim 来源 interim_q2")

    def test_current_interim_without_record_fails(self):
        self.data["records"] = [record for record in self.data["records"] if record["id"] != "record_interim_q2"]
        self.assert_invalid("来源无对应当年 record")

    def test_unread_source_fails(self):
        self.data["sources"][0]["read"] = False
        self.assert_invalid("opened/read")

    def test_unopened_source_fails(self):
        self.data["sources"][0]["opened"] = False
        self.assert_invalid("opened/read")

    def test_missing_source_fails(self):
        self.data["records"][0]["metrics"]["rev"]["source_ids"] = ["missing_fixture"]
        self.assert_invalid("来源不存在")

    def test_missing_source_list_fails(self):
        self.data["records"][0]["metrics"]["rev"]["source_ids"] = []
        self.assert_invalid("不得为空")

    def test_future_source_fails(self):
        self.data["sources"][0]["published_at"] = "2027-01-01"
        self.assert_invalid("日期晚于 analysis_as_of")

    def test_future_record_fails(self):
        self.data["records"][0]["end"] = "2027-12-31"
        self.assert_invalid("日期晚于 analysis_as_of")

    def test_future_cell_fails(self):
        self.data["entity"]["total_shares"]["as_of"] = "2027-01-01"
        self.assert_invalid("日期晚于 analysis_as_of")

    def test_future_quote_fails(self):
        self.data["markets"][0]["close_date"] = "2026-11-11"
        self.assert_invalid("日期晚于 analysis_as_of")

    def test_missing_status_must_not_contain_zero(self):
        self.data["records"][0]["metrics"]["rev"].update(status="not_disclosed", value=0, reason="合成缺失声明")
        self.assert_invalid("非 ok 状态必须为 null")

    def test_missing_status_must_not_contain_other_value(self):
        self.data["records"][0]["metrics"]["rev"].update(status="unavailable", value=99, reason="合成缺失声明")
        self.assert_invalid("非 ok 状态必须为 null")

    def test_missing_status_requires_reason(self):
        self.data["records"][0]["metrics"]["rev"].update(status="not_disclosed", value=None)
        self.assert_invalid("reason")

    def test_explicit_missing_cell_allowed(self):
        self.data["records"][0]["metrics"]["capex"].update(status="not_disclosed", value=None, reason="test_fixture 明确未披露", source_ids=[], locator="")
        self.assertTrue(audit(self.data, allow_test=True)["ok"])

    def test_core_metric_key_required(self):
        del self.data["records"][-1]["metrics"]["prepay"]
        self.assert_invalid("核心字段必须出现")

    def test_currency_unit_required(self):
        self.data["records"][0]["metrics"]["rev"]["unit"] = "shares"
        self.assert_invalid("必须使用 currency")

    def test_price_unit_required(self):
        self.data["markets"][0]["price"]["unit"] = "currency"
        self.assert_invalid("必须使用 price")

    def test_currency_code_required(self):
        del self.data["records"][0]["metrics"]["rev"]["currency"]
        self.assert_invalid("三位大写币种")

    def test_nan_and_infinity_fails(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=repr(value)):
                self.data["records"][0]["metrics"]["rev"]["value"] = value
                self.assert_invalid("NaN/Infinity")

    def test_bool_is_not_a_number(self):
        self.data["records"][0]["metrics"]["rev"]["value"] = True
        self.assert_invalid("bool 不算数值")

    def test_locator_required(self):
        self.data["records"][0]["metrics"]["rev"]["locator"] = "待核实"
        self.assert_invalid("明确 PDF")

    def test_five_annuals_must_be_contiguous(self):
        self.data["coverage"]["annual_periods"][0] = "FY2020"
        self.assert_invalid("连续")

    def test_missing_annual_source_and_record_fails(self):
        self.data["sources"] = [source for source in self.data["sources"] if source["id"] != "annual_2021"]
        self.data["records"] = [record for record in self.data["records"] if record["id"] != "record_annual_2021"]
        self.assert_invalid("缺少已读 annual 来源")

    def test_verified_unpublished_annual_is_explicit_limitation(self):
        self.data["sources"] = [source for source in self.data["sources"] if source["id"] != "annual_2021"]
        self.data["records"] = [record for record in self.data["records"] if record["id"] != "record_annual_2021"]
        self.data["coverage"]["missing_annual_reasons"] = {"FY2021": {"reason": "test_fixture 合成渠道核实该年度未公开", "searched_channels": ["test_fixture 年报存档范围"], "searched_at": "2026-11-10"}}
        result = audit(self.data, allow_test=True)
        self.assertTrue(result["ok"], result)
        self.assertTrue(any("FY2021 年度来源/记录缺口" in warning for warning in result["warnings"]))

    def test_no_interims_requires_reason(self):
        self.data["sources"] = [source for source in self.data["sources"] if source["report_type"] != "interim"]
        self.data["records"] = [record for record in self.data["records"] if record["kind"] == "annual"]
        self.data["coverage"]["interim_report_ids"] = []
        self.assert_invalid("no_interims_reason")
        self.data["coverage"]["no_interims_reason"] = "test_fixture 合成渠道尚无当年披露"
        self.assertTrue(audit(self.data, allow_test=True)["ok"])

    def test_connector_disabled_fails(self):
        self.data["connector_inventory"] = [{"name": "synthetic_connector", "status": "connected", "enabled": False, "tools_read": True, "used_tools": ["test_fixture_tool"]}]
        self.assert_invalid("被使用连接器必须")

    def test_connector_used_requires_connected_and_tools_read(self):
        for key, value in (("status", "disconnected"), ("tools_read", False)):
            with self.subTest(key=key):
                self.data["connector_inventory"] = [{"name": "synthetic_connector", "status": "connected", "enabled": True, "tools_read": True, "used_tools": ["test_fixture_tool"]}]
                self.data["connector_inventory"][0][key] = value
                self.assert_invalid("被使用连接器必须")

    def test_unused_connector_does_not_need_authorization(self):
        self.data["connector_inventory"] = [{"name": "unused_fixture", "status": "disconnected", "enabled": False, "tools_read": False, "used_tools": []}]
        self.assertTrue(audit(self.data, allow_test=True)["ok"])

    def test_unassociated_connector_source_fails(self):
        self.data["sources"][-1]["kind"] = "connector"
        self.assert_invalid("实际使用的 connector_inventory")

    def test_core_financial_cell_cannot_only_reference_connector(self):
        self.data["coverage"]["evidence_mode"] = "document_first"
        self.data["records"][0]["metrics"]["rev"]["source_ids"] = ["quote_fixture"]
        self.data["sources"][-1]["kind"] = "public"
        self.assert_invalid("statutory_report 原文")

    def test_api_first_allows_anchored_connector_core_metric(self):
        self.data["coverage"]["source_strategy"] = fixture_strategy()
        self.data["records"][0]["metrics"]["rev"]["source_ids"] = ["quote_fixture"]
        self.data["sources"][-1]["kind"] = "public"
        self.assertTrue(audit(self.data, allow_test=True)["ok"])

    def test_api_first_requires_anchor_check(self):
        del self.data["coverage"]["source_strategy"]["anchor_check"]
        self.assert_invalid("锚定核对")

    def test_api_first_rejects_unperformed_anchor(self):
        self.data["coverage"]["source_strategy"]["anchor_check"]["performed"] = False
        self.assert_invalid("performed")

    def test_api_first_anchor_must_cite_statutory_report(self):
        self.data["coverage"]["source_strategy"]["anchor_check"]["source_ids"] = ["quote_fixture"]
        self.data["sources"][-1]["kind"] = "public"
        self.assert_invalid("不能用接口自证")

    def test_api_first_anchor_fields_verified_must_be_positive_integer(self):
        for value in (0, True, "5", None):
            with self.subTest(value=value):
                self.data["coverage"]["source_strategy"] = fixture_strategy()
                self.data["coverage"]["source_strategy"]["anchor_check"]["fields_verified"] = value
                self.assert_invalid("fields_verified")

    def test_api_first_mismatch_requires_resolution(self):
        self.data["coverage"]["source_strategy"]["anchor_check"]["mismatches"] = [
            {"field": "gross_profit", "interface_value": "100", "document_value": "98", "resolution": "采用原文值"}
        ]
        result = audit(self.data, allow_test=True)
        self.assertTrue(result["ok"], result)
        self.assertTrue(any("处差异" in item for item in result["warnings"]))
        del self.data["coverage"]["source_strategy"]["anchor_check"]["mismatches"][0]["resolution"]
        self.assert_invalid("resolution")

    def test_api_first_requires_api_fields(self):
        self.data["coverage"]["source_strategy"]["api_fields"] = []
        self.assert_invalid("api_fields")

    def test_document_first_mode_keeps_statutory_requirement(self):
        self.data["coverage"]["evidence_mode"] = "document_first"
        del self.data["coverage"]["source_strategy"]
        self.data["records"][0]["metrics"]["rev"]["source_ids"] = ["quote_fixture"]
        self.data["sources"][-1]["kind"] = "public"
        self.assert_invalid("statutory_report 原文")

    def test_unknown_evidence_mode_fails(self):
        self.data["coverage"]["evidence_mode"] = "fast_mode"
        self.assert_invalid("evidence_mode")

    def test_quote_requires_closed_price_and_dates(self):
        self.data["markets"][0]["price_state"] = "intraday"
        self.assert_invalid("必须声明 close")
        self.data["markets"][0]["price_state"] = "close"
        del self.data["markets"][0]["fx_to_cny"]["as_of"]
        self.assert_invalid("fx_to_cny.as_of")

    def test_underlying_quote_ratio_must_be_positive(self):
        for value in (0, -1, True):
            with self.subTest(value=value):
                self.data["markets"][0]["underlying_per_quote"] = value
                self.assert_invalid("大于零的有限数字")

    def test_html_external_and_relative_resources_fail(self):
        resources = (
            '<script src="https://example.invalid/test_fixture.js"></script>',
            '<link rel="stylesheet" href="//example.invalid/test_fixture.css">',
            '<img src="file:///test_fixture.png">', '<img src="./test_fixture.png">',
            '<object data="test_fixture.html"></object>', '<iframe src="https://example.invalid/test_fixture"></iframe>',
            '<audio src="test_fixture.mp3"></audio>', '<video poster="test_fixture.png"></video>',
            '<source srcset="test_fixture.png 1x, test_fixture2.png 2x">',
            '<style>p{background:url(https://example.invalid/test_fixture.png)}</style>',
            '<style>@import "test_fixture.css";</style>',
        )
        for resource in resources:
            with self.subTest(resource=resource):
                self.assert_invalid("离线报告禁止", fixture_html().replace("</body>", resource + "</body>"))

    def test_html_navigation_source_links_allowed(self):
        html = fixture_html().replace("</body>", '<a href="https://example.invalid/test_fixture">来源原文测试链接</a></body>')
        self.assertTrue(audit(self.data, html, allow_test=True)["ok"])

    def test_html_data_images_and_inline_svg_allowed(self):
        html = fixture_html().replace("</body>", '<img src="data:image/png;base64,AAAA"><svg><defs><path id="synthetic_shape" d="M0 0"/></defs><use href="#synthetic_shape"/></svg></body>')
        self.assertTrue(audit(self.data, html, allow_test=True)["ok"])

    def test_html_missing_interpretation_fails(self):
        self.assert_invalid("module3 缺少非空", fixture_html().replace('class="interpretation">合成测试解释 3', 'class="other">合成测试解释 3'))

    def test_html_hidden_or_empty_interpretation_fails(self):
        for replacement in ('class="interpretation" hidden>合成测试解释 3', 'class="interpretation">   '):
            with self.subTest(replacement=replacement):
                self.assert_invalid("module3 缺少非空", fixture_html().replace('class="interpretation">合成测试解释 3', replacement))

    def test_html_required_ids_and_disclaimer(self):
        self.assert_invalid("id=watchlist", fixture_html().replace('id="watchlist"', 'id="other"'))
        self.assert_invalid("免责声明", fixture_html().replace("免责声明", "其它说明"))

    def test_html_placeholders_fail(self):
        self.assert_invalid("占位符", fixture_html().replace("</body>", "{{test_fixture}}</body>"))

    def test_html_forbidden_visible_terms_fail(self):
        for term in ("边际毛利率", "A+H总市值", "A+H 总市值", "A + H 总市值", "A＋H市值合计"):
            with self.subTest(term=term):
                self.assert_invalid("禁止", fixture_html().replace("</body>", "<p>" + term + "</p></body>"))

    def test_html_script_and_comment_text_is_not_visible(self):
        html = fixture_html().replace("</body>", '<!-- 边际毛利率 --><script>const test_fixture="A+H总市值";</script></body>')
        self.assertTrue(audit(self.data, html, allow_test=True)["ok"])

    def test_additional_cells_do_not_bypass_validation(self):
        self.data["extra"] = {"some_metric": fixture_cell("absent_fixture")}
        self.assert_invalid("来源不存在 absent_fixture")

    def test_current_interim_cannot_reuse_wrong_period_record(self):
        self.data["records"] = [record for record in self.data["records"] if record["id"] != "record_interim_q2"]
        self.data["records"][-1]["source_ids"].append("interim_q2")
        self.assert_invalid("来源无对应当年 record")

    def test_annual_record_must_match_source_period(self):
        self.data["records"][0]["start"] = "2021-02-01"
        self.assert_invalid("缺少已读 annual 来源")

    def test_nested_extra_cells_are_audited(self):
        self.data["entity"]["extra_fixture"] = fixture_cell("absent_fixture")
        self.assert_invalid("来源不存在 absent_fixture")

    def test_data_srcset_cannot_hide_relative_resource(self):
        self.assert_invalid("离线报告禁止", fixture_html().replace("</body>", '<img srcset="data:image/png;base64,AAAA 1x, assets.png 2x"></body>'))

    def test_enum_arrays_report_errors_instead_of_crashing(self):
        for section, key in (("sources", "kind"), ("sources", "report_type"), ("records", "kind")):
            with self.subTest(section=section, key=key):
                data = fixture_data()
                data[section][0][key] = ["malformed_fixture"]
                self.assertFalse(audit(data, allow_test=True)["ok"])
        for key in ("status", "unit"):
            with self.subTest(cell_key=key):
                data = fixture_data()
                data["records"][0]["metrics"]["rev"][key] = ["malformed_fixture"]
                self.assertFalse(audit(data, allow_test=True)["ok"])

    def test_schema_version_must_be_integer_three(self):
        for value in (None, 2, "3", 3.0, True):
            with self.subTest(version=value):
                self.data["schema_version"] = value
                self.assert_invalid("schema_version")

    def test_records_financial_amounts_must_be_normalized_cny(self):
        self.data["records"][0]["metrics"]["rev"]["currency"] = "USD"
        self.assert_invalid("本字段币种必须为 CNY")

    def test_financials_are_currency_cny_cells(self):
        self.data["financials"] = {"ttm_parent": fixture_cell("annual_2025")}
        self.assertTrue(audit(self.data, allow_test=True)["ok"])
        self.data["financials"]["ttm_parent"]["unit"] = "shares"
        self.assert_invalid("必须使用 currency")
        self.data["financials"]["ttm_parent"]["unit"] = "currency"
        self.data["financials"]["ttm_parent"]["currency"] = "USD"
        self.assert_invalid("本字段币种必须为 CNY")

    def test_structured_annual_missing_scope(self):
        self.data["sources"] = [source for source in self.data["sources"] if source["id"] != "annual_2021"]
        self.data["records"] = [record for record in self.data["records"] if record["id"] != "record_annual_2021"]
        self.data["coverage"]["missing_annual_reasons"] = {"FY2021": {
            "reason": "test_fixture 核实指定存档未公开", "searched_at": "2026-11-10",
            "scope": {"fiscal_year": "FY2021", "report_type": "annual", "searched_channels": ["test_fixture 年报存档"]},
        }}
        self.assertTrue(audit(self.data, allow_test=True)["ok"])
        self.data["coverage"]["missing_annual_reasons"]["FY2021"]["scope"]["fiscal_year"] = "FY2022"
        self.assert_invalid("检索范围财年必须匹配")

    def test_entity_total_shares_must_be_positive(self):
        for value in (0, -10):
            with self.subTest(total_shares=value):
                self.data["entity"]["total_shares"]["value"] = value
                self.assert_invalid("已披露总股数必须大于零")

    def test_production_rejects_masquerading_test_fixture_url(self):
        for source in self.data["sources"]:
            source["kind"] = "statutory_report" if source["report_type"] != "announcement" else "public"
        result = audit(self.data)
        self.assertFalse(result["ok"])
        self.assertTrue(any("生产审计禁止 test_fixture://" in error for error in result["errors"]), result)

    def test_future_forecast_metadata_does_not_mean_future_observation(self):
        self.data["financials"] = {"forward_parent": fixture_cell("quote_fixture")}
        self.data["financials"]["forward_parent"]["forecast_period"] = {"start": "2027-01-01", "end": "2027-12-31", "fiscal_year": "FY2027"}
        self.data["financials"]["forward_parent"]["as_of"] = "2026-11-10"
        self.assertTrue(audit(self.data, allow_test=True)["ok"])

    def test_html_completion_and_unfilled_template_require_human_review(self):
        result = audit(self.data, fixture_html(), allow_test=True)
        self.assertTrue(result["ok"])
        self.assertTrue(any("未填版式模板不得交付" in warning for warning in result["warnings"]))

    def test_malformed_structure_reports_errors(self):
        for data in (None, {}, {"analysis_as_of": "wrong", "sources": "wrong", "records": [None], "coverage": None, "markets": [None]}):
            with self.subTest(data=data):
                self.assertFalse(audit(data, allow_test=True)["ok"])

    def test_cli_exit_codes_and_nan_parse_rejection(self):
        with tempfile.TemporaryDirectory(prefix="audit_test_fixture_") as directory:
            input_path = Path(directory) / "test_fixture.json"
            input_path.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
            script = str(Path(__file__).with_name("audit_evidence.py"))
            success = subprocess.run([sys.executable, "-B", script, str(input_path), "--allow-test"], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(success.returncode, 0, success.stderr + success.stdout)
            production = subprocess.run([sys.executable, "-B", script, str(input_path)], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(production.returncode, 2)
            broken = copy.deepcopy(self.data)
            broken["extra_nan"] = float("nan")
            input_path.write_text(json.dumps(broken), encoding="utf-8")
            rejected = subprocess.run([sys.executable, "-B", script, str(input_path), "--allow-test"], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(rejected.returncode, 2)
            self.assertIn("NaN", rejected.stdout)


if __name__ == "__main__":
    unittest.main()
