"""Seven-day cloud details backed by durable local history and compact retries.

Only the laptop runs cleanup. No Tally calls, no deletion of local records.
The caller owns the cloud transaction; any failure rolls back all cloud work.
"""
import datetime as dt
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from contextlib import closing

from psycopg.types.json import Jsonb
import tally_store as store

DAYS = 7
IST = dt.timezone(dt.timedelta(hours=5,minutes=30))


def backup_archive(local, directory):
    """Atomic, checked cumulative daily copy; original history is never pruned."""
    if local.in_transaction:
        raise ValueError('Commit local work before making a retention backup')
    directory = Path(directory).resolve()
    directory.mkdir(parents=True,exist_ok=True)
    target = directory / (dt.datetime.now(IST).strftime('%Y-%m-%d') + '.sqlite')
    pending = target.with_suffix('.pending')
    with closing(sqlite3.connect(pending)) as copy:
        local.backup(copy)
        if copy.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Local backup integrity check failed')
    os.replace(pending,target)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix('.sha256').write_text(digest+'\n',encoding='ascii')
    archived = sqlite3.connect(target.as_uri()+'?mode=ro',uri=True)
    archived.row_factory = sqlite3.Row
    return archived,target,digest


def protected_guids(cur):
    # Full orders (including protected/late completions) retain their linked
    # invoice snapshot until they are eligible for verified archival.
    cur.execute('SELECT tally_guid FROM cloud_order WHERE tally_guid IS NOT NULL '
                'UNION SELECT guid FROM history_voucher WHERE retention_hold=TRUE')
    return {r[0] for r in cur.fetchall()}


def _latest(local,guid):
    return local.execute('SELECT * FROM voucher WHERE guid=? ORDER BY alterid DESC LIMIT 1',
                         (guid,)).fetchone()


def _confirmed(local,guid,version,amount):
    row = _latest(local,guid)
    if not row or version is None or row['alterid'] != version:
        return False
    rec = json.loads(gzip.decompress(row['blob']))
    return (row['vchtype']=='Sales' and abs(row['amount'] or 0)==float(amount)
        and rec.get('ISCANCELLED')!='Yes' and rec.get('ISOPTIONAL')!='Yes'
        and not local.execute('SELECT 1 FROM deleted_voucher WHERE guid=?',(guid,)).fetchone())


def _order_matches(local,order,lines):
    no,who,request,fingerprint,customer,total,guid,number = order[:8]
    saved = local.execute('SELECT * FROM shop_order WHERE order_no=?',(no,)).fetchone()
    if not saved or saved['status']!='invoiced' or saved['tally_guid']!=guid or saved['tally_vchno']!=number:
        return False
    if saved['party_name']!=customer or round(saved['total'],2)!=round(float(total),2):
        return False
    original = local.execute('SELECT line_no,item_guid,item_name,unit,qty,rate,amount '
        'FROM shop_order_line WHERE order_no=? ORDER BY line_no',(no,)).fetchall()
    def normalize(row):
        return tuple(row[:4])+tuple(round(float(x),6) for x in row[4:])
    return bool(lines) and [normalize(r) for r in original]==[normalize(r) for r in lines]


