#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v3 evidence-aware deterministic metrics; no network, forecasts or imputed data.
Usage: python calc_metrics.py input.json | python calc_metrics.py --template
Production must additionally pass audit_evidence.py and human source review.
All financial input amounts are normalized CNY yuan; output retains input sources.
"""
import calendar
import json
import math
import sys
from datetime import date, timedelta

FLOW_KEYS = ('rev', 'cost', 'op', 'np', 'parent', 'ded', 'rd', 'ocf', 'capex')
TEMPLATE = {
    'schema_version': 3, 'analysis_as_of': None,
    'entity': {'name': None, 'primary_code': None, 'target_fiscal_year': None,
               'fiscal_year_start': None, 'total_shares': None},
    'coverage': {'evidence_mode': 'api_first',
                 'source_strategy': {'api_fields': [], 'document_fields': [], 'anchor_check': None, 'fallbacks': []},
                 'annual_periods': [], 'interim_report_ids': [], 'peer_comparison_sections': [],
                 'searched_at': None, 'searched_channels': [], 'no_interims_reason': None},
    'sources': [], 'connector_inventory': [], 'records': [], 'financials': {},
    'peers': [], 'peer_comparison_specs': [],
    'markets': [], 'histories': [], 'macro': {}, 'ttm': {},
    '_instructions': 'Read references/input-schema.md. Never replace missing values with 0. '
                     'Schema v2 is incompatible. Tests are synthetic and prohibited in reports.'
}


def parse_date(value):
    return date.fromisoformat(value)


def finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Numeric values must be finite real numbers, not bool/NaN/Infinity')
    return float(value)


def val(cell, unit=None, currency=None):
    if cell is None:
        return None
    if not isinstance(cell, dict):
        raise ValueError('Every input number must be a provenance CELL, not a bare number')
    state = cell.get('status')
    if state not in ('ok', 'not_disclosed', 'unavailable', 'not_applicable', 'not_calculable'):
        raise ValueError('Invalid CELL status')
    if state != 'ok':
        if cell.get('value') is not None or not cell.get('reason'):
            raise ValueError('Missing CELL must have value=null and a reason')
        return None
    if unit and cell.get('unit') != unit:
        raise ValueError('Unexpected unit: expected ' + unit)
    if currency and cell.get('currency') != currency:
        raise ValueError('Normalize financial amounts to ' + currency + ' before computing')
    if not cell.get('source_ids') or not cell.get('locator'):
        raise ValueError('Input CELL requires source_ids and locator')
    return finite(cell.get('value'))


def refs(cells):
    return sorted({sid for c in cells if isinstance(c, dict) for sid in c.get('source_ids', [])})


def output(value, formula, cells=(), unit='ratio', reason=None, **extra):
    ids = refs(cells)
    if value is None:
        result = {'value': None, 'status': 'not_calculable', 'reason': reason or 'Required inputs missing or denominator not positive'}
    else:
        result = {'value': finite(value), 'status': 'ok'}
    result.update(formula=formula, source_ids=ids, unit=unit, computed=True)
    result.update(extra)
    return result


def ratio(a, b, scale=1):
    return a / b * scale if a is not None and b is not None and b > 0 else None


def growth(a, b):
    return (a / b - 1) * 100 if a is not None and b is not None and a >= 0 and b > 0 else None


def cny(metrics, key):
    c = metrics.get(key)
    return val(c, 'currency', 'CNY') if c is not None else None


def prior_year(day):
    return day.replace(year=day.year - 1, day=min(day.day, calendar.monthrange(day.year - 1, day.month)[1]))


def comparable(current, prior):
    return (current['kind'] == prior['kind'] and
            parse_date(prior['start']) == prior_year(parse_date(current['start'])) and
            parse_date(prior['end']) == prior_year(parse_date(current['end'])))


def record_metrics(record, lookup):
    m = record['metrics']
    get = lambda key: cny(m, key)
    values = {k: get(k) for k in FLOW_KEYS}
    r, c = values['rev'], values['cost']
    result = {'id': record['id'], 'label': record['label'], 'kind': record['kind'],
              'start': record['start'], 'end': record['end'], 'metrics': {}}
    out = result['metrics']
    for label, numerator, keys in (
        ('gross_margin', r - c if r is not None and c is not None else None, ('rev', 'cost')),
        ('operating_margin', values['op'], ('op', 'rev')),
        ('net_margin', values['np'], ('np', 'rev')),
        ('parent_margin', values['parent'], ('parent', 'rev')),
        ('rd_intensity', values['rd'], ('rd', 'rev'))):
        formulas = {'gross_margin': '(revenue-cost)/revenue*100',
                    'operating_margin': 'operating profit/revenue*100',
                    'net_margin': 'consolidated net profit/revenue*100',
                    'parent_margin': 'parent profit/revenue*100',
                    'rd_intensity': 'expensed R&D/revenue*100'}
        out[label] = output(ratio(numerator, r, 100), formulas[label],
                            [m.get(k) for k in keys], 'percent')
    p = lookup.get(record.get('prior_id'))
    if p and not comparable(record, p):
        raise ValueError('prior_id must reference the same interval one year earlier: ' + record['id'])
    for k in FLOW_KEYS + ('assets', 'inv', 'ar', 'ap', 'contract_liab', 'prepay', 'liab', 'ib_debt'):
        pc = p['metrics'].get(k) if p else None
        base = val(pc, 'currency', 'CNY') if pc else None
        out[k + '_yoy'] = output(growth(get(k), base), '(current/prior_same_interval-1)*100',
                                 [m.get(k), pc], 'percent', reason='Missing same-interval base or non-positive growth base')
        if p:
            out[k + '_change'] = output(get(k)-base if get(k) is not None and base is not None else None,
                                        'current-prior', [m.get(k), pc], 'currency', currency='CNY')
    np_, ocf, capex = values['np'], values['ocf'], values['capex']
    out['cash_conversion'] = output(ratio(ocf, np_), 'consolidated OCF/consolidated net profit', [m.get('ocf'), m.get('np')])
    out['fcf'] = output(ocf-capex if ocf is not None and capex is not None else None,
                        'OCF-CapEx', [m.get('ocf'), m.get('capex')], 'currency', currency='CNY')
    out['capex_ocf'] = output(ratio(capex, ocf), 'CapEx/OCF', [m.get('ocf'), m.get('capex')])
    for name, a, b, scale in (
        ('debt_assets', 'liab', 'assets', 100), ('interest_debt_equity', 'ib_debt', 'equity', 100),
        ('current_ratio', 'current_assets', 'current_liab', 1),
        ('cash_short_debt', 'cash', 'short_ib_debt', 1)):
        out[name] = output(ratio(get(a), get(b), scale), a + '/' + b, [m.get(a), m.get(b)], 'percent' if scale == 100 else 'ratio')
    ca, inv = get('current_assets'), get('inv')
    out['quick_ratio_simple'] = output(ratio(ca-inv if ca is not None and inv is not None else None, get('current_liab')),
                                        '(current_assets-inventory)/current_liabilities',
                                        [m.get('current_assets'), m.get('inv'), m.get('current_liab')])
    out['prepayment_inventory_ratio'] = output(ratio(get('contract_liab'), get('inv')),
                                              'contract_liabilities/inventory; not order coverage', [m.get('contract_liab'), m.get('inv')])
    gi, gc, gr = (out[k]['value'] for k in ('inv_yoy', 'contract_liab_yoy', 'rev_yoy'))
    source_cells = [m.get(k) for k in ('rev', 'inv', 'contract_liab')] + ([p['metrics'].get(k) for k in ('rev', 'inv', 'contract_liab')] if p else [])
    out['revenue_inventory_growth_gap_pp'] = output(gr-gi if gr is not None and gi is not None else None,
                                                   'revenue YoY-inventory YoY', source_cells, 'pp')
    out['prepayment_inventory_growth_ratio'] = output(gc/gi if gc is not None and gc >= 0 and gi is not None and gi > 0 else None,
                                                      'contract_liabilities YoY/inventory YoY; auxiliary only', source_cells,
                                                      reason='Missing base or inventory growth non-positive; near-zero and small-base risks require manual review')
    gn, ga = out['np_yoy']['value'], out['assets_yoy']['value']
    out['profit_asset_growth_gap_pp'] = output(gn-ga if gn is not None and ga is not None else None,
                                              'net profit YoY-assets YoY; not marginal ROIC',
                                              [m.get('np'), m.get('assets')] + ([p['metrics'].get('np'), p['metrics'].get('assets')] if p else []), 'pp')
    begin = lookup.get(record.get('begin_id'))
    if begin and parse_date(begin['end']) != parse_date(record['start']) - timedelta(days=1):
        raise ValueError('begin_id must be the immediately preceding balance date')
    if begin:
        bm = begin['metrics']
        days = (parse_date(record['end']) - parse_date(record['start'])).days + 1
        working = {}
        for name, key, denominator in (('dio', 'inv', c), ('dso', 'ar', r), ('dpo_cost_proxy', 'ap', c), ('asset_turnover', 'assets', r)):
            a, b = get(key), cny(bm, key)
            average = (a+b)/2 if a is not None and b is not None else None
            v = ratio(average, denominator, days) if name != 'asset_turnover' else ratio(denominator, average)
            working[name] = output(v, 'average balance/period flow*period days' if name != 'asset_turnover' else 'period revenue/average assets',
                                    [m.get(key), bm.get(key), m.get('cost' if key in ('inv', 'ap') else 'rev')], 'days' if name != 'asset_turnover' else 'ratio', interval_days=days)
        vv = [working[k]['value'] for k in ('dio', 'dso', 'dpo_cost_proxy')]
        working['ccc'] = output(vv[0]+vv[1]-vv[2] if all(x is not None for x in vv) else None,
                                 'DIO+DSO-DPO(cost proxy)', list(m.values()) + list(bm.values()), 'days')
        out.update(working)
        di = get('inv')-cny(bm, 'inv') if get('inv') is not None and cny(bm, 'inv') is not None else None
        dc = get('contract_liab')-cny(bm, 'contract_liab') if get('contract_liab') is not None and cny(bm, 'contract_liab') is not None else None
        out['inventory_prepayment_increment_gap'] = output(di-dc if di is not None and dc is not None else None,
                                                          'delta inventory-delta contract liabilities; financing proxy only',
                                                          [m.get('inv'), bm.get('inv'), m.get('contract_liab'), bm.get('contract_liab')], 'currency', currency='CNY')
        terms, absent = {}, []
        bridge_cells = [m.get('np'), m.get('ocf')]
        for key, sign in (('inv', -1), ('prepay', -1), ('ar', -1), ('contract_liab', 1), ('ap', 1), ('notes_payable', 1)):
            a, b = get(key), cny(bm, key)
            bridge_cells.extend([m.get(key), bm.get(key)])
            if a is None or b is None:
                absent.append(key)
            else:
                terms[key] = sign * (a-b)
        est = np_ + sum(terms.values()) if np_ is not None and terms else None
        result['simplified_cash_bridge'] = {
            'known_terms_cny': terms, 'missing_terms': absent,
            'estimate': output(est, 'consolidated net profit+known signed balance changes', bridge_cells, 'currency', currency='CNY'),
            'unexplained_residual': output(ocf-est if ocf is not None and est is not None else None,
                                           'reported OCF-known simplified estimate; cause unverified', bridge_cells, 'currency', currency='CNY'),
            'warning': 'Balance changes are not necessarily cash flows; mergers, FX and noncash items require original cash reconciliation.'}
    return result


def derive_quarters(records):
    direct = {r['end']: r for r in records if r['kind'] == 'quarter'}
    cumulative = sorted((r for r in records if r['kind'] in ('ytd', 'annual')), key=lambda r: r['end'])
    for cur in cumulative:
        end, start = parse_date(cur['end']), parse_date(cur['start'])
        duration = (end-start).days + 1
        if cur['end'] in direct:
            continue
        if 70 <= duration <= 110:
            direct[cur['end']] = dict(cur, kind='quarter', derived=True)
            continue
        preceding = [r for r in cumulative if r['start'] == cur['start'] and r['end'] < cur['end']]
        if not preceding:
            continue
        prev = preceding[-1]
        days = (end-parse_date(prev['end'])).days
        if not 70 <= days <= 110:
            continue
        metrics = {}
        for key in FLOW_KEYS:
            a, b = cny(cur['metrics'], key), cny(prev['metrics'], key)
            cells = [cur['metrics'].get(key), prev['metrics'].get(key)]
            v = a-b if a is not None and b is not None else None
            metrics[key] = output(v, 'current cumulative-previous cumulative', cells, 'currency', currency='CNY', locator='derived cumulative difference')
        direct[cur['end']] = {'id': 'derived-' + cur['id'], 'label': cur['label'] + ' [single quarter derived]',
                              'kind': 'quarter', 'start': (parse_date(prev['end'])+timedelta(days=1)).isoformat(),
                              'end': cur['end'], 'metrics': metrics, 'derived': True}
    ordered = sorted(direct.values(), key=lambda r: r['end'])
    result = []
    last = None
    quarter_lookup = {r['id']: r for r in ordered}
    for r in ordered:
        same = next((p for p in ordered if comparable(r, p)), None)
        calculated_record = dict(r)
        if same:
            calculated_record['prior_id'] = same['id']
        item = record_metrics(calculated_record, quarter_lookup)
        for key in ('gross_margin', 'operating_margin', 'net_margin'):
            a = item['metrics'][key]['value']
            contiguous = last and parse_date(last['end']) + timedelta(days=1) == parse_date(r['start'])
            b = last['metrics'][key]['value'] if contiguous else None
            item['metrics'][key + '_qoq_pp'] = output(a-b if a is not None and b is not None else None,
                                                      'current margin-previous adjacent quarter margin',
                                                      [item['metrics'][key], last['metrics'][key] if last else None], 'pp')
        if same:
            prior_item = record_metrics(same, {})
            for key in ('gross_margin', 'operating_margin', 'net_margin'):
                a, b = item['metrics'][key]['value'], prior_item['metrics'][key]['value']
                item['metrics'][key + '_yoy_pp'] = output(a-b if a is not None and b is not None else None,
                                                          'current quarter margin-same prior-year quarter margin',
                                                          [item['metrics'][key], prior_item['metrics'][key]], 'pp')
        result.append(item)
        last = item
    return result[-4:]


def derive_ttm(config, lookup):
    if not config:
        return {}
    annual, cur, prior = [lookup.get(config.get(k)) for k in ('annual_id', 'current_ytd_id', 'prior_ytd_id')]
    if not all((annual, cur, prior)):
        raise ValueError('TTM reconstruction requires all three record IDs')
    if annual['kind'] != 'annual' or cur['kind'] != 'ytd' or prior['kind'] != 'ytd' or not comparable(cur, prior):
        raise ValueError('TTM requires annual plus matching current/prior YTD')
    if parse_date(annual['end']) + timedelta(days=1) != parse_date(cur['start']) or annual['start'] != prior['start']:
        raise ValueError('TTM fiscal boundaries do not match')
    result = {}
    for k in FLOW_KEYS:
        cells = [x['metrics'].get(k) for x in (annual, cur, prior)]
        values = [val(c, 'currency', 'CNY') if c else None for c in cells]
        result[k] = output(values[0]+values[1]-values[2] if all(v is not None for v in values) else None,
                           'latest annual+current YTD-prior same YTD', cells, 'currency', currency='CNY', as_of=cur['end'], locator='TTM reconstructed')
    return result


def quantile(values, p):
    a = sorted(values)
    pos = (len(a)-1)*p
    i = int(pos)
    return a[i] + (a[min(i+1, len(a)-1)]-a[i])*(pos-i)


def history_metrics(history, as_of):
    result = {'code': history['code'], 'point_in_time': history.get('point_in_time'), 'metrics': {}}
    for key in ('pe', 'pb', 'ps'):
        cells = [row.get(key) for row in history.get('observations', []) if parse_date(row['date']) <= as_of]
        all_values = [val(c, 'ratio') for c in cells if c is not None]
        sample = [v for v in all_values if v is not None and v > 0]
        current = val(history.get('current', {}).get(key), 'ratio')
        if sample and current is not None and current > 0:
            percentile = (sum(v < current for v in sample) + 0.5*sum(v == current for v in sample))/len(sample)*100
            result['metrics'][key] = {
                'samples': len(sample), 'excluded': len(all_values)-len(sample),
                'q25': output(quantile(sample, .25), 'linear sample quantile25', cells),
                'q50': output(quantile(sample, .50), 'linear sample quantile50', cells),
                'q75': output(quantile(sample, .75), 'linear sample quantile75', cells),
                'percentile': output(percentile, '(less+0.5*equal)/N*100', cells+[history['current'][key]], 'percent')}
        else:
            result['metrics'][key] = {'status': 'not_calculable', 'reason': 'No positive valid samples/current multiple'}
    result['warning'] = 'History must be PIT-valid. This routine does not prove original financial publication availability or five-year completeness.'
    return result


def compare_peers(data, cutoff):
    """Compare target metrics with same-period, same-unit core peers. No data fetching."""
    peers = data.get('peers', []) or []
    by_id = {p.get('id'): p for p in peers}
    out = []
    for spec in data.get('peer_comparison_specs', []) or []:
        section, label, metric = spec.get('section'), spec.get('label'), spec.get('metric')
        period, unit = spec.get('period'), spec.get('unit', 'percent')
        tc = spec.get('target')
        target = val(tc, unit, 'CNY') if (tc and unit == 'currency') else (val(tc, unit) if tc else None)
        ids = spec.get('peer_ids') or [p.get('id') for p in peers]
        samples, cells = [], [tc]
        for pid in ids:
            peer = by_id.get(pid)
            if not peer or peer.get('relevance') == 'reference':
                continue
            for per in peer.get('periods', []) or []:
                if per.get('label') != period:
                    continue
                if parse_date(per['end']) > cutoff:
                    raise ValueError('Peer period beyond analysis date: ' + str(pid))
                c = (per.get('metrics', {}) or {}).get(metric)
                cells.append(c)
                v = val(c, unit, 'CNY') if (c and unit == 'currency') else (val(c, unit) if c else None)
                if v is not None:
                    samples.append({'peer_id': pid, 'value': v, 'source_ids': refs([c])})
                break
        vals = [s['value'] for s in samples]
        n = len(vals)
        med = quantile(vals, .5) if vals else None
        mean = sum(vals)/n if n else None
        gap = target-med if target is not None and med is not None else None
        percentile = ((sum(v < target for v in vals) + .5*sum(v == target for v in vals))/n*100
                      if target is not None and n else None)
        out.append({
            'section': section, 'label': label, 'metric': metric, 'period': period, 'unit': unit,
            'target': output(target, 'verified target input', [tc], unit),
            'peer_values': samples, 'sample_size': n,
            'median': output(med, 'linear median of valid core-peer values', cells, unit),
            'mean': output(mean, 'arithmetic mean of valid core-peer values', cells, unit),
            'gap_to_median': output(gap, 'target minus peer median', cells,
                                    'pp' if unit == 'percent' else unit),
            'target_percentile': output(percentile, '(count lower + 0.5*count equal)/N*100', cells, 'percent'),
            'status': 'ok' if n >= 3 else ('partial' if n else 'not_calculable'),
            'warning': 'Position only; peer median is not an industry benchmark. N<3: list values, do not assert a median conclusion.'
        })
    return out


def compute(data):
    if data.get('schema_version') != 3:
        raise ValueError('Schema v3 required; migrate v2 bare values to sourced CELLs using input-schema.md')
    cutoff = parse_date(data['analysis_as_of'])
    records = data.get('records', [])
    lookup = {r['id']: r for r in records}
    if len(lookup) != len(records):
        raise ValueError('Duplicate record IDs')
    for r in records:
        if parse_date(r['end']) > cutoff or parse_date(r['start']) > parse_date(r['end']):
            raise ValueError('Record date outside analysis boundary')
        for cell in r['metrics'].values():
            val(cell)
    ttm = derive_ttm(data.get('ttm', {}), lookup)
    fin = dict(data.get('financials', {}))
    for field, k in (('ttm_parent', 'parent'), ('ttm_ded', 'ded'), ('ttm_rev', 'rev'), ('ocf_ttm', 'ocf'), ('capex_ttm', 'capex')):
        if field not in fin and k in ttm:
            fin[field] = ttm[k]
    fv = lambda k: val(fin[k], 'currency', 'CNY') if fin.get(k) else None
    total_cell = data.get('entity', {}).get('total_shares')
    total = val(total_cell, 'shares') if total_cell else None
    result = {'schema_version': 3, 'entity': data.get('entity', {}),
              'records': [record_metrics(r, lookup) for r in records],
              'recent_quarters': derive_quarters(records), 'ttm': ttm, 'markets': [], 'histories': [],
              'warnings': ['Computation is not evidence clearance; run audit_evidence.py and original-document review.']}
    base_price = None
    macro = data.get('macro', {})
    for market in data.get('markets', []):
        if parse_date(market['close_date']) > cutoff or market.get('price_state') != 'close':
            raise ValueError('Use a verified completed closing price')
        pc, fx, vc = market['price'], market['fx_to_cny'], market['venue_shares']
        p, rate, vs = val(pc, 'price', market['currency']), val(fx, 'ratio'), val(vc, 'shares')
        k = finite(market.get('underlying_per_quote', 1))
        if k <= 0 or (rate is not None and rate <= 0):
            raise ValueError('Underlying quote ratio and FX must be positive')
        normalized = p*rate/k if p is not None and rate is not None else None
        # venue_shares counts the quoted instruments; total_shares counts underlying ordinary shares.
        venue = p*rate*vs if all(v is not None for v in (p, rate, vs)) else None
        equivalent = normalized*total if normalized is not None and total is not None else None
        cells = [pc, fx, total_cell]
        row = {'code': market['code'], 'close_date': market['close_date'], 'metrics': {}}
        om = row['metrics']
        om['cny_per_underlying_share'] = output(normalized, 'price*FX/underlying_per_quote', [pc, fx], 'price', currency='CNY')
        om['venue_share_value_cny'] = output(venue, 'quoted price*FX*issued quoted-class shares; not valuation denominator', [pc, fx, vc], 'currency', currency='CNY')
        om['price_equivalent_equity_cny'] = output(equivalent, 'CNY price per underlying*total ordinary shares', cells, 'currency', currency='CNY')
        for name, denominator in (('pe_ttm', 'ttm_parent'), ('pe_static', 'annual_parent'), ('pe_ded_ttm', 'ttm_ded'), ('forward_pe', 'forward_parent'), ('pb', 'parent_equity'), ('ps_ttm', 'ttm_rev')):
            om[name] = output(ratio(equivalent, fv(denominator)), 'price-equivalent equity/' + denominator, cells+[fin.get(denominator)])
        ey = ratio(fv('ttm_parent'), equivalent, 100)
        om['earnings_yield'] = output(ey if fv('ttm_parent') is not None and fv('ttm_parent') > 0 else None,
                                      'TTM parent profit/price-equivalent equity*100', cells+[fin.get('ttm_parent')], 'percent')
        ocf, capex = fv('ocf_ttm'), fv('capex_ttm')
        fcf = ocf-capex if ocf is not None and capex is not None else None
        om['fcf_ttm'] = output(fcf, 'TTM OCF-TTM CapEx', [fin.get('ocf_ttm'), fin.get('capex_ttm')], 'currency', currency='CNY')
        om['fcf_yield'] = output(ratio(fcf, equivalent, 100) if fcf is not None and fcf > 0 else None,
                                 'positive TTM FCF/price-equivalent equity*100', cells+[fin.get('ocf_ttm'), fin.get('capex_ttm')], 'percent',
                                 reason='TTM FCF missing or non-positive; show FCF amount instead')
        debt, cash = fv('ib_debt'), fv('cash')
        ev = equivalent+debt-cash if all(v is not None for v in (equivalent, debt, cash)) else None
        om['ev_simple'] = output(ev, 'price-equivalent equity+interest debt-cash; minority/preferred excluded', cells+[fin.get('ib_debt'), fin.get('cash')], 'currency', currency='CNY')
        om['ev_ebitda_simple'] = output(ratio(ev, fv('ebitda')), 'simple EV/verified EBITDA', [om['ev_simple'], fin.get('ebitda')])
        growth_cells = market.get('peg_growth_cases', {})
        om['peg_cases'] = {}
        for label, gc in growth_cells.items():
            g = val(gc, 'percent')
            pe = om['pe_ttm']['value']
            om['peg_cases'][label] = output(pe/g if pe is not None and pe > 0 and g is not None and g > 0 else None,
                                           'PE-TTM/G(percent number)', [om['pe_ttm'], gc], reason='PE or G missing/non-positive; comparability additionally requires review')
        for key in ('cn10y', 'us10y'):
            yc = macro.get(key)
            yield_ = val(yc, 'percent') if yc else None
            om['ey_bond_spread_' + key] = output(ey-yield_ if ey is not None and yield_ is not None else None,
                                                'earnings yield-government yield; not expected ERP', [om['earnings_yield'], yc], 'pp')
        if not result['markets']:
            base_price = normalized
        if market.get('has_equal_economic_rights') is True and data['markets'][0].get('has_equal_economic_rights') is True:
            om['price_vs_first_market_pct'] = output((normalized/base_price-1)*100 if normalized is not None and base_price is not None and base_price > 0 else None,
                                                      'same-right normalized price/first market normalized price-1', [pc, fx, *([data['markets'][0].get('price'), data['markets'][0].get('fx_to_cny')] if data['markets'] else [])], 'percent')
        result['markets'].append(row)
    u3c, u10c = macro.get('us3m'), macro.get('us10y')
    u3, u10 = val(u3c, 'percent') if u3c else None, val(u10c, 'percent') if u10c else None
    same_date = bool(u3c and u10c and u3c.get('as_of') and u3c.get('as_of') == u10c.get('as_of'))
    result['us_term_spread_bps'] = output((u10-u3)*100 if same_date and u3 is not None and u10 is not None else None,
                                         '(US10Y-US3M)*100; same observation day', [u3c, u10c], 'bps', reason='Missing or unmatched observation dates')
    for h in data.get('histories', []):
        result['histories'].append(history_metrics(h, cutoff))
    result['peer_comparisons'] = compare_peers(data, cutoff)
    return result


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    if sys.argv[1] == '--template':
        print(json.dumps(TEMPLATE, ensure_ascii=False, indent=2))
        return 0
    try:
        with open(sys.argv[1], encoding='utf-8') as fh:
            data = json.load(fh)
        print(json.dumps(compute(data), ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print('Computation blocked: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
