"""Focused owner reports over the local archive; no cloud queries or Tally writes."""
import calendar
import csv
import datetime as dt
import io
import json
import math
import sqlite3
from contextlib import closing
import threading
from collections import defaultdict
from desktop_archive import IST,number,owner,unpack
from desktop_analysis import shift_months
from desktop_workflows import Workflows,descendants


class Management:
    def __init__(self,archive,analysis):
        self.archive=archive;self.analysis=analysis;self.lock=threading.RLock();self.lines={}

    def report(self,start='',end='',route='',rep='',party='',mode='custom',year=''):
        with self.lock:return self._report(start,end,route,rep,party,mode,year)

    def _report(self,start,end,route,rep,party,mode,year):
        today=dt.datetime.now(IST).date()
        if not start and mode=='custom':start=today.replace(day=1).strftime('%Y%m%d')
        d=self.analysis.report(start,end,route,rep,'previous',mode,year,party)
        first=dt.datetime.strptime(d['start'],'%Y%m%d').date();last=dt.datetime.strptime(d['end'],'%Y%m%d').date()
        previous_first=shift_months(first,-1);previous_last=shift_months(last,-1)
        if last.day==calendar.monthrange(last.year,last.month)[1]:previous_last=previous_last.replace(day=calendar.monthrange(previous_last.year,previous_last.month)[1])
        with self.archive.read() as conn:
            vouchers=self.archive.current(conn);tables=self.archive.tables(conn);states=dict(conn.execute('SELECT key,value FROM sync_state'))
            parties={p['name']:p for p in self.archive.catalogue(conn,'parties')}
            debtor_groups=descendants(self.archive.masters(conn,'Group'),'Sundry Debtors')
            d['debtors']=round(sum(-p['balance'] for p in parties.values() if p['group'] in debtor_groups and p.get('balance') is not None and p['balance']<0 and (not route or p['group']==route) and (not party or p['name']==party)),2)
            orders=self.archive.rows(conn,'shop_order') if 'shop_order' in tables else []
            owners=defaultdict(set)
            for order in orders:
                if order.get('tally_guid'):owners[order['tally_guid']].add(owner(order.get('note')))
            auxiliary=self.archive.path.parent/'saarthi_admin.db'
            if auxiliary.exists():
                with closing(sqlite3.connect(auxiliary.as_uri()+'?mode=ro',uri=True)) as admin:
                    if admin.execute("SELECT 1 FROM sqlite_master WHERE name='command'").fetchone():
                        for payload,result in admin.execute("SELECT payload,result FROM command WHERE kind='sale' AND state='confirmed'"):
                            payload,result=json.loads(payload),json.loads(result or '{}')
                            if payload.get('company')==states.get('company') and result.get('guid'):owners[result['guid']].add('Admin desktop')
            def attribution(v):return next(iter(owners[v['guid']])) if len(owners[v['guid']])==1 else 'Unattributed'
            def area(v):return parties.get(v['party'],{}).get('group') or 'Uncategorised'
            def match(v):return (not route or area(v)==route) and (not rep or attribution(v)==rep) and (not party or v['party']==party)
            active=[v for v in vouchers if v['state']=='active' and v['date']]
            sales=[v for v in active if v['type']=='Sales' and match(v)]
            prior=[v for v in active if v['type'] in ('Sales','Credit Note') and match(v) and previous_first.strftime('%Y%m%d')<=v['date']<=previous_last.strftime('%Y%m%d')]
            previous=sum(v['total'] for v in prior if v['type']=='Sales')
            d.update(mom_previous=round(previous,2),mom_growth=round((d['metrics']['gross']-previous)/abs(previous)*100,1) if mode!='all' and previous>0 and d.get('earliest_sale') and d['earliest_sale']<=previous_first.strftime('%Y%m%d') else None,mom_start=previous_first.strftime('%Y%m%d'),mom_end=previous_last.strftime('%Y%m%d'))
            month=last.replace(day=1);prior_month=shift_months(month,-1);three=shift_months(month,-3)
            activity=defaultdict(lambda:dict(previous=set(),current=set(),last_three=False,last_month=False,this_month=False))
            for v in sales:
                if not three.strftime('%Y%m%d')<=v['date']<=d['end']:continue
                key=(v['guid'],v['version'])
                if key not in self.lines:
                    raw=conn.execute('SELECT blob FROM voucher WHERE guid=? AND alterid=?',key).fetchone()
                    record=unpack(raw[0]) if raw else {}
                    lines=record.get('ALLINVENTORYENTRIES.LIST') or record.get('INVENTORYENTRIES.LIST') or []
                    self.lines[key]=lines if isinstance(lines,list) else [lines]
                row=activity[v['party']];items={l.get('STOCKITEMNAME') for l in self.lines[key] if isinstance(l,dict) and l.get('STOCKITEMNAME')}
                if v['date']>=month.strftime('%Y%m%d'):row['this_month']=True;row['current'].update(items)
                else:
                    row['last_three']=True
                    if v['date']>=prior_month.strftime('%Y%m%d'):row['last_month']=True;row['previous'].update(items)
            # A waiting website order already represents a customer response.
            current_ids={v['guid'] for v in active if v['type']=='Sales'}
            for order in orders:
                if order['status'] in ('draft','rejected') or order.get('tally_guid') in current_ids:continue
                name=order['party_name'];day=(order.get('created_at') or '')[:10].replace('-','')
                if not month.strftime('%Y%m%d')<=day<=d['end'] or (party and name!=party) or (route and parties.get(name,{}).get('group')!=route) or (rep and owner(order.get('note'))!=rep):continue
                row=activity[name];row['this_month']=True
                for line in conn.execute('SELECT item_name FROM shop_order_line WHERE order_no=?',(order['order_no'],)):row['current'].add(line[0])
            focus=[]
            for name,row in activity.items():
                missing=sorted(row['previous']-row['current'])
                if row['last_three'] and not row['this_month'] or missing:
                    focus.append(dict(name=name,route=parties.get(name,{}).get('group'),reason='Bought last month; no order this month' if row['last_month'] and not row['this_month'] else 'Bought in the previous 3 months; no order this month' if not row['this_month'] else 'Some previous products not bought this month',priority=0 if row['last_month'] and not row['this_month'] else 1 if not row['this_month'] else 2,previous_items=len(row['previous']),current_items=len(row['current']),missing_items=missing))
            d['customer_focus']=sorted(focus,key=lambda r:(r['priority'],r['name']))
            d['focus_month']=month.strftime('%B %Y')
            from purchase_costs import apply_margins
            selected=[v for v in active if v['type'] in ('Sales','Credit Note') and match(v) and d['start']<=v['date']<=d['end']]
            def cost_lines(v):
                key=(v['guid'],v['version'])
                if key not in self.lines:
                    raw=conn.execute('SELECT blob FROM voucher WHERE guid=? AND alterid=?',key).fetchone()
                    record=unpack(raw[0]) if raw else {}
                    lines=record.get('ALLINVENTORYENTRIES.LIST') or record.get('INVENTORYENTRIES.LIST') or []
                    self.lines[key]=lines if isinstance(lines,list) else [lines]
                return self.lines[key]
            apply_margins(self.archive,conn,d,selected,cost_lines)
            visits=self.archive.rows(conn,'sales_visit') if 'sales_visit' in tables else []
            visit_sets=defaultdict(set)
            for v in visits:
                if d['start']<=v['vdate']<=d['end'] and (not route or v['route']==route) and (not rep or v['salesperson']==rep) and (not party or v['customer_name']==party):visit_sets[v['salesperson']].add((v['vdate'],v['customer_guid']))
            team={r['name']:r for r in d['team']}
            for name in visit_sets:
                if name not in team:team[name]=dict(name=name,gross=0,net=0,invoices=0,submitted=0,billed_orders=0,buyers=0,waiting_orders=0)
            for name,row in team.items():
                row['visits']=len(visit_sets[name]) if states.get('visit_sync_at') else None
                row['order_days']=len({((o.get('created_at') or '')[:10],o['party_guid']) for o in orders if o['status'] not in ('draft','rejected') and owner(o.get('note'))==name and d['start']<=(o.get('created_at') or '')[:10].replace('-','')<=d['end'] and (not route or (parties.get(o['party_name'],{}).get('group') or o.get('route'))==route) and (not party or o['party_name']==party)})
            d['team']=sorted(team.values(),key=lambda r:-r['gross']);d['visit_sync_at']=states.get('visit_sync_at')
            d['reps']=sorted(set(d['reps'])|set(team))
            activity_days={}
            for offset in range(min(14,(last-first).days+1)):
                day=(last-dt.timedelta(days=offset)).strftime('%Y%m%d');activity_days[day]=dict(date=day,visits=0,orders=0)
            for who,rows in visit_sets.items():
                for day,_ in rows:
                    if day in activity_days:activity_days[day]['visits']+=1
            for order in orders:
                day=(order.get('created_at') or '')[:10].replace('-','')
                if day in activity_days and order['status'] not in ('draft','rejected') and (not rep or owner(order.get('note'))==rep) and (not route or (parties.get(order['party_name'],{}).get('group') or order.get('route'))==route) and (not party or order['party_name']==party):activity_days[day]['orders']+=1
            d['team_activity']=sorted(activity_days.values(),key=lambda r:r['date'])
            keys={(v['guid'],v['version']) for v in vouchers};self.lines={k:v for k,v in self.lines.items() if k in keys}
        return d

    def overview(self,start='',end=''):
        today=dt.datetime.now(IST).date();start=start or today.replace(day=1).strftime('%Y%m%d')
        d=self.archive.overview(start,end);report=self.report(d['start'],d['end'])
        with self.archive.read() as conn:
            groups=descendants(self.archive.masters(conn,'Group'),'Sundry Debtors')
            debtors=[p for p in self.archive.catalogue(conn,'parties') if p['group'] in groups and p.get('balance') is not None and p['balance']<0]
        d.update(debtors=round(sum(-p['balance'] for p in debtors),2),debtor_count=len(debtors),debtor_list=sorted(debtors,key=lambda p:p['balance']),debtor_asof=max((p.get('balance_asof') or '' for p in debtors),default=None),routes=report['territories'],top_products=sorted(report['products'],key=lambda p:-p['gross'])[:10])
        return d


