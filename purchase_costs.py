"""Read-only purchase-invoice rates and explicitly estimated inventory margins."""
from bisect import bisect_right
from collections import defaultdict
import re
import datetime as dt
from desktop_archive import number, unpack, IST

CLOUD_SCHEMA = '''
CREATE TABLE IF NOT EXISTS owner_purchase_rate (
 guid TEXT PRIMARY KEY REFERENCES item(guid) ON DELETE CASCADE,
 rate NUMERIC(18,6) NOT NULL, unit TEXT NOT NULL, vdate TEXT NOT NULL,
 voucher_guid TEXT NOT NULL, source_alterid BIGINT NOT NULL);
'''


def quantity(line, fallback=''):
    raw=str(line.get('BILLEDQTY') or line.get('ACTUALQTY') or '')
    value=number(raw)
    unit=re.sub(r'^\s*-?[\d,.]+\s*','',raw).split('=')[0].strip() or fallback
    return value,unit


def history(archive, conn):
    vouchers=[v for v in archive.current(conn) if v['type']=='Purchase' and v['state']=='active' and v['date']]
    masters=archive.masters(conn,'StockItem')
    signature=(tuple((v['guid'],v['version']) for v in vouchers),tuple((m['guid'],m['alterid']) for m in masters))
    with archive._lock:
        cached=getattr(archive,'_purchase_cost_cache',None)
        if cached and cached[0]==signature:return cached[1]
        units={m['name']:unpack(m['blob']).get('BASEUNITS') or '' for m in masters}
        result=defaultdict(list)
        for v in vouchers:
            raw=conn.execute('SELECT blob FROM voucher WHERE guid=? AND alterid=?',(v['guid'],v['version'])).fetchone()
            record=unpack(raw[0]);lines=record.get('ALLINVENTORYENTRIES.LIST') or record.get('INVENTORYENTRIES.LIST') or []
            sums=defaultdict(lambda:[0,0])
            for line in lines if isinstance(lines,list) else [lines]:
                name=line.get('STOCKITEMNAME');qty,unit=quantity(line,units.get(name,''));amount=number(line.get('AMOUNT'))
                if not name or not qty or qty<=0 or not unit or amount is None:continue
                sums[name,unit][0]+=qty;sums[name,unit][1]+=abs(amount)
            for (name,unit),(qty,amount) in sums.items():
                result[name,unit.casefold()].append(dict(rate=amount/qty,unit=unit,date=v['date'],guid=v['guid'],version=v['version']))
        result={key:sorted(rows,key=lambda r:(r['date'],r['version'],r['guid'])) for key,rows in result.items()}
        archive._purchase_cost_cache=(signature,result)
        return result


def latest(history, name, unit=None, asof='99999999'):
    if unit is not None:
        rows=history.get((name,unit.casefold()),[])
        pos=bisect_right([r['date'] for r in rows],asof)
        return rows[pos-1] if pos else None
    rows=[latest(history,name,u,asof) for item,u in history if item==name]
    return max((r for r in rows if r),key=lambda r:(r['date'],r['version'],r['guid']),default=None)


def enrich_products(archive,conn,products):
    costs=history(archive,conn)
    for p in products:
        cost=latest(costs,p['name'],asof=dt.datetime.now(IST).strftime('%Y%m%d'))
        p.update(purchase_rate=round(cost['rate'],6) if cost else None,purchase_unit=cost['unit'] if cost else None,purchase_date=cost['date'] if cost else None)
        rate=p.get('rate');same=bool(cost and p.get('unit') and p['unit'].casefold()==cost['unit'].casefold())
        p['estimated_margin']=round((rate-cost['rate'])/rate*100,2) if same and rate is not None and rate>0 else None
    return products


