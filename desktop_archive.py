"""Read-only desktop interface to the complete local archive. No cloud/Tally calls."""
import csv
import datetime as dt
import gzip
import io
import json
import re
import sqlite3
import threading
from contextlib import closing, contextmanager
from pathlib import Path

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def unpack(value):
    if isinstance(value, bytes):
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(value)) as stream:
                # A 16 MB read allocated a huge buffer for every small record.
                # Bound total expansion without paying that cost 10,000 times.
                chunks=[];size=0
                while chunk:=stream.read(64 * 1024):
                    size+=len(chunk)
                    if size>16 * 1024 * 1024:
                        return {'error': 'Record exceeds the 16 MB display limit'}
                    chunks.append(chunk)
                raw=b''.join(chunks)
            return json.loads(raw)
        except (OSError, ValueError, EOFError):
            return {'binary_bytes': len(value)}
    return value


def number(value):
    m = re.search(r'-?[\d,]+(?:\.\d+)?', str(value or ''))
    return float(m[0].replace(',', '')) if m else None


def state(record, deleted=False):
    return ('deleted' if deleted else 'cancelled' if record.get('ISCANCELLED') == 'Yes'
            else 'optional' if record.get('ISOPTIONAL') == 'Yes' else 'active')


def owner(note):
    match = re.match(r'^\[([^\]]+)\]', note or '')
    return match[1] if match else 'Unattributed'


def search_rank(name, query):
    import unicodedata
    normal=lambda value: ''.join(c for c in unicodedata.normalize('NFKD',value) if not unicodedata.combining(c)).lower().strip()
    name,query=normal(name),normal(query)
    if name==query:return 0
    if name.startswith(query):return 1
    words=re.findall(r'[^\W_]+',name)
    return 2 if all(any(w.startswith(t) for w in words) for t in query.split()) else 3


def sort_value(value):
    return (0, value) if isinstance(value,(int,float)) else (1, str(value or '').casefold())