def rate_preview(work,text):
    if not isinstance(text,str) or len(text)>500000:raise ValueError('Choose a CSV smaller than 500 KB')
    reader=csv.DictReader(io.StringIO(text.lstrip('\ufeff')))
    if 'rate' not in (reader.fieldnames or []):raise ValueError('CSV must have a rate column')
    rows=list(reader)
    if not 1<=len(rows)<=1000:raise ValueError('CSV must contain 1–1000 products')
    context=work.context();products=context['products'];by_id={p['id']:p for p in products};by_name=defaultdict(list)
    for p in products:by_name[p['name']].append(p)
    result=[];seen=set();skipped=0
    for index,row in enumerate(rows,2):
        if None in row:raise ValueError(f'Row {index}: extra CSV columns. Enter the rate without thousands separators.')
        price=(row.get('rate') or '').strip()
        if not price:
            skipped+=1
            continue
        p=by_id.get((row.get('item_guid') or '').strip());name=(row.get('product') or '').strip()
        if not p and len(by_name[name])==1:p=by_name[name][0]
        if not p:raise ValueError(f'Row {index}: use an exact product name or item_guid')
        if p['id'] in seen:raise ValueError(f'Row {index}: duplicate product')
        body=dict(item=p['id'],name=p['name'],price=price,rate_only=True)
        try:work.validate('product_update',body,context=context)
        except ValueError as exc:raise ValueError(f'Row {index}: {exc}') from exc
        seen.add(p['id']);result.append(body)
    return {'rows':result,'skipped':skipped}