def apply_margins(archive,conn,report,selected,lines_for):
    costs=history(archive,conn);units={m['name']:unpack(m['blob']).get('BASEUNITS') or '' for m in archive.masters(conn,'StockItem')};party=defaultdict(lambda:dict(cost=0,revenue=0,missing=0));party_items=defaultdict(lambda:dict(cost=0,revenue=0,missing=0));products=defaultdict(lambda:dict(cost=0,revenue=0,missing=0))
    for v in selected:
        rows=lines_for(v)
        if not rows:party[v['party']]['missing']+=1
        for line in rows:
            name=line.get('STOCKITEMNAME');amount=number(line.get('AMOUNT'));qty,unit=quantity(line,units.get(name,''))
            if not name or amount is None:party[v['party']]['missing']+=1;continue
            sign=-1 if v['type']=='Credit Note' else 1
            rate=latest(costs,name,unit,v['date']) if unit else None
            for row in (party[v['party']],products[name],party_items[v['party'],name,unit]):
                row['revenue']+=sign*abs(amount)
                if qty is None or qty==0 or rate is None:row['missing']+=1
                else:row['cost']+=sign*abs(qty)*rate['rate']
    def values(row):
        valid=row is not None and row['missing']==0
        profit=row['revenue']-row['cost'] if valid else None
        return dict(purchase_cost=round(row['cost'],2) if valid else None,profit=round(profit,2) if valid else None,
                    margin=round(profit/row['revenue']*100,2) if valid and row['revenue']>0 else None,
                    markup=round(profit/row['cost']*100,2) if valid and row['cost']>0 else None,
                    cost_note='Estimate: latest purchase invoice on/before each sale or credit; inventory lines only' if valid else 'Unavailable: missing purchase rate, quantity or matching unit')
    for row in report['customers']:row.update(values(party.get(row['name'])))
    for row in report['products']:row.update(values(products.get(row['name'])))
    for row in report.get('party_products',[]):row.update(values(party_items.get((row['party'],row['name'],row['unit']))))
    return report


def push(local,cur,log=print):
    from desktop_archive import Archive
    from pathlib import Path
    path=local.execute('PRAGMA database_list').fetchone()[2]
    if not path:return
    cur.execute("SELECT to_regclass('public.owner_purchase_rate')")
    if not cur.fetchone()[0]:
        log('Owner purchase rates pending: apply cloud_db.py --purchase-cost-schema');return
    global _cloud_archive
    if globals().get('_cloud_archive') is None or _cloud_archive.path!=Path(path).resolve():_cloud_archive=Archive(Path(path))
    archive=_cloud_archive
    with archive.read() as conn:
        costs=history(archive,conn);masters=archive.masters(conn,'StockItem')
        rows=[]
        for m in masters:
            cost=latest(costs,m['name'],asof=dt.datetime.now(IST).strftime('%Y%m%d'))
            if cost:rows.append((m['guid'],cost['rate'],cost['unit'],cost['date'],cost['guid'],cost['version']))
    cur.execute('CREATE TEMP TABLE t_owner_cost (LIKE owner_purchase_rate) ON COMMIT DROP')
    if rows:cur.executemany('INSERT INTO t_owner_cost VALUES(%s,%s,%s,%s,%s,%s)',rows)
    cur.execute('INSERT INTO owner_purchase_rate SELECT t.* FROM t_owner_cost t JOIN item i ON i.guid=t.guid WHERE TRUE ON CONFLICT(guid) DO UPDATE SET rate=excluded.rate,unit=excluded.unit,vdate=excluded.vdate,voucher_guid=excluded.voucher_guid,source_alterid=excluded.source_alterid WHERE (owner_purchase_rate.rate,owner_purchase_rate.unit,owner_purchase_rate.vdate,owner_purchase_rate.voucher_guid,owner_purchase_rate.source_alterid) IS DISTINCT FROM (excluded.rate,excluded.unit,excluded.vdate,excluded.voucher_guid,excluded.source_alterid)')
    cur.execute('DELETE FROM owner_purchase_rate WHERE guid NOT IN (SELECT guid FROM t_owner_cost)')