class Archive:
    def __init__(self, path):
        self.path = Path(path).expanduser().resolve(strict=True)
        self._lock = threading.Lock()
        self._current = []
        self._signature = None
        self._metadata = {}
        with self.read() as conn:
            if not {'voucher', 'master', 'sync_state'} <= self.tables(conn).keys():
                raise ValueError('Choose a Saarthi tally_raw.db archive')

    @contextmanager
    def read(self):
        with closing(sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True, timeout=5)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA query_only=ON')
            conn.execute('BEGIN')
            yield conn

    @staticmethod
    def tables(conn):
        return {r['name']: r['sql'] for r in conn.execute(
            "SELECT name,sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}

    @staticmethod
    def rows(conn, table):
        return [dict(r) for r in conn.execute('SELECT * FROM "' + table.replace('"', '""') + '"')]

    def current(self, conn):
        # Archive versions are immutable. Decode only new (GUID, AlterID) pairs;
        # an unchanged archive must not be rebuilt just because time passed.
        with self._lock:
            tables = self.tables(conn)
            deleted = {r[0] for r in conn.execute('SELECT guid FROM deleted_voucher')} if 'deleted_voucher' in tables else set()
            version=tuple(conn.execute('SELECT MAX(alterid),COUNT(*) FROM voucher').fetchone())
            signature=(version,frozenset(deleted))
            if signature==self._signature:return self._current
            rows = conn.execute('SELECT v.guid,v.alterid,v.vdate,v.vchtype,v.party,v.amount,v.fetched_at FROM voucher v JOIN '
                '(SELECT guid,MAX(alterid) a FROM voucher GROUP BY guid) x '
                'ON v.guid=x.guid AND v.alterid=x.a').fetchall()
            missing=[r['guid'] for r in rows if (r['guid'],r['alterid']) not in self._metadata]
            metadata=dict(self._metadata)
            for offset in range(0,len(missing),400):
                batch=missing[offset:offset+400]
                for source in conn.execute('SELECT v.guid,v.alterid,v.blob FROM voucher v '
                    'WHERE v.guid IN ('+','.join('?' for _ in batch)+') AND v.alterid='
                    '(SELECT MAX(x.alterid) FROM voucher x WHERE x.guid=v.guid)',batch):
                    rec=unpack(source['blob'])
                    metadata[(source['guid'],source['alterid'])]=(rec.get('VOUCHERNUMBER'),state(rec))
            current = []
            for row in rows:
                number,source_state=metadata[(row['guid'],row['alterid'])]
                current.append(dict(guid=row['guid'], version=row['alterid'], date=row['vdate'],
                    type=row['vchtype'], party=row['party'], total=abs(row['amount'] or 0),
                    number=number, state='deleted' if row['guid'] in deleted else source_state,
                    observed_at=row['fetched_at']))
            self._metadata={(r['guid'],r['alterid']):metadata[(r['guid'],r['alterid'])] for r in rows}
            self._current,self._signature=current,signature
            return current

    def overview(self, start='', end=''):
        today = dt.datetime.now(IST).date()
        start = start or today.replace(day=1).strftime('%Y%m%d')
        end = end or today.strftime('%Y%m%d')
        if not re.fullmatch(r'\d{8}', start) or not re.fullmatch(r'\d{8}', end) or start > end:
            raise ValueError('Choose a valid date range')
        first=dt.datetime.strptime(start,'%Y%m%d').date()
        last=dt.datetime.strptime(end,'%Y%m%d').date()
        if (last-first).days>3660:raise ValueError('Select at most ten years at a time')
        with self.read() as conn:
            current = self.current(conn)
            active = [v for v in current if v['state'] == 'active' and start <= (v['date'] or '') <= end]
            sales = [v for v in active if v['type'] == 'Sales']
            receipts = [v for v in active if v['type'] == 'Receipt']
            credits = [v for v in active if v['type'] == 'Credit Note']
            daily, parties = {}, {}
            for v in sales:
                d = daily.setdefault(v['date'],dict(date=v['date'],total=0,count=0))
                d['total'] += v['total']; d['count'] += 1
                parties[v['party']] = parties.get(v['party'],0) + v['total']
            # Preserve calendar spacing: missing dates are not compressed away.
            for offset in range((last-first).days+1):
                key=(first+dt.timedelta(days=offset)).strftime('%Y%m%d')
                daily.setdefault(key,dict(date=key,total=0,count=0))
            tables = self.tables(conn)
            orders = self.rows(conn,'shop_order') if 'shop_order' in tables else []
            states = dict(conn.execute('SELECT key,value FROM sync_state'))
            stock_at = conn.execute('SELECT MAX(observed_at) FROM stock_snapshot').fetchone()[0] if 'stock_snapshot' in tables else None
            return dict(company=states.get('company','Company not recorded'), database=str(self.path),
                asof=dt.datetime.now(IST).isoformat(), start=start,end=end,
                sales=round(sum(v['total'] for v in sales),2),sales_count=len(sales),
                receipts=round(sum(v['total'] for v in receipts),2),
                credits=round(sum(v['total'] for v in credits),2),
                pending=sum(o['status'] not in ('invoiced','rejected','draft') for o in orders),
                failed=sum(bool(o.get('invoice_error')) and o['status']!='invoiced' for o in orders),
                versions=conn.execute('SELECT COUNT(*) FROM voucher').fetchone()[0],
                current_vouchers=len(current),stock_asof=stock_at,
                daily=sorted(daily.values(),key=lambda x:x['date']),
                top_parties=[dict(name=n,total=a) for n,a in sorted(parties.items(),key=lambda x:-x[1])[:8]],
                recent=sorted(current,key=lambda v:((v['date'] or ''),v['version']),reverse=True)[:12],
                sync=states)

    def masters(self, conn, kind):
        gone = {(r['objtype'],r['guid']) for r in self.rows(conn,'deleted_master')} if 'deleted_master' in self.tables(conn) else set()
        return [dict(r) for r in conn.execute('SELECT m.* FROM master m JOIN '
            '(SELECT guid,MAX(alterid) a FROM master WHERE objtype=? GROUP BY guid) x '
            'ON m.guid=x.guid AND m.alterid=x.a WHERE m.objtype=?',(kind,kind))
            if (kind,r['guid']) not in gone]

    def catalogue(self, conn, kind):
        tables = self.tables(conn)
        if kind == 'products':
            from desktop_workflows import our_prices
            explicit_prices=our_prices(self.path)
            masters=self.masters(conn,'StockItem')
            stock = self.latest_snapshots(conn,'stock_snapshot',[r['guid'] for r in masters]) if 'stock_snapshot' in tables else {}
            rates = {r['item']:r for r in self.rows(conn,'item_invoice_rate')} if 'item_invoice_rate' in tables else {}
            result = []
            for r in masters:
                rec, st, rate = unpack(r['blob']), stock.get(r['guid'],{}), rates.get(r['name'],{})
                explicit=explicit_prices.get(r['guid'])
                result.append(dict(id=r['guid'],name=r['name'],group=r['parent'],unit=rec.get('BASEUNITS'),
                    stock=st.get('qty'),stock_asof=st.get('observed_at'),
                    our_rate=explicit,
                    rate=explicit if explicit is not None else rate.get('last_rate'),
                    rate_date=rate.get('last_date') if explicit is None else None,
                    last_sold_rate=rate.get('last_rate'),last_sold_date=rate.get('last_date'),
                    version=r['alterid']))
            from purchase_costs import enrich_products
            return enrich_products(self,conn,result)
        masters=self.masters(conn,'Ledger')
        balances = self.latest_snapshots(conn,'party_balance',[r['guid'] for r in masters]) if 'party_balance' in tables else {}
        return [dict(id=r['guid'],name=r['name'],group=r['parent'],version=r['alterid'],
            balance=balances.get(r['guid'],{}).get('closing'),
            balance_asof=balances.get(r['guid'],{}).get('observed_at')) for r in masters]

    @staticmethod
    def latest_snapshots(conn, table, guids):
        if table not in ('stock_snapshot','party_balance'):raise ValueError('Unknown snapshot table')
        # Snapshot primary keys start with observed_at: read the newest round
        # using that index, instead of scanning 200,000 historical rows.
        latest={r['guid']:dict(r) for r in conn.execute(
            f'SELECT * FROM {table} WHERE observed_at=(SELECT MAX(observed_at) FROM {table})')}
        # Partial rounds and newly removed parties must retain each item's
        # own last observation, with its actual older timestamp.
        for guid in set(guids)-latest.keys():
            row=conn.execute(f'SELECT * FROM {table} WHERE guid=? ORDER BY observed_at DESC LIMIT 1',(guid,)).fetchone()
            if row:latest[guid]=dict(row)
        return latest

    def listing(self, kind, q='', page=1, size=50, category='', start='', end='', sort='', direction='asc'):
        page, size = max(1,int(page)), min(100,max(1,int(size)))
        with self.read() as conn:
            if kind == 'vouchers':
                rows = list(self.current(conn))
                categories = sorted({r['type'] or '' for r in rows if r['state'] != 'deleted'})
                if category == 'deleted':
                    rows = [r for r in rows if r['state'] == 'deleted']
                else:
                    rows = [r for r in rows if r['state'] != 'deleted' and (not category or r['type'] == category)]
                if start: rows = [r for r in rows if (r['date'] or '') >= start]
                if end: rows = [r for r in rows if (r['date'] or '') <= end]
                rows.sort(key=lambda r:((r['date'] or ''),r['version']),reverse=True)
            elif kind == 'orders':
                rows = self.rows(conn,'shop_order') if 'shop_order' in self.tables(conn) else []
                invoices = {v['guid']:v for v in self.current(conn)}
                linked = {r.get('tally_guid') for r in rows if r.get('tally_guid')}
                for r in rows:
                    r['source'] = 'Website'
                    r['detail_kind'], r['detail_id'] = 'orders', r['order_no']
                    r['salesperson'] = owner(r.get('note'))
                    r['invoice'] = invoices.get(r.get('tally_guid'))
                    r['display_total'] = (r['invoice']['total'] if r['invoice']['state']=='active' else None) if r['invoice'] else (None if r['status']=='invoiced' else r['total'])
                    if r['invoice']:
                        r['status'] = 'invoiced' if r['invoice']['state']=='active' else r['invoice']['state']
                        r['tally_vchno'] = r['invoice']['number']
                    r['date'] = r['invoice']['date'] if r['invoice'] else (r['created_at'] or '')[:10].replace('-','')
                # One current row per invoice identity. A website order linked
                # to this voucher already represents it; never append it twice.
                for v in invoices.values():
                    if v['type'] != 'Sales' or v['guid'] in linked: continue
                    rows.append(dict(order_no='Tally '+(v['number'] or 'unnumbered'),
                        created_at=None,date=v['date'],party_name=v['party'],salesperson='Unattributed',
                        source='Tally',detail_kind='vouchers',detail_id=v['guid'],invoice=v,
                        tally_guid=v['guid'],tally_vchno=v['number'],
                        status='invoiced' if v['state']=='active' else v['state'],
                        display_total=v['total'] if v['state']=='active' else None))
                categories = sorted({r['status'] for r in rows})
                if category: rows=[r for r in rows if r['status']==category]
                else: rows=[r for r in rows if r['status']!='deleted']
                if start: rows=[r for r in rows if (r['date'] or '')>=start]
                if end: rows=[r for r in rows if (r['date'] or '')<=end]
                rows.sort(key=lambda r:(r['date'] or '',r['created_at'] or '',r['detail_id']),reverse=True)
            elif kind in ('products','parties'):
                rows = self.catalogue(conn,kind); categories=sorted({r['group'] or '' for r in rows})
                if category: rows=[r for r in rows if r['group']==category]
                rows.sort(key=lambda r:(r['name'] or '').lower())
            else:
                raise ValueError('Unknown listing')
            terms = q.lower().split()
            if terms:
                rows = [r for r in rows if all(t in json.dumps(r,ensure_ascii=False).lower() for t in terms)]
            allowed = {'vouchers': ('date','number','type','party','total','state'),
                       'orders': ('order_no','date','source','party_name','salesperson','status','display_total','tally_vchno'),
                       'products': ('name','group','unit','stock','rate','rate_date','last_sold_rate','last_sold_date'),
                       'parties': ('name','group','balance','balance_asof')}[kind]
            if sort:
                if sort not in allowed or direction not in ('asc','desc'): raise ValueError('Choose a valid sort')
                rows.sort(key=lambda r: sort_value(r.get(sort)),reverse=direction=='desc')
                rows.sort(key=lambda r:r.get(sort) is None)
            if terms:
                rows.sort(key=lambda r: search_rank(r.get('name') or r.get('party_name') or r.get('party') or '',q))
            return dict(rows=rows[(page-1)*size:page*size],total=len(rows),page=page,size=size,categories=categories)

    def detail(self, kind, identity, version=None):
        with self.read() as conn:
            if kind == 'vouchers':
                sql='SELECT * FROM voucher WHERE guid=?'; args=[identity]
                if version is not None: sql+=' AND alterid=?';args.append(int(version))
                row=conn.execute(sql+' ORDER BY alterid DESC LIMIT 1',args).fetchone()
                if not row: raise ValueError('Voucher not found')
                result=dict(row); result['record']=unpack(result.pop('blob'))
                result['versions']=[dict(r) for r in conn.execute(
                    'SELECT alterid,vdate,amount,fetched_at FROM voucher WHERE guid=? ORDER BY alterid DESC',(identity,))]
                gone=conn.execute('SELECT 1 FROM deleted_voucher WHERE guid=?',(identity,)).fetchone() if 'deleted_voucher' in self.tables(conn) else None
                result['state']=state(result['record'],bool(gone))
                result['raw_versions']=[r[0] for r in conn.execute('SELECT alterid FROM voucher_raw WHERE guid=? ORDER BY alterid DESC',(identity,))] if 'voucher_raw' in self.tables(conn) else []
                return result
            if kind == 'orders':
                row=conn.execute('SELECT * FROM shop_order WHERE order_no=?',(identity,)).fetchone()
                if not row: raise ValueError('Order not found')
                result=dict(row)
                result['submitted_lines']=[dict(r) for r in conn.execute('SELECT * FROM shop_order_line WHERE order_no=? ORDER BY line_no',(identity,))]
                result['invoice']=None
                if result.get('tally_guid'):
                    try:result['invoice']=self.detail('vouchers',result['tally_guid'])
                    except ValueError:result['invoice']={'unavailable':True}
                return result
            kind_name={'products':'StockItem','parties':'Ledger'}.get(kind)
            if not kind_name: raise ValueError('Unknown detail')
            rows=[r for r in self.masters(conn,kind_name) if r['guid']==identity]
            if not rows: raise ValueError('Record not found')
            result=rows[0];result['record']=unpack(result.pop('blob'))
            result['vouchers']=[v for v in self.current(conn) if v['party']==result['name']] if kind=='parties' else []
            result['facts']={}
            for table in ('party_item_rate','party_ageing','party_profile','party_buy'):
                if table in self.tables(conn):
                    field='party' if kind=='parties' else 'item'
                    columns={r['name'] for r in conn.execute(f'PRAGMA table_info("{table}")')}
                    if field in columns: result['facts'][table]=[dict(r) for r in conn.execute(f'SELECT * FROM "{table}" WHERE "{field}"=?',(result['name'],))]
            return result

    def explorer(self, table='', page=1, q='', rowid=None, sort='', direction='desc'):
        with self.read() as conn:
            tables=self.tables(conn)
            if not table:
                return [dict(name=t,rows=conn.execute('SELECT COUNT(*) FROM "'+t.replace('"','""')+'"').fetchone()[0],schema=s) for t,s in sorted(tables.items())]
            if table not in tables: raise ValueError('Unknown table')
            quoted='"'+table.replace('"','""')+'"'
            cols=[dict(r) for r in conn.execute('PRAGMA table_info('+quoted+')')]
            if rowid is not None:
                row=conn.execute('SELECT * FROM '+quoted+' WHERE rowid=?',(int(rowid),)).fetchone()
                if not row: raise ValueError('Row not found')
                return {k:unpack(v) for k,v in dict(row).items()}
            fields=['"'+r['name'].replace('"','""')+'"' for r in cols if r['type'].upper()!='BLOB']
            where=' WHERE '+' OR '.join('instr(lower(CAST('+f+' AS TEXT)),?)>0' for f in fields) if q and fields else ''
            args=[q.lower()]*len(fields) if where else []
            total=conn.execute('SELECT COUNT(*) FROM '+quoted+where,args).fetchone()[0]
            if sort and sort not in {c['name'] for c in cols}:raise ValueError('Choose a valid sort column')
            if direction not in ('asc','desc'):raise ValueError('Choose a valid sort direction')
            order='"'+sort.replace('"','""')+'"' if sort else 'rowid'
            rows=[dict(r) for r in conn.execute('SELECT rowid AS __rowid__,* FROM '+quoted+where+' ORDER BY '+order+' '+direction+' LIMIT 50 OFFSET ?',args+[(max(1,int(page))-1)*50])]
            for r in rows:
                for k,v in r.items():
                    if isinstance(v,bytes):r[k]=f'{len(v):,} compressed bytes · open row to inspect'
            return dict(rows=rows,columns=cols,total=total,page=max(1,int(page)),size=50)

    def export(self, table):
        with self.read() as conn:
            if table not in self.tables(conn): raise ValueError('Unknown table')
            cursor=conn.execute('SELECT * FROM "'+table.replace('"','""')+'"')
            out=io.StringIO();writer=csv.writer(out);writer.writerow([c[0] for c in cursor.description])
            yield '\ufeff'+out.getvalue();out.seek(0);out.truncate(0)
            for row in cursor:
                values=[]
                for value in row:
                    if isinstance(value,bytes):value=json.dumps(unpack(value),ensure_ascii=False)
                    if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@')):value="'"+value
                    values.append(value)
                writer.writerow(values);yield out.getvalue();out.seek(0);out.truncate(0)

    def backup(self, directory):
        directory=Path(directory).resolve();directory.mkdir(parents=True,exist_ok=True)
        target=directory/('saarthi-'+dt.datetime.now(IST).strftime('%Y%m%d-%H%M%S-%f')+'.sqlite')
        with self.read() as source, closing(sqlite3.connect(target)) as dest:
            source.backup(dest)
            if dest.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Backup validation failed')
        return dict(path=str(target),bytes=target.stat().st_size)
