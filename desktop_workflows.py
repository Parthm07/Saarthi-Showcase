"""Durable admin requests and local product media. Only the agent writes Tally."""
import base64
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from contextlib import contextmanager,closing
from decimal import Decimal,InvalidOperation,ROUND_HALF_UP
from uuid import UUID

from desktop_archive import unpack

SCHEMA='''
CREATE TABLE IF NOT EXISTS command (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'queued', created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL, error TEXT, result TEXT);
CREATE INDEX IF NOT EXISTS command_state ON command(state,created_at);
CREATE TABLE IF NOT EXISTS product_image (
 guid TEXT PRIMARY KEY, mime TEXT NOT NULL, body BLOB NOT NULL,
 updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS inventory_policy(
 guid TEXT PRIMARY KEY,minimum REAL NOT NULL DEFAULT 0,target REAL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS website_hidden_product(guid TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS product_our_rate(guid TEXT PRIMARY KEY, price REAL NOT NULL);
CREATE TABLE IF NOT EXISTS website_rate_receipt(guid TEXT PRIMARY KEY, revision TEXT NOT NULL);
'''
KINDS=('sale','purchase','party_create','product_create','product_update')


def stamp():return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')


def text(value,label,maximum=200,required=True):
    if not isinstance(value,str):raise ValueError(label+' must be text')
    value=value.strip()
    if (required and not value) or len(value)>maximum or any(ord(c)<32 for c in value):
        raise ValueError('Check '+label)
    return value


def number(value,label):
    try:value=Decimal(str(value))
    except (InvalidOperation,ValueError):raise ValueError('Check '+label) from None
    if not value.is_finite() or value<=0 or value>Decimal('10000000'):raise ValueError(label+' must be positive and at most 10,000,000')
    return value


def amount(value):return str(value.quantize(Decimal('.01'),rounding=ROUND_HALF_UP))


def descendants(groups,root):
    found={root}
    while True:
        more={r['name'] for r in groups if r['parent'] in found}-found
        if not more:return found
        found.update(more)


