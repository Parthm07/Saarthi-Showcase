"""Local sales analysis. Accounting totals use current active Tally vouchers."""
import calendar
import datetime as dt
import json
import re
import threading
import sqlite3
from contextlib import closing
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from desktop_archive import IST, number, owner, unpack


def shift_months(day, months):
    month = day.year * 12 + day.month - 1 + months
    year, month = divmod(month, 12); month += 1
    return dt.date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def period(start='', end='', comparison='previous'):
    today = dt.datetime.now(IST).date()
    if any(value and (not isinstance(value,str) or not re.fullmatch(r'\d{8}',value)) for value in (start,end)): raise ValueError('Choose valid dates')
    try:
        last = dt.datetime.strptime(end, '%Y%m%d').date() if end else today
        first = dt.datetime.strptime(start, '%Y%m%d').date() if start else last - dt.timedelta(days=29)
    except (ValueError, TypeError): raise ValueError('Choose valid dates') from None
    days = (last-first).days + 1
    if not 1 <= days <= 1096: raise ValueError('Select a date range of at most three years')
    if comparison == 'previous': previous_end = first-dt.timedelta(days=1); previous_start = previous_end-dt.timedelta(days=days-1)
    elif comparison in ('year', 'quarter'):
        months = -12 if comparison == 'year' else -3
        previous_start, previous_end = shift_months(first, months), shift_months(last, months)
    else: raise ValueError('Choose previous period, previous quarter or previous year')
    return tuple(d.strftime('%Y%m%d') for d in (first,last,previous_start,previous_end))


