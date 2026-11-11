#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Synthetic fixtures only. These values are not real company financials or prices."""
import copy
import json
import unittest
from pathlib import Path
import calc_metrics as cm
from audit_evidence import audit
from test_audit_evidence import fixture_data, fixture_html


def cell(v, unit='currency', currency='CNY', as_of=None):
    c = {'value': v, 'status': 'ok', 'unit': unit, 'source_ids': ['quote_fixture'],
         'locator': 'test_fixture $.synthetic', 'currency': currency}
    if as_of:
        c['as_of'] = as_of
    return c


def rec(name, start, end, kind, revenue, cost, profit=10):
    return {'id': name, 'label': name+' synthetic', 'fiscal_year': 'FY'+end[:4],
            'start': start, 'end': end, 'kind': kind, 'source_ids': ['quote_fixture'],
            'metrics': {'rev': cell(revenue), 'cost': cell(cost), 'op': cell(profit),
                        'np': cell(profit), 'parent': cell(profit), 'ocf': cell(20), 'capex': cell(10)}}


def base():
    return {'schema_version': 3, 'analysis_as_of': '2026-11-10',
            'entity': {'total_shares': cell(100, 'shares')}, 'records': [], 'markets': [],
            'sources': [{'id': 'quote_fixture', 'kind': 'test_fixture'}]}