class Workflows:
    def __init__(self,archive):
        self.archive=archive;self.path=archive.path.parent/'saarthi_admin.db'

    @contextmanager
    def db(self):
        conn=sqlite3.connect(self.path,timeout=10);conn.row_factory=sqlite3.Row
        try:
            conn.executescript(SCHEMA)
            with conn:yield conn
        finally:conn.close()

    def context(self):
        with self.archive.read() as conn:
            groups=self.archive.masters(conn,'Group')
            customers=descendants(groups,'Sundry Debtors');suppliers=descendants(groups,'Sundry Creditors')
            sales=descendants(groups,'Sales Accounts');purchases=descendants(groups,'Purchase Accounts')
            ledgers=self.archive.catalogue(conn,'parties');products=self.archive.catalogue(conn,'products')
            records={r['guid']:unpack(r['blob']) for r in self.archive.masters(conn,'StockItem')}
            from tally_shop import reservations
            tables = self.archive.tables(conn)
            held = reservations(conn) if {'shop_order','shop_order_line'} <= tables.keys() else {}
            for p in products:
                p['standard_price']=records[p['id']].get('STANDARDPRICE')
                p['order_load']=held.get(p['id'], 0)
            states=dict(conn.execute('SELECT key,value FROM sync_state'))
            return dict(company=states.get('company'),database=str(self.archive.path),
                enabled=self.archive.path.name=='tally_raw.db',products=products,
                customers=[p for p in ledgers if p['group'] in customers],
                suppliers=[p for p in ledgers if p['group'] in suppliers],
                sales_ledgers=[p for p in ledgers if p['group'] in sales],
                purchase_ledgers=[p for p in ledgers if p['group'] in purchases],
                customer_groups=sorted(customers),supplier_groups=sorted(suppliers),
                stock_groups=sorted({'Primary'}|{r['name'] for r in self.archive.masters(conn,'StockGroup')}),
                units=sorted({r['name'] for r in self.archive.masters(conn,'Unit')}),
                godowns=sorted({r['name'] for r in self.archive.masters(conn,'Godown')}))

    def validate(self,kind,body,context=None):
        if kind not in KINDS:raise ValueError('Unknown admin operation')
        if not isinstance(body,dict):raise ValueError('Entry details must be an object')
        ctx=context or self.context()
        if not ctx['enabled'] or not ctx['company']:raise ValueError('Choose the active tally_raw.db with its company recorded')
        out={'company':ctx['company']}
        def member(value,rows,label):
            row=next((r for r in rows if r['id']==value),None)
            if not row:raise ValueError('Choose an existing '+label)
            return {k:row[k] for k in ('id','name','version')}
        if kind in ('sale','purchase'):
            purchase=kind=='purchase'
            out['party']=member(body.get('party'),ctx['suppliers' if purchase else 'customers'],'supplier' if purchase else 'sales party')
            out['ledger']=member(body.get('ledger'),ctx['purchase_ledgers' if purchase else 'sales_ledgers'],'accounting ledger')
            try:out['date']=dt.date.fromisoformat(body.get('date','')).isoformat()
            except (ValueError,TypeError):raise ValueError('Choose a valid invoice date') from None
            if body.get('godown') not in ctx['godowns']:raise ValueError('Choose an existing godown')
            out['godown']=body['godown'];out['bill_no']=text(body.get('bill_no',''),'supplier bill number',80,purchase)
            out['note']=text(body.get('note',''),'note',400,False)
            lines=body.get('lines')
            if not isinstance(lines,list) or not 1<=len(lines)<=100:raise ValueError('Add between 1 and 100 product lines')
            out['lines']=[];seen=set();total=Decimal(0)
            with self.archive.read() as conn:
                records={r['guid']:unpack(r['blob']) for r in self.archive.masters(conn,'StockItem')}
                for line in lines:
                    if not isinstance(line,dict):raise ValueError('Check the product lines')
                    product=member(line.get('item'),ctx['products'],'product')
                    if product['id'] in seen:raise ValueError('Use one quantity line per product')
                    seen.add(product['id'])
                    row=next(p for p in ctx['products'] if p['id']==product['id'])
                    if not row['unit']:raise ValueError('Product has no base unit in Tally')
                    record=records[product['id']]
                    if str(record.get('ISBATCHWISEON','No')).lower()=='yes':raise ValueError('Batch-tracked products require entry in Tally')
                    qty=number(line.get('qty'),'quantity');rate=number(line.get('rate'),'rate')
                    value=amount(qty*rate)
                    if Decimal(value)<=0:raise ValueError('Line value rounds to zero')
                    total+=Decimal(value)
                    out['lines'].append(dict(**product,unit=row['unit'],qty=str(qty),rate=str(rate),amount=value))
            out['total']=amount(total)
            if total>Decimal('999999999999'):raise ValueError('Invoice total is too large')
        elif kind=='party_create':
            out['name']=text(body.get('name'),'party name')
            if body.get('group') not in ctx['customer_groups']+ctx['supplier_groups']:raise ValueError('Choose a customer or supplier group')
            out['group']=body['group'];out['phone']=text(body.get('phone',''),'phone',40,False)
            out['address']=text(body.get('address',''),'address',400,False)
        else:
            out['name']=text(body.get('name'),'product name')
            if kind=='product_create':
                if body.get('group') not in ctx['stock_groups'] or body.get('unit') not in ctx['units']:raise ValueError('Choose an existing stock group and unit')
                out.update(group=body['group'],unit=body['unit'])
            else:out['product']=member(body.get('item'),ctx['products'],'product')
            if kind=='product_update' and body.get('rate_only') is True:
                # A rate import follows the GUID, preserving later Tally renames.
                out['name']=out['product']['name']
                raw_price=str(body.get('price')).strip()
                try:price=Decimal(raw_price)
                except InvalidOperation:raise ValueError('Rate must be a number') from None
                if not price.is_finite() or '_' in raw_price:raise ValueError('Rate must be a finite number')
                out['price']=str(price)
                out['rate_only']=True
            elif body.get('price') not in (None,''):
                out['price']=amount(number(body['price'],'selling price'))
                if Decimal(out['price'])<=0:raise ValueError('Selling price must be at least 0.01')
            if 'price' in out:
                out['effective_date']=dt.datetime.now(dt.timezone(dt.timedelta(hours=5,minutes=30))).strftime('%Y%m%d')
        return out

    def submit(self,request_id,kind,body):
        try:request_id=str(UUID(request_id))
        except (ValueError,TypeError,AttributeError):raise ValueError('Valid request ID required') from None
        # Retries compare the original client fields before resolving masters;
        # a confirmed rename must not make the same request fail validation.
        encoded=json.dumps({'kind':kind,'body':body},sort_keys=True,separators=(',',':'),allow_nan=False)
        with self.db() as conn:
            existing=conn.execute('SELECT * FROM command WHERE id=?',(request_id,)).fetchone()
            if existing:
                saved=json.loads(existing['payload'])
                if saved['input']!=json.loads(encoded):raise ValueError('Request ID already belongs to different entry details')
                return self.public(existing)
        if kind=='product_create':raise ValueError('Create products in Tally; product creation is unavailable in Saarthi')
        payload=self.validate(kind,body)
        payload['input']=json.loads(encoded)
        with self.db() as conn:
            conn.execute('INSERT OR IGNORE INTO command(id,kind,payload,created_at,updated_at) VALUES(?,?,?,?,?)',
                (request_id,kind,json.dumps(payload,sort_keys=True),stamp(),stamp()))
            row=conn.execute('SELECT * FROM command WHERE id=?',(request_id,)).fetchone()
            if json.loads(row['payload'])['input']!=json.loads(encoded):raise ValueError('Request ID already belongs to another entry')
            return self.public(row)

    @staticmethod
    def public(row):
        p=json.loads(row['payload'])
        return {**{k:row[k] for k in ('id','kind','state','created_at','updated_at','error')},
            'summary':p.get('name') or p.get('party',{}).get('name'),
            'total':p.get('total'),'result':json.loads(row['result']) if row['result'] else None}

    def history(self):
        with self.db() as conn:return [self.public(r) for r in conn.execute('SELECT * FROM command ORDER BY created_at DESC,rowid DESC LIMIT 100')]

    def history_page(self,page=1,sort="",direction="asc"):
        page=max(1,int(page));size=50
        with self.db() as conn:
            total=conn.execute('SELECT COUNT(*) FROM command').fetchone()[0]
            page=min(page,max(1,(total+size-1)//size))
            if sort:
                from desktop_archive import sort_value
                if sort not in ('created_at','kind','summary','total','state') or direction not in ('asc','desc'):raise ValueError('Choose a valid request sort')
                rows=[self.public(r) for r in conn.execute('SELECT * FROM command')]
                rows.sort(key=lambda r:sort_value(float(r['total']) if sort=='total' and r.get('total') is not None else r.get(sort)),reverse=direction=='desc')
                rows.sort(key=lambda r:r.get(sort) is None)
                return dict(rows=rows[(page-1)*size:page*size],page=page,size=size,total=total)
            rows=conn.execute("SELECT * FROM command ORDER BY CASE WHEN state='confirmed' THEN 1 ELSE 0 END,created_at DESC,rowid DESC LIMIT ? OFFSET ?",(size,(page-1)*size)).fetchall()
            return dict(rows=[self.public(r) for r in rows],page=page,size=size,total=total)

    def detail(self,request_id):
        with self.db() as conn:
            row=conn.execute('SELECT * FROM command WHERE id=?',(request_id,)).fetchone()
            if not row:raise ValueError('Entry not found')
            return {**self.public(row),'details':json.loads(row['payload'])}

    def retry(self,request_id):
        with self.db() as conn:
            row=conn.execute('SELECT * FROM command WHERE id=?',(request_id,)).fetchone()
            if not row:raise ValueError('Entry not found')
            if row['state'] not in ('failed','uncertain'):raise ValueError('This entry is already queued or confirmed')
            # Uncertain imports are reconciliation-only. Never resend them.
            state='processing' if row['state']=='uncertain' else 'queued'
            conn.execute('UPDATE command SET state=?,updated_at=?,error=NULL WHERE id=?',(state,stamp(),request_id))
        return {'queued':True}

    def image(self,guid):
        with self.db() as conn:
            row=conn.execute('SELECT mime,body FROM product_image WHERE guid=?',(guid,)).fetchone()
            return (row['mime'],row['body']) if row else None

    def save_image(self,guid,encoded):
        with self.archive.read() as conn:
            if guid not in {r['guid'] for r in self.archive.masters(conn,'StockItem')}:raise ValueError('Choose a product extracted from Tally')
        try:body=base64.b64decode(encoded,validate=True)
        except (ValueError,TypeError):raise ValueError('Invalid image upload') from None
        if not body or len(body)>1500000:raise ValueError('Image must be smaller than 1.5 MB')
        mime=('image/png' if body.startswith(b'\x89PNG\r\n\x1a\n') else
              'image/jpeg' if body.startswith(b'\xff\xd8\xff') else
              'image/webp' if body.startswith(b'RIFF') and body[8:12]==b'WEBP' else None)
        if not mime:raise ValueError('Choose a PNG, JPEG or WebP image')
        with self.db() as conn:conn.execute('INSERT INTO product_image VALUES(?,?,?,?) ON CONFLICT(guid) DO UPDATE SET mime=excluded.mime,body=excluded.body,updated_at=excluded.updated_at',(guid,mime,body,stamp()))
        return {'saved':True}

    def backup(self,directory):
        directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
        path=directory/('saarthi-admin-'+dt.datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.db')
        with self.db() as source,closing(sqlite3.connect(path)) as target:
            source.backup(target)
            if target.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Admin backup verification failed')
        return str(path)


def website_hidden_products(database):
    path = Path(database).resolve().parent / 'saarthi_admin.db'
    if not path.is_file():
        return set()
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=5)) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='website_hidden_product'").fetchone():
            return set()
        return {row[0] for row in conn.execute('SELECT guid FROM website_hidden_product')}


def our_prices(database):
    """Saved Saarthi prices take precedence over previously confirmed imports."""
    prices = confirmed_prices(database)
    path = Path(database).parent / 'saarthi_admin.db'
    if path.is_file():
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=5)) as conn:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='product_our_rate'").fetchone():
                prices.update(conn.execute('SELECT guid,price FROM product_our_rate'))
    return prices


def confirmed_prices(database):
    """Explicit future selling prices, keyed by immutable Tally product GUID."""
    path=Path(database).parent/'saarthi_admin.db'
    if not path.exists():return {}
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=5)) as conn:
        # A concurrent first history request can create the file just before
        # its first schema statement. No confirmed price exists in that gap.
        tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not tables:return {}
        if 'command' not in tables:raise ValueError('Admin request database is missing its command table')
        rows=conn.execute("SELECT kind,payload,result FROM command WHERE state='confirmed' AND kind IN ('product_create','product_update') ORDER BY updated_at,rowid").fetchall()
    out={}
    for kind,payload,result in rows:
        p=json.loads(payload);result=json.loads(result or '{}')
        if 'price' in p and result.get('guid'):
            price=float(p['price'])
            if math.isfinite(price):out[result['guid']]=price
    return out