def inventory(work):
    context=work.context()
    with work.db() as conn:
        policies={r['guid']:dict(r) for r in conn.execute('SELECT * FROM inventory_policy')}
        images={r[0] for r in conn.execute('SELECT guid FROM product_image')}
        hidden={r[0] for r in conn.execute('SELECT guid FROM website_hidden_product')}
    for product in context['products']:
        product['website_hidden']=product['id'] in hidden
        policy=policies.get(product['id'],{});minimum=policy.get('minimum',0);stock=product['stock'];target=policy.get('target')
        product.update(minimum=minimum,target=target,has_image=product['id'] in images,restock=stock is not None and stock<=minimum,reorder_qty=max(0,target-stock) if target is not None and stock is not None else None)
    return {'company':context['company'],'database':context['database'],'products':context['products']}


def save_our_rates(work, body):
    from decimal import Decimal, InvalidOperation
    rows = body.get('rates')
    if not isinstance(rows, list) or not 1 <= len(rows) <= 1000:
        raise ValueError('Select 1–1000 product rates')
    with work.archive.read() as conn:
        products = {row['guid'] for row in work.archive.masters(conn, 'StockItem')}
    rates = {}
    for row in rows:
        guid = row.get('item') if isinstance(row, dict) else None
        if not isinstance(guid, str) or guid not in products or guid in rates:
            raise ValueError('Choose an existing Tally product once per rate')
        raw = str(row.get('price')).strip()
        try:
            amount = Decimal(raw)
            price = float(amount)
        except (InvalidOperation, OverflowError, ValueError):
            raise ValueError('Rate must be a finite number') from None
        if '_' in raw or not amount.is_finite() or not math.isfinite(price):
            raise ValueError('Rate must be a finite number')
        rates[guid] = price
    with work.db() as conn:
        conn.executemany('INSERT INTO product_our_rate VALUES(?,?) '
                         'ON CONFLICT(guid) DO UPDATE SET price=excluded.price', rates.items())
    return {'saved': len(rates)}


