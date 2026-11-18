"""Explicit integration check: only TEMP Postgres tables and temporary SQLite files.

python tests/check_fact_retention_postgres.py --temporary-tables
Never applies a migration or cleanup to permanent cloud tables.
"""
import argparse
import datetime as dt
import os
from pathlib import Path
import sys
import tempfile
from contextlib import closing
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('SESSION_SECRET','isolated-retention-check')
import psycopg
import api
import cloud_db
import cloud_retention as retention
import tally_cloud as cloud
import tally_insights as insights
import tally_shop as shop
import tally_store as store
import tally_updates as updates


def run():
    now=dt.datetime.now(dt.timezone.utc)
    olddate=(now.astimezone(retention.IST).date()-dt.timedelta(days=12)).strftime('%Y%m%d')
    with tempfile.TemporaryDirectory() as directory:
        local=shop.open_db(str(Path(directory)/'local.sqlite'))
        store._set(local,'company','isolated-retention-company')
        store._set(local,'voucher_alterid','100')
        cases=['ok','retry','hold','unresolved','recent','missing_lines','stale_source','cancelled']
        requests={}
        def save(guid,version,rate=80,cancelled='No'):
            store.save(local,[dict(GUID=guid,ALTERID=str(version),DATE=olddate,
                VOUCHERTYPENAME='Sales',VOUCHERNUMBER=guid,PARTYLEDGERNAME='Test Party',
                AMOUNT=str(-rate),ISCANCELLED=cancelled,ISOPTIONAL='No',
                **{'ALLINVENTORYENTRIES.LIST':[dict(STOCKITEMNAME='Harpic Blue',
                    BILLEDQTY='1 PCS',RATE=f'{rate}/PCS',AMOUNT=str(rate))]})])
        for version,no in enumerate(cases,10):
            save(no,version)
            local.execute('INSERT INTO shop_order(order_no,created_at,party_guid,party_name,status,'
                'subtotal,gst,total,note,tally_guid,tally_vchno) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (no,olddate,'customer','Test Party','invoiced',252,0,252,'[rep@example.com]',no,no))
            if no!='missing_lines':
                local.execute('INSERT INTO shop_order_line VALUES(?,?,?,?,?,?,?,?)',
                    (no,1,'item','Harpic Blue','PCS',3,84,252))
            requests[no]=api.OrderIn(request_id=uuid4(),customer_guid='customer',
                lines=[dict(item_guid='item',qty=3,rate=84)])
        local.commit()
        with psycopg.connect(cloud_db.env('CLOUD_DB_URL'),connect_timeout=5,sslmode='require',
            options='-c default_transaction_read_only=on -c statement_timeout=15000') as cx:
            with cx.cursor() as cur:
                cur.execute('SET TRANSACTION READ WRITE')
                cur.execute('SET LOCAL search_path=pg_temp')
                cur.execute(cloud_db.SCHEMA.replace('CREATE TABLE IF NOT EXISTS','CREATE TEMP TABLE IF NOT EXISTS'))
                cur.execute(cloud_db.MIGRATE.replace('CREATE TABLE IF NOT EXISTS','CREATE TEMP TABLE IF NOT EXISTS'))
                cx.commit()
                cur.execute('SET LOCAL search_path=pg_temp')
                cur.execute('SHOW transaction_read_only');assert cur.fetchone()==('on',)
                cur.execute("INSERT INTO sync_status(id,company) VALUES(1,'isolated-retention-company')")
                cur.execute("INSERT INTO customer(guid,name,route) VALUES('customer','Test Party','Route')")
                cur.execute("INSERT INTO item(guid,name,unit,rate) VALUES('item','Harpic Blue','PCS',84)")
                for version,no in enumerate(cases,10):
                    body=requests[no]
                    cur.execute('INSERT INTO cloud_order(order_no,salesperson,customer_guid,customer_name,'
                        'status,subtotal,gst,total,pulled_at,tally_guid,tally_vchno,completed_at,'
                        'client_request_id,request_fingerprint,retry_protected,retention_hold) '
                        'VALUES(%s,%s,%s,%s,%s,252,0,252,%s,%s,%s,%s,%s,%s,%s,%s)',
                        (no,'rep@example.com','customer','Test Party','placed' if no=='unresolved' else 'invoiced',
                         now-dt.timedelta(days=12),no,no,
                         now-dt.timedelta(days=1 if no=='recent' else 9),str(body.request_id),
                         api._order_fingerprint(body),no=='retry',no=='hold'))
                    cur.execute('INSERT INTO cloud_order_line VALUES(%s,1,%s,%s,%s,3,84,252)',
                                (no,'item','Harpic Blue','PCS'))
                    cur.execute('INSERT INTO history_voucher(guid,party,vdate,vchtype,number,amount,'
                        'source_alterid,source_state) VALUES(%s,%s,%s,%s,%s,80,%s,%s)',
                        (no,'Test Party',olddate,'Sales',no,version-1 if no=='stale_source' else version,
                         'cancelled' if no=='cancelled' else 'active'))
                    cur.execute('INSERT INTO history_line VALUES(%s,1,%s,1,%s,80,80)',(no,'Harpic Blue','PCS'))
                def rebuild_publish():
                    with patch.object(shop,'customers',return_value=[{'name':'Test Party'}]):
                        shop.rebuild_party_rates(local,log=lambda *a:None)
                        insights.rebuild(local,log=lambda *a:None)
                    cloud.push_party_rates(local,cur,log=lambda *a:None)
                    cloud.push_recommendations(local,cur,log=lambda *a:None)
                    cloud.push_daily_sales(local,cur,log=lambda *a:None)
                rebuild_publish()
                user=dict(role='salesperson',email='rep@example.com',routes=['Route'])
                api._cache['at']=None
                payload=api.data(user,cur)
                fact=payload['customers'][0]['buys'][0]
                assert fact['rate']==80 and fact['purchaseCount']==8
                assert payload['lines']==[] and payload['daily']=={}
                wrong=dict(role='salesperson',email='other@example.com',routes=['Route'])
                assert not api.acknowledge_order(api.OrderAcknowledgement(order_no='retry',
                    request_id=requests['retry'].request_id),wrong,cur)['acknowledged']
                cur.execute("SELECT retry_protected FROM cloud_order WHERE order_no='retry'")
                assert cur.fetchone()==(True,)
                with patch.object(retention,'backup_archive',side_effect=OSError('injected backup failure')):
                    try:
                        retention.cleanup(local,cur,Path(directory)/'backup',now=now,log=lambda *a:None)
                        raise AssertionError('Backup failure allowed cleanup')
                    except OSError:
                        pass
                cur.execute('SELECT COUNT(*) FROM cloud_order');assert cur.fetchone()==(8,)
                cur.execute('SAVEPOINT retention_case')
                result=retention.cleanup(local,cur,Path(directory)/'backup',now=now,log=lambda *a:None)
                assert result['orders']==1,result
                cur.execute('SELECT order_no FROM cloud_order ORDER BY order_no')
                assert {r[0] for r in cur.fetchall()}==set(cases)-{'ok'}
                cur.execute('SELECT count(*) FROM cloud_order_line WHERE order_no=%s',('ok',))
                assert cur.fetchone()==(0,)
                cur.execute('SELECT count(*) FROM order_receipt');assert cur.fetchone()==(1,)
                reply=api.place_order(requests['ok'],user,cur)
                assert reply['order_no']=='ok' and reply['lines']==1 and reply['total']==252 and reply['archived']
                assert api.acknowledge_order(api.OrderAcknowledgement(order_no='ok',
                    request_id=requests['ok'].request_id),user,cur)['acknowledged']
                try:
                    api.place_order(requests['ok'].model_copy(update={'note':'changed'}),user,cur)
                    raise AssertionError('Changed archived request was accepted')
                except api.HTTPException as exc:
                    assert exc.status_code==409
                with closing(__import__('sqlite3').connect(result['backup'])) as backup:
                    assert backup.execute("SELECT total FROM shop_order WHERE order_no='ok'").fetchone()==(252,)
                # Simulate an enclosing transaction failure: details and receipts
                # must return to their pre-cleanup state together.
                cur.execute('ROLLBACK TO SAVEPOINT retention_case')
                cur.execute("SELECT count(*) FROM cloud_order WHERE order_no='ok'");assert cur.fetchone()==(1,)
                cur.execute('SELECT count(*) FROM order_receipt');assert cur.fetchone()==(0,)
                result=retention.cleanup(local,cur,Path(directory)/'backup',now=now,log=lambda *a:None)
                assert result['orders']==1
                # An archived old invoice edit updates facts, not old detail rows.
                save('ok',300,75);store._set(local,'voucher_alterid','300');local.commit()
                rebuild_publish()
                with patch.object(shop,'customers',return_value=[{'name':'Test Party'}]):
                    updates.publish(local,cur,log=lambda *a:None)
                cur.execute("SELECT last_rate,times FROM party_rate WHERE party='Test Party'")
                assert cur.fetchone()==(75,8)
                cur.execute("SELECT count(*) FROM history_voucher WHERE guid='ok'");assert cur.fetchone()==(0,)
                save('ok',301,75,'Yes');store._set(local,'voucher_alterid','301');local.commit()
                rebuild_publish()
                cur.execute("SELECT last_rate,times FROM party_rate WHERE party='Test Party'")
                assert cur.fetchone()==(80,7)
                cur.execute('SELECT SUM(invoices),SUM(amount) FROM daily_sales')
                assert cur.fetchone()==(7,560)
                # A mismatched or stale local fact snapshot prevents cleanup.
                store._set(local,'voucher_alterid','999');local.commit()
                try:
                    retention.cleanup(local,cur,Path(directory)/'backup',now=now,log=lambda *a:None)
                    raise AssertionError('Stale facts allowed cleanup')
                except ValueError as exc:
                    assert 'Rebuild' in str(exc)
                # Cancelling the last valid purchases must clear stale price
                # facts instead of being mistaken for an empty installation.
                for version,no in enumerate(cases,500):
                    save(no,version,80,'Yes')
                store._set(local,'voucher_alterid','600');local.commit()
                rebuild_publish()
                cur.execute('SELECT COUNT(*) FROM party_rate');assert cur.fetchone()==(0,)
                cur.execute('SELECT COUNT(*) FROM party_buy');assert cur.fetchone()==(0,)
            cx.rollback()
        local.close()
    print('PASS: protected/unresolved/recent/mismatched orders retained; backed-up completion archived; '
          'retry receipt, rollback, old corrections, cancellation and stale-fact guard verified; no permanent writes.')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--temporary-tables',action='store_true',required=True);p.parse_args()
    run()