def cleanup(local,cur,backup_dir,now=None,log=print):
    """Archive only acknowledged, completed, verified rows; keep flags as vetoes."""
    now = now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        raise ValueError('Retention clock must include a timezone')
    company = store._get(local,'company')
    cur.execute('SELECT company FROM sync_status WHERE id=1')
    cloud_company = cur.fetchone()
    if not company or not cloud_company or cloud_company[0]!=company:
        raise ValueError('Company identities differ; retention refused')
    if (store._get(local,'facts_built_at_alterid') != (store._get(local,'voucher_alterid') or '')
            or store._get(local,'facts_built_on') != now.astimezone(IST).date().isoformat()):
        raise ValueError('Rebuild and publish current facts before retention')
    # Facts must already have been published in this transaction. Refuse to
    # erase history if a caller skipped that step or a prior push failed.
    for table,columns in (
        ('party_rate','party,item,last_rate,last_date,times,item_median'),
        ('party_buy','party,item,cyc_lo,cyc_hi,last_days,typical_qty,freq_n,freq_m,is_core'),
        ('daily_sales','vdate,party,salesperson,invoices,amount')):
        local_table = 'party_item_rate' if table=='party_rate' else table
        cur.execute('SELECT '+columns+' FROM '+table)
        key_count=3 if table=='daily_sales' else 2
        remote={tuple(r[:key_count]):tuple(r[key_count:]) for r in cur.fetchall()}
        expected={tuple(r[:key_count]):tuple(r[key_count:]) for r in
                  local.execute('SELECT '+columns+' FROM '+local_table)}
        def same(a,b):
            if a is None or b is None or isinstance(a,str) or isinstance(b,str):
                return a==b
            return abs(float(a)-float(b)) <= (0.001 if table=='party_buy' else 0.011)
        if (remote.keys()!=expected.keys() or any(not all(same(a,b) for a,b in
                zip(remote[key],expected[key])) for key in expected)):
            raise ValueError('Cloud facts are stale; retention refused: '+table)
    cutoff = now - dt.timedelta(days=DAYS)
    floor = cutoff.astimezone(IST).strftime('%Y%m%d')
    archived,path,digest = backup_archive(local,backup_dir)
    # Completion age, not submission age, is the seven-day window.
    removed_orders=removed_vouchers=0
    try:
        cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',('saarthi-history:'+company,))
        cur.execute("SELECT order_no FROM cloud_order WHERE status='invoiced' "
            'AND completed_at < %s AND retry_protected=FALSE AND retention_hold=FALSE '
            'AND client_request_id IS NOT NULL AND request_fingerprint IS NOT NULL '
            'AND pulled_at IS NOT NULL ORDER BY order_no LIMIT 50',(cutoff,))
        candidates = [r[0] for r in cur.fetchall()]
        for no in candidates:
            # Same order-number lock as the API allocator, before row locks.
            cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,1))',(no,))
            cur.execute('SELECT o.order_no,o.salesperson,o.client_request_id,o.request_fingerprint,'
                'o.customer_name,o.total,o.tally_guid,o.tally_vchno,h.source_alterid,h.amount '
                'FROM cloud_order o JOIN history_voucher h ON h.guid=o.tally_guid '
                "WHERE o.order_no=%s AND o.status='invoiced' AND o.completed_at < %s "
                'AND o.retry_protected=FALSE AND o.retention_hold=FALSE AND o.pulled_at IS NOT NULL '
                "AND h.source_state='active' AND h.retention_hold=FALSE FOR UPDATE OF o,h",(no,cutoff))
            order=cur.fetchone()
            if not order or not order[1] or not order[2] or not order[3]:
                continue
            cur.execute('SELECT line_no,item_guid,item_name,unit,qty,rate,amount '
                'FROM cloud_order_line WHERE order_no=%s ORDER BY line_no',(no,))
            lines=cur.fetchall()
            if not _order_matches(archived,order,lines) or not _confirmed(archived,order[6],order[8],order[9]):
                continue
            response=dict(order_no=no,total=float(order[5]),lines=len(lines),customer=order[4],
                          status='invoiced',note=None,archived=True)
            cur.execute('INSERT INTO order_receipt(salesperson,client_request_id,request_fingerprint,'
                'order_no,tally_guid,response) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                (order[1],order[2],order[3],no,order[6],Jsonb(response)))
            cur.execute('SELECT order_no,request_fingerprint FROM order_receipt '
                'WHERE salesperson=%s AND client_request_id=%s',(order[1],order[2]))
            if cur.fetchone() != (no,order[3]):
                raise ValueError('Conflicting retry receipt; transaction must roll back')
            cur.execute('DELETE FROM cloud_order WHERE order_no=%s',(no,))
            removed_orders+=1
        keep=protected_guids(cur)
        cur.execute('SELECT guid,source_alterid FROM history_voucher h WHERE vdate < %s '
                    'AND retention_hold=FALSE AND NOT EXISTS '
                    '(SELECT 1 FROM cloud_order o WHERE o.tally_guid=h.guid) '
                    'ORDER BY guid LIMIT 1000 FOR UPDATE',(floor,))
        old=cur.fetchall()
        eligible=[]
        for guid,version in old:
            source=_latest(archived,guid)
            if guid not in keep and source and version is not None and source['alterid']==version:
                eligible.append(guid)
        if eligible:
            cur.execute('DELETE FROM history_voucher WHERE guid=ANY(%s)',(eligible,))
            removed_vouchers=len(eligible)
    finally:
        archived.close()
    log(f'retention: {removed_orders} completed order(s), {removed_vouchers} old voucher(s) archived; '
        f'local backup {path.name}, sha256 {digest[:12]}')
    return dict(orders=removed_orders,vouchers=removed_vouchers,backup=str(path))