class Analysis:
    def __init__(self, archive):
        self.archive = archive
        self._lines = {}
        self._lock = threading.RLock()

    def goals(self, start, end, route):
        path = self.archive.path.parent / 'saarthi_admin.db'
        if not path.exists(): return {}
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=5)) as conn:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='analysis_goal'").fetchone(): return {}
            return {row[0]:float(row[1]) for row in conn.execute('SELECT rep,amount FROM analysis_goal WHERE start=? AND end=? AND route=?',(start,end,route))}

    def save_goal(self, body):
        start,end,_,_ = period(body.get('start'),body.get('end'))
        route,rep = body.get('route',''),body.get('rep','')
        if not all(isinstance(x,str) and len(x)<=300 for x in (route,rep)): raise ValueError('Invalid target scope')
        try: value = Decimal(str(body.get('amount')))
        except (InvalidOperation, ValueError): raise ValueError('Enter a valid sales target') from None
        if not value.is_finite() or value < 0 or value > Decimal('1000000000000'): raise ValueError('Enter a sales target between zero and 1 trillion')
        from desktop_workflows import Workflows
        with Workflows(self.archive).db() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS analysis_goal(start TEXT,end TEXT,route TEXT,rep TEXT,amount TEXT,PRIMARY KEY(start,end,route,rep))')
            if not value: conn.execute('DELETE FROM analysis_goal WHERE start=? AND end=? AND route=? AND rep=?',(start,end,route,rep))
            else: conn.execute('INSERT INTO analysis_goal VALUES(?,?,?,?,?) ON CONFLICT(start,end,route,rep) DO UPDATE SET amount=excluded.amount',(start,end,route,rep,str(value.quantize(Decimal('.01')))))
        return {'saved':True}

    def report(self, start='', end='', route='', rep='', comparison='previous', mode='custom', year='', party=''):
        with self._lock:
            return self._report(start,end,route,rep,comparison,mode,year,party)

    def _report(self, start='', end='', route='', rep='', comparison='previous', mode='custom', year='', party=''):
        if mode not in ('custom','year','all'): raise ValueError('Choose dates, a year, or all time')
        today=dt.datetime.now(IST).date()
        if mode=='year':
            try: chosen=int(year or today.year)
            except (ValueError,TypeError): raise ValueError('Choose a valid year') from None
            if not 1901<=chosen<=today.year: raise ValueError('Choose a year through the current year')
            start=f'{chosen}0101';end=(today if chosen==today.year else dt.date(chosen,12,31)).strftime('%Y%m%d');comparison='year'
        if mode!='all': start,end,prior_start,prior_end = period(start,end,comparison)
        with self.archive.read() as conn:
            tables = self.archive.tables(conn)
            states = dict(conn.execute('SELECT key,value FROM sync_state'))
            vouchers = self.archive.current(conn)
            orders = self.archive.rows(conn,'shop_order') if 'shop_order' in tables else []
            linked = {r['tally_guid']:r for r in orders if r.get('tally_guid')}
            owners=defaultdict(set)
            for order in orders:
                if order.get('tally_guid'): owners[order['tally_guid']].add(owner(order.get('note')))
            # Preserve confirmed desktop-sale attribution from the remote branch.
            auxiliary=self.archive.path.parent/'saarthi_admin.db'
            if auxiliary.exists():
                with closing(sqlite3.connect(auxiliary.as_uri()+'?mode=ro',uri=True,timeout=5)) as admin:
                    if admin.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='command'").fetchone():
                        for payload,result in admin.execute("SELECT payload,result FROM command WHERE kind='sale' AND state='confirmed'"):
                            payload,result=json.loads(payload),json.loads(result or '{}')
                            if payload.get('company')==states.get('company') and result.get('guid'): owners[result['guid']].add('Admin desktop')
            parties = {r['name']:r for r in self.archive.catalogue(conn,'parties')}
            products = {r['name']:r for r in self.archive.catalogue(conn,'products')}
            # No salesperson is guessed from a party's current route or owner.
            def salesman(v): return next(iter(owners[v['guid']])) if len(owners[v['guid']])==1 else 'Unattributed'
            def territory(v): return parties.get(v['party'],{}).get('group') or linked.get(v['guid'],{}).get('route') or 'Uncategorised'
            def scoped(v): return (not route or territory(v)==route) and (not rep or salesman(v)==rep) and (not party or v['party']==party)
            active = [v for v in vouchers if v['state']=='active' and v['type'] in ('Sales','Credit Note')]
            sales = [v for v in active if v['type']=='Sales']
            observed_years={int(v['date'][:4]) for v in active if v['date'] and '1901'<=v['date'][:4]<=str(today.year)}|{today.year}
            years=list(range(today.year,min(observed_years)-1,-1))
            party_options=sorted({v['party'] for v in active if (not route or territory(v)==route) and (not rep or salesman(v)==rep)})
            if mode=='all':
                start=min((v['date'] for v in active if scoped(v) and v['date'] and v['date']<=today.strftime('%Y%m%d')),default=today.strftime('%Y%m%d'))
                end=today.strftime('%Y%m%d');prior_start=prior_end='';comparison='none'
            options_routes = sorted({territory(v) for v in sales} | {r.get('route') for r in orders if r.get('route')})
            options_reps = sorted({salesman(v) for v in sales} | {owner(r.get('note')) for r in orders})
            selected = [v for v in active if scoped(v) and start <= (v['date'] or '') <= end]
            previous = [v for v in active if scoped(v) and prior_start <= (v['date'] or '') <= prior_end]
            current_sales = [v for v in selected if v['type']=='Sales']
            def metrics(rows):
                invoices = [v for v in rows if v['type']=='Sales']
                gross = sum(v['total'] for v in invoices); credits = sum(v['total'] for v in rows if v['type']=='Credit Note')
                buyers = Counter(v['party'] for v in invoices)
                return dict(gross=round(gross,2),credits=round(credits,2),net=round(gross-credits,2),invoices=len(invoices),buyers=len(buyers),repeat_buyers=sum(n>1 for n in buyers.values()),average=round(gross/len(invoices),2) if invoices else None,credit_ratio=round(credits/gross*100,1) if gross else None)
            now,prior = metrics(selected),metrics(previous)
            earliest = min((v['date'] for v in sales if v['date']),default=None)
            complete = bool(earliest and earliest<=prior_start)
            growth = round((now['net']-prior['net'])/abs(prior['net'])*100,1) if complete and prior['net']>0 else None
            daily = defaultdict(lambda:dict(gross=0,credits=0,invoices=0))
            for v in selected:
                d=daily[v['date']];d['gross' if v['type']=='Sales' else 'credits']+=v['total'];d['invoices']+=v['type']=='Sales'
            first=dt.datetime.strptime(start,'%Y%m%d').date();last=dt.datetime.strptime(end,'%Y%m%d').date()
            trend=[]
            for offset in range((last-first).days+1 if mode!='all' else 0):
                key=(first+dt.timedelta(days=offset)).strftime('%Y%m%d');d=daily[key]
                trend.append(dict(date=key,**d,net=round(d['gross']-d['credits'],2)))
            def breakdown(key):
                groups=defaultdict(list)
                for v in selected: groups[key(v)].append(v)
                prev=defaultdict(list)
                for v in previous: prev[key(v)].append(v)
                result=[]
                for name in groups.keys() | prev.keys():
                    m=metrics(groups[name]);p=metrics(prev[name])
                    result.append(dict(name=name,**m,prior_gross=p['gross'],change=round((m['gross']-p['gross'])/p['gross']*100,1) if complete and p['gross']>0 else None,share=round(m['gross']/now['gross']*100,1) if now['gross'] else 0))
                return sorted(result,key=lambda r:-r['gross'])
            customers=breakdown(lambda v:v['party']);team=breakdown(salesman);routes=breakdown(territory)
            active_buyers={v['party'] for v in current_sales}
            earliest_party={};last_party={}
            for v in sorted(sales,key=lambda v:v['date'] or ''):
                earliest_party.setdefault(v['party'],v['date']);last_party[v['party']]=v['date']
            for customer in customers:
                p=parties.get(customer['name'],{})
                customer.update(balance=max(0,-(p.get('balance') or 0)),balance_asof=p.get('balance_asof'),route=p.get('group'),last_purchase=last_party.get(customer['name']))
            top5=sum(c['gross'] for c in customers[:5]);now['top5_share']=round(top5/now['gross']*100,1) if now['gross'] else 0
            now['first_observed_buyers']=sum(start<= (earliest_party.get(name) or '') <=end for name in active_buyers)
            # Decode inventory payloads only when a new immutable version appears.
            keys={(v['guid'],v['version']) for v in vouchers}
            self._lines={k:v for k,v in self._lines.items() if k in keys}
            product_rows={};party_products={};missing=0
            for v in selected:
                key=(v['guid'],v['version'])
                if key not in self._lines:
                    row=conn.execute('SELECT blob FROM voucher WHERE guid=? AND alterid=?',key).fetchone()
                    raw=unpack(row[0]) if row else {}
                    lines=raw.get('ALLINVENTORYENTRIES.LIST') or raw.get('INVENTORYENTRIES.LIST') or []
                    self._lines[key]=lines if isinstance(lines,list) else [lines]
                lines=self._lines[key]
                if not lines: missing+=1
                for line in lines:
                    if not isinstance(line,dict): continue
                    name=line.get('STOCKITEMNAME')
                    if not name: continue
                    p=products.get(name,{})
                    item=product_rows.setdefault(name,dict(name=name,group=p.get('group'),gross=0,credits=0,units=defaultdict(float),parties=set(),bills=set(),stock=p.get('stock'),stock_asof=p.get('stock_asof')))
                    credit=v['type']=='Credit Note';value=abs(number(line.get('AMOUNT')) or 0)
                    item['credits' if credit else 'gross']+=value
                    qty_text=str(line.get('BILLEDQTY') or line.get('ACTUALQTY') or '')
                    quantity=number(qty_text)
                    if quantity is not None:
                        unit=re.sub(r'^\s*-?[\d,.]+\s*','',qty_text).split('=')[0].strip() or p.get('unit') or 'unknown unit'
                        item['units'][unit]+=abs(quantity)*(-1 if credit else 1)
                    else: unit=p.get('unit') or 'unknown unit'
                    purchase=party_products.setdefault((v['party'],name,unit),dict(party=v['party'],name=name,unit=unit,gross=0,credits=0,quantity=0,returned=0,bills=set(),last_purchase=None,last_price=None,_latest=('',-1)))
                    purchase['credits' if credit else 'gross']+=value
                    purchase['returned' if credit else 'quantity']+=abs(quantity or 0)
                    if not credit:
                        purchase['bills'].add(v['guid'])
                        order=(v['date'] or '',v['version'])
                        if order>=purchase['_latest']:
                            purchase.update(_latest=order,last_purchase=v['date'],last_price=number(line.get('RATE')) if line.get('RATE') else (value/abs(quantity) if quantity else None))
                    if not credit: item['parties'].add(v['party']);item['bills'].add(v['guid'])
            product_list=[]
            for p in product_rows.values():
                p.update(net=round(p['gross']-p['credits'],2),customers=len(p.pop('parties')),invoices=len(p.pop('bills')),units=dict(p['units']));product_list.append(p)
            product_list.sort(key=lambda p:-p['net'])
            purchase_list=[]
            for row in party_products.values():
                row.pop('_latest');row.update(gross=round(row['gross'],2),credits=round(row['credits'],2),net=round(row['gross']-row['credits'],2),invoices=len(row.pop('bills')),net_quantity=round(row['quantity']-row['returned'],3))
                purchase_list.append(row)
            purchase_list.sort(key=lambda r:(-r['net'],r['party'],r['name']))
            # Calendar comparisons use matching elapsed dates for the current year,
            # so a partly finished year is never compared with a full prior year.
            scoped_history=[v for v in active if scoped(v) and v['date'] and v['date']<=today.strftime('%Y%m%d')]
            history_start=min((v['date'] for v in scoped_history),default=None)
            def calendar_row(label,lo,hi,prior_lo,prior_hi,future=False):
                rows=[v for v in scoped_history if lo<=v['date']<=hi]
                previous_rows=[v for v in scoped_history if prior_lo<=v['date']<=prior_hi]
                m,p=metrics(rows),metrics(previous_rows)
                covered=bool(history_start and history_start<=prior_lo and not future)
                return dict(label=label,start=lo,end=hi,**m,previous_net=p['net'] if covered else None,change=round((m['net']-p['net'])/abs(p['net'])*100,1) if covered and p['net']>0 else None,future=future,partial=hi<lo[:4]+'1231' if len(label)==4 else hi[-2:]!=str(calendar.monthrange(int(lo[:4]),int(lo[4:6]))[1]))
            yearly=[]
            if history_start:
                for y in range(int(history_start[:4]),today.year+1):
                    hi=min(dt.date(y,12,31),today);prev=shift_months(hi,-12)
                    yearly.append(calendar_row(str(y),f'{y}0101',hi.strftime('%Y%m%d'),f'{y-1}0101',prev.strftime('%Y%m%d')))
            monthly=[]
            if mode=='year':
                for month in range(1,13):
                    lo=dt.date(chosen,month,1);month_end=dt.date(chosen,month,calendar.monthrange(chosen,month)[1]);future=lo>today
                    hi=min(month_end,today) if not future else month_end
                    monthly.append(calendar_row(calendar.month_abbr[month],lo.strftime('%Y%m%d'),hi.strftime('%Y%m%d'),shift_months(lo,-12).strftime('%Y%m%d'),shift_months(hi,-12).strftime('%Y%m%d'),future))
            current_by_guid={v['guid']:v for v in vouchers};pipeline=[];counts=Counter();totals=Counter();all_waiting=[]
            def parse_time(value):
                try:
                    stamp=dt.datetime.fromisoformat(value.replace('Z','+00:00'))
                    return stamp if stamp.tzinfo else stamp.replace(tzinfo=IST)
                except (ValueError,TypeError,AttributeError): return None
            for order in orders:
                if order['status']=='draft': continue
                salesman_name=owner(order.get('note'));order_party=order['party_name'];area=parties.get(order_party,{}).get('group') or order.get('route') or 'Uncategorised'
                if (rep and salesman_name!=rep) or (route and area!=route) or (party and party!=order['party_name']): continue
                stamp=parse_time(order.get('created_at'));date=stamp.astimezone(IST).strftime('%Y%m%d') if stamp else ''
                invoice=current_by_guid.get(order.get('tally_guid'))
                billed=bool(invoice and invoice['state']=='active' and invoice['type']=='Sales')
                status='invoiced' if billed else 'rejected' if order['status']=='rejected' else 'needs_check' if order.get('invoice_error') or order['status']=='invoiced' else 'waiting'
                value=invoice['total'] if billed else order['total']
                hours=max(0,(dt.datetime.now(IST)-stamp).total_seconds()/3600) if stamp else None
                item=dict(order=order['order_no'],party=order_party,rep=salesman_name,route=area,status=status,value=value,age_hours=round(hours,1) if hours is not None else None,error=order.get('invoice_error'))
                if status in ('waiting','needs_check'): all_waiting.append(item)
                if start<=date<=end:
                    counts[status]+=1;totals[status]+=value;pipeline.append(item)
            scoped_orders=len(pipeline)
            waiting=sorted(all_waiting,key=lambda r:r['age_hours'] if r['age_hours'] is not None else -1,reverse=True)
            team_by_name={r['name']:r for r in team}
            for row in pipeline:
                if row['rep'] not in team_by_name:
                    team_by_name[row['rep']]=dict(name=row['rep'],**metrics([]),prior_gross=0,change=None,share=0)
            goals=self.goals(start,end,route) if not party and mode!='all' else {}
            for name,row in team_by_name.items():
                cohort=[o for o in pipeline if o['rep']==name];unresolved=[o for o in waiting if o['rep']==name]
                row.update(target=goals.get(name),target_progress=round(row['net']/goals[name]*100,1) if goals.get(name) else None,submitted=len(cohort),billed_orders=sum(o['status']=='invoiced' for o in cohort),waiting_orders=len(unresolved),pending_value=round(sum(o['value'] for o in unresolved),2),completion=round(sum(o['status']=='invoiced' for o in cohort)/len(cohort)*100,1) if cohort else None)
            team=sorted(team_by_name.values(),key=lambda r:-r['gross'])
            target=goals.get(rep)
            result=dict(start=start,end=end,prior_start=prior_start,prior_end=prior_end,comparison=comparison,route=route,rep=rep,company=states.get('company','Local company'),asof=dt.datetime.now(IST).isoformat(),stock_asof=max((p.get('stock_asof') or '' for p in products.values()),default=None),routes=options_routes,reps=options_reps,metrics=now,previous=prior,growth=growth,baseline_covered=complete,earliest_sale=earliest,daily=trend,customers=customers,team=team,territories=routes,products=product_list,missing_inventory_vouchers=missing,target=target,target_progress=round(now['net']/target*100,1) if target else None,pipeline=pipeline,pipeline_counts=dict(counts),pipeline_values=dict(totals),billing_completion=round(counts['invoiced']/scoped_orders*100,1) if scoped_orders else None,waiting=waiting,unallocated_scope_credits=round(sum(v['total'] for v in active if v['type']=='Credit Note' and salesman(v)=='Unattributed' and start<=(v['date'] or '')<=end and (not route or territory(v)==route) and (not party or party==v['party'])),2),unattributed_gross=round(sum(v['total'] for v in current_sales if salesman(v)=='Unattributed'),2),unattributed_credits=round(sum(v['total'] for v in selected if v['type']=='Credit Note' and salesman(v)=='Unattributed'),2),warnings=['Sales use current active Sales vouchers; credit notes are shown separately. Cancelled, deleted and optional vouchers are excluded.','Salesman credit comes only from an exact linked order. Direct Tally entries remain Unattributed. Per-salesman net does not allocate unlinked credit notes.','Product totals use inventory-line amounts; invoice totals may include additional ledgers. Quantities are separated by unit.','Customer balances and stock are latest local snapshots, not historical balances for the selected period. First observed purchase is not proof of a new customer.','Billing completion describes the selected website-order cohort, not an opportunity win rate. Waiting-work alerts cover all dates within the chosen salesman/route scope.','Growth uses available local history and is hidden when the baseline starts before the earliest observed sale or the previous net amount is zero. No assurance of complete historical coverage is implied.'])
            result.update(mode=mode,year=str(chosen) if mode=='year' else '',years=years,party=party,party_options=party_options,monthly=monthly,yearly=yearly,party_products=purchase_list,party_invoices=[dict(date=v['date'],number=v.get('number'),type=v['type'],total=v['total']) for v in sorted(selected,key=lambda v:(v['date'] or '',v['version']),reverse=True)] if party else [])
            if mode=='all': result.update(growth=None,baseline_covered=False)
            return result