def save_visibility(work, body):
    hidden = body.get('hidden')
    guids = body.get('guids', [body.get('guid')])
    if type(hidden) is not bool:
        raise ValueError('Choose whether to hide this product on the website')
    if not isinstance(guids, list) or not 1 <= len(guids) <= 1000 or any(
            not isinstance(guid, str) or not guid for guid in guids):
        raise ValueError('Select 1–1000 products')
    guids = set(guids)
    with work.archive.read() as conn:
        if not guids <= {row['guid'] for row in work.archive.masters(conn, 'StockItem')}:
            raise ValueError('Choose an existing Tally product')
    with work.db() as conn:
        if hidden:
            conn.executemany('INSERT OR IGNORE INTO website_hidden_product(guid) VALUES(?)', ((guid,) for guid in guids))
        else:
            conn.executemany('DELETE FROM website_hidden_product WHERE guid=?', ((guid,) for guid in guids))
    return {'hidden': hidden}


def save_policy(work,body):
    if body.get('guid') not in {p['id'] for p in work.context()['products']}:raise ValueError('Choose an existing Tally product')
    try:
        minimum=float(body.get('minimum') or 0);target=float(body['target']) if body.get('target') not in ('',None) else None
        if not math.isfinite(minimum) or minimum<0 or minimum>10000000 or target is not None and (not math.isfinite(target) or target<minimum or target>10000000):raise ValueError()
    except (ValueError,TypeError):raise ValueError('Reorder level must be non-negative; target must be at least the reorder level') from None
    from desktop_workflows import stamp
    with work.db() as conn:conn.execute('INSERT INTO inventory_policy VALUES(?,?,?,?) ON CONFLICT(guid) DO UPDATE SET minimum=excluded.minimum,target=excluded.target,updated_at=excluded.updated_at',(body['guid'],minimum,target,stamp()))
    return {'saved':True}
