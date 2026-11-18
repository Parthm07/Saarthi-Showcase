"""Publish current Tally sales versions without rewriting website orders.

The archive remains append-only. The cloud history is its current, sales-only
projection. Confirmed cancellations/deletions remain visible as source states.
"""
import datetime as dt
import gzip
import json

import tally_connector as t
import tally_shop as shop
import tally_store as store

KINDS = frozenset(('Sales', 'Receipt', 'Credit Note'))


def plan(local, known, customers, retention_floor=None, protected=()):
    """Build replacements from source versions, never from order totals.

    known maps GUID to (source AlterID, source state). No date watermark:
    a repaired missing record or a backdated edit must remain discoverable.
    """
    gone = {r[0] for r in local.execute('SELECT guid FROM deleted_voucher')}
    rows = local.execute(
        'SELECT v.* FROM voucher v JOIN '
        '(SELECT guid,MAX(alterid) a FROM voucher GROUP BY guid) x '
        'ON v.guid=x.guid AND v.alterid=x.a').fetchall()
    headers, lines, clear, withheld = [], [], [], []
    for row in rows:
        guid, version = row['guid'], row['alterid']
        if (retention_floor and row['vdate'] < retention_floor
                and guid not in protected and guid not in known):
            continue
        old_version, old_state = known.get(guid, (None, None))
        if old_version is not None and version < old_version:
            continue
        # Purchase/supplier details never enter the website projection.
        if row['vchtype'] not in KINDS or row['party'] not in customers:
            if guid in known and (version != old_version or old_state != 'unavailable'):
                withheld.append((version, guid)); clear.append(guid)
            continue
        deleted = guid in gone
        if (version == old_version and old_state != 'unavailable'
                and deleted == (old_state == 'deleted')):
            continue
        rec = json.loads(gzip.decompress(row['blob']))
        state = ('deleted' if deleted else 'cancelled' if rec.get('ISCANCELLED') == 'Yes'
                 else 'optional' if rec.get('ISOPTIONAL') == 'Yes' else 'active')
        observed = dt.datetime.fromisoformat(row['fetched_at']).astimezone().isoformat()
        headers.append((guid, row['party'], row['vdate'], row['vchtype'],
                        rec.get('VOUCHERNUMBER'), abs(row['amount'] or 0),
                        version, state, observed))
        clear.append(guid)
        if state == 'active':
            for n, line in enumerate(rec.get('ALLINVENTORYENTRIES.LIST', []), 1):
                qty, unit = t.qty(line.get('BILLEDQTY') or line.get('ACTUALQTY'))
                rate, _ = t.rate(line.get('RATE'))
                lines.append((guid, n, line.get('STOCKITEMNAME'), abs(qty), unit,
                              abs(rate), abs(t.amount(line.get('AMOUNT')) or 0)))
    # A validated tombstone may remain after a local source row was pruned.
    missing_deleted = [g for g in gone if g in known and known[g][1] != 'deleted'
                       and g not in clear]
    return dict(headers=headers, lines=lines, clear=clear, withheld=withheld,
                deleted=missing_deleted)


def publish(local, cur, log=print, retention_days=7):
    """Apply one atomic delta in the caller's Postgres transaction. No Tally calls."""
    company = store._get(local, 'company')
    if not company:
        raise ValueError('Cannot publish invoice changes from an unbound archive')
    cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                ('saarthi-history:' + company,))
    cur.execute('SELECT guid,source_alterid,source_state FROM history_voucher')
    known = {g:(v,s) for g,v,s in cur.fetchall()}
    customers = {c['name'] for c in shop.customers(local)}
    if known and not customers:
        raise ValueError('No local customers; refusing to remove a healthy sales projection')
    from cloud_retention import protected_guids,IST
    keep=protected_guids(cur)
    floor=(dt.datetime.now(IST).date()-dt.timedelta(days=retention_days)).strftime('%Y%m%d')
    delta = plan(local, known, customers,retention_floor=floor,protected=keep)
    if delta['headers']:
        cur.executemany(
            'INSERT INTO history_voucher(guid,party,vdate,vchtype,number,amount,'
            'source_alterid,source_state,observed_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) '
            'ON CONFLICT(guid) DO UPDATE SET party=EXCLUDED.party,vdate=EXCLUDED.vdate,'
            'vchtype=EXCLUDED.vchtype,number=EXCLUDED.number,amount=EXCLUDED.amount,'
            'source_alterid=EXCLUDED.source_alterid,source_state=EXCLUDED.source_state,'
            'observed_at=EXCLUDED.observed_at', delta['headers'])
    if delta['withheld']:
        cur.executemany("UPDATE history_voucher SET source_state='unavailable',"
                        'source_alterid=%s WHERE guid=%s', delta['withheld'])
    if delta['deleted']:
        cur.execute("UPDATE history_voucher SET source_state='deleted' WHERE guid=ANY(%s)",
                    (delta['deleted'],))
    clear = delta['clear'] + delta['deleted']
    if clear:
        cur.execute('DELETE FROM history_line WHERE voucher_guid=ANY(%s)', (clear,))
    if delta['lines']:
        cur.executemany('INSERT INTO history_line(voucher_guid,line_no,item,qty,unit,rate,amount) '
                        'VALUES(%s,%s,%s,%s,%s,%s,%s)', delta['lines'])
    changed = len(delta['headers']) + len(delta['withheld']) + len(delta['deleted'])
    log(f'Tally changes: {changed} voucher(s), {len(delta["lines"])} current line(s) published')
    return changed, len(delta['lines'])


if __name__ == '__main__':
    import argparse
    import pathlib
    import sqlite3
    import cloud_db
    parser = argparse.ArgumentParser(description='Publish observed Tally edits; never creates Tally invoices.')
    parser.add_argument('--publish', action='store_true', required=True)
    parser.add_argument('--upgrade-schema', action='store_true')
    args = parser.parse_args()
    path = pathlib.Path(__file__).resolve().parent / 'tally_raw.db'
    local = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    local.row_factory = sqlite3.Row
    try:
        with cloud_db.connect() as cx, cx.cursor() as cur:
            cur.execute('SELECT company FROM sync_status WHERE id=1')
            company = cur.fetchone()
            if not company or company[0] != store._get(local, 'company'):
                raise ValueError('Cloud and archive company identities do not match; nothing published')
            if args.upgrade_schema:
                cur.execute(cloud_db.TALLY_UPDATES_MIGRATION + cloud_db.FACT_RETENTION_MIGRATION)
            publish(local, cur)
    finally:
        local.close()