class CalcTests(unittest.TestCase):
    def test_old_schema_blocked(self):
        with self.assertRaisesRegex(ValueError, 'v3 required'):
            cm.compute({'years': {}, 'latest_quarter': {}})

    def test_finite_and_missing_rules(self):
        for v in (True, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                cm.val(cell(v))
        with self.assertRaises(ValueError):
            cm.val({'status': 'not_disclosed', 'value': 0, 'reason': 'fixture'})
        missing = {'status': 'not_disclosed', 'value': None, 'reason': 'fixture'}
        self.assertIsNone(cm.val(missing))
        self.assertEqual(cm.val(cell(0)), 0)

    def test_normalized_currency_required(self):
        d = base(); d['records'] = [rec('q1', '2026-01-01', '2026-03-31', 'quarter', 100, 80)]
        d['records'][0]['metrics']['rev']['currency'] = 'USD'
        with self.assertRaisesRegex(ValueError, 'CNY'):
            cm.compute(d)

    def test_three_margins_and_negative_base(self):
        d = base(); q = rec('q1', '2026-01-01', '2026-03-31', 'quarter', 100, 80, 10)
        p = rec('p1', '2025-01-01', '2025-03-31', 'quarter', 80, 70, -5)
        q['prior_id'] = 'p1'; d['records'] = [p, q]
        out = cm.compute(d)['records'][1]['metrics']
        self.assertEqual(out['gross_margin']['value'], 20)
        self.assertEqual(out['operating_margin']['value'], 10)
        self.assertEqual(out['net_margin']['value'], 10)
        self.assertIsNone(out['np_yoy']['value'])
        self.assertEqual(out['np_change']['value'], 15)

    def test_wrong_yoy_interval_blocked(self):
        d = base(); q = rec('q1', '2026-01-01', '2026-03-31', 'quarter', 100, 80)
        q['prior_id'] = 'p1'; p = rec('p1', '2025-01-01', '2025-06-30', 'ytd', 80, 70)
        d['records'] = [p, q]
        with self.assertRaisesRegex(ValueError, 'same interval'):
            cm.compute(d)

    def test_cumulative_restore_and_qoq(self):
        d = base(); d['records'] = [rec('q1', '2026-01-01', '2026-03-31', 'ytd', 100, 80),
                                    rec('h1', '2026-01-01', '2026-06-30', 'ytd', 250, 185),
                                    rec('m9', '2026-01-01', '2026-09-30', 'ytd', 450, 305)]
        qs = cm.compute(d)['recent_quarters']
        self.assertEqual(len(qs), 3)
        self.assertEqual([q['metrics']['gross_margin']['value'] for q in qs], [20, 30, 40])
        self.assertEqual(qs[2]['metrics']['gross_margin_qoq_pp']['value'], 10)

    def test_no_cross_gap_quarter_restore(self):
        d = base(); d['records'] = [rec('q1', '2026-01-01', '2026-03-31', 'ytd', 100, 80),
                                    rec('m9', '2026-01-01', '2026-09-30', 'ytd', 450, 305)]
        self.assertEqual(len(cm.compute(d)['recent_quarters']), 1)

    def test_first_of_four_keeps_preceding_qoq(self):
        d = base(); d['records'] = [rec('p3','2025-07-01','2025-09-30','quarter',100,90),
                                    rec('p4','2025-10-01','2025-12-31','quarter',100,80),
                                    rec('q1','2026-01-01','2026-03-31','quarter',100,70),
                                    rec('q2','2026-04-01','2026-06-30','quarter',100,60),
                                    rec('q3','2026-07-01','2026-09-30','quarter',100,50)]
        qs = cm.compute(d)['recent_quarters']
        self.assertEqual(len(qs), 4)
        self.assertEqual(qs[0]['metrics']['gross_margin_qoq_pp']['value'], 10)
        self.assertEqual(qs[-1]['metrics']['gross_margin_yoy_pp']['value'], 40)

    def test_ttm_same_fiscal_boundary(self):
        d = base(); d['records'] = [rec('a','2025-01-01','2025-12-31','annual',400,320,40),
                                    rec('p','2025-01-01','2025-09-30','ytd',300,240,30),
                                    rec('c','2026-01-01','2026-09-30','ytd',450,315,45)]
        d['ttm'] = {'annual_id':'a','current_ytd_id':'c','prior_ytd_id':'p'}
        out = cm.compute(d)['ttm']
        self.assertEqual(out['rev']['value'],550)
        self.assertEqual(out['parent']['value'],55)
        self.assertEqual(set(out['rev']['source_ids']), {'quote_fixture'})
        d['records'][0]['end'] = '2025-11-30'
        with self.assertRaises(ValueError):
            cm.compute(d)

    def test_working_days_and_bridge_missing_terms(self):
        d=base(); b=rec('b','2025-01-01','2025-12-31','annual',100,80)
        c=rec('c','2026-01-01','2026-03-31','ytd',100,80)
        for r in (b,c):
            r['metrics'].update(inv=cell(10),ar=cell(10),ap=cell(10),assets=cell(100),contract_liab=cell(2))
        c['begin_id']='b'; d['records']=[b,c]
        out=cm.compute(d)['records'][1]
        self.assertEqual(out['metrics']['dio']['value'],11.25)
        self.assertIn('prepay',out['simplified_cash_bridge']['missing_terms'])
        self.assertIn('unverified',out['simplified_cash_bridge']['unexplained_residual']['formula'])

    def test_market_denominator_not_venue_value(self):
        d=base(); d['financials']={'ttm_parent':cell(100),'ttm_rev':cell(500),'parent_equity':cell(500)}
        def market(code,p,fx,vs,k=1):
            return {'code':code,'currency':'USD','close_date':'2026-11-10','price_state':'close',
                    'underlying_per_quote':k,'has_equal_economic_rights':True,
                    'price':cell(p,'price','USD','2026-11-10'),
                    'fx_to_cny':cell(fx,'ratio',as_of='2026-11-10'),'venue_shares':cell(vs,'shares')}
        d['markets']=[market('TEST.A',10,1,90),market('TEST.H',8,1,10),market('TEST.ADR',16,1,5,2)]
        rows=cm.compute(d)['markets']
        self.assertEqual(rows[0]['metrics']['venue_share_value_cny']['value'],900)
        self.assertEqual(rows[1]['metrics']['venue_share_value_cny']['value'],80)
        self.assertEqual(rows[1]['metrics']['pe_ttm']['value'],8)
        self.assertEqual(rows[2]['metrics']['pe_ttm']['value'],8)
        self.assertAlmostEqual(rows[1]['metrics']['price_vs_first_market_pct']['value'],-20)
        self.assertNotIn('total_mcap', cm.compute(d))

    def test_nonpositive_profit_fcf_peg(self):
        d=base(); d['financials']={'ttm_parent':cell(-5),'ocf_ttm':cell(5),'capex_ttm':cell(10)}
        d['markets']=[{'code':'TEST','currency':'CNY','close_date':'2026-11-10','price_state':'close',
                       'underlying_per_quote':1,'price':cell(10,'price',as_of='2026-11-10'),
                       'fx_to_cny':cell(1,'ratio',as_of='2026-11-10'),'venue_shares':cell(100,'shares'),
                       'peg_growth_cases':{'loss':cell(-10,'percent')}}]
        m=cm.compute(d)['markets'][0]['metrics']
        self.assertIsNone(m['pe_ttm']['value']); self.assertIsNone(m['fcf_yield']['value'])
        self.assertEqual(m['fcf_ttm']['value'],-5); self.assertIsNone(m['peg_cases']['loss']['value'])

    def test_term_spread_needs_matching_dates(self):
        d=base(); d['macro']={'us3m':cell(3,'percent',as_of='2026-11-09'),'us10y':cell(4,'percent',as_of='2026-11-10')}
        self.assertIsNone(cm.compute(d)['us_term_spread_bps']['value'])
        d['macro']['us3m']['as_of']='2026-11-10'
        self.assertEqual(cm.compute(d)['us_term_spread_bps']['value'],100)

    def test_quantiles_and_tie_percentile(self):
        d=base(); d['histories']=[{'code':'TEST','point_in_time':True,
          'observations':[{'date':'2026-11-0'+str(i+1),'pe':cell(v,'ratio')} for i,v in enumerate([10,20,30,40])],
          'current':{'pe':cell(20,'ratio')}}]
        m=cm.compute(d)['histories'][0]['metrics']['pe']
        self.assertEqual(m['q25']['value'],17.5)
        self.assertEqual(m['q50']['value'],25)
        self.assertEqual(m['percentile']['value'],37.5)

    def test_full_evidence_fixture_integration_and_offline_template(self):
        d=fixture_data(); d['schema_version']=3
        self.assertTrue(audit(d, fixture_html(), allow_test=True)['ok'])
        result=cm.compute(d)
        self.assertEqual(len(result['records']),8)
        self.assertEqual(len(result['markets']),1)
        html=Path(__file__).parent.parent.joinpath('assets/report-skeleton.html').read_text(encoding='utf-8')
        checked=audit(d,html,allow_test=True)
        self.assertTrue(checked['ok'],checked)
        self.assertNotIn('cdn.',html); self.assertNotIn('<script',html)
        self.assertNotIn('边际毛利率',json.dumps(result,ensure_ascii=False))


if __name__ == '__main__':
    unittest.main()
