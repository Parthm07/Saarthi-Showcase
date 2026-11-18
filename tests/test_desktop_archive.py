"""Offline desktop checks against disposable SQLite fixtures only."""
import datetime as dt
import http.client
import json
import pathlib
import sqlite3
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from unittest.mock import patch

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import tally_shop as shop
import tally_store as store
from desktop_archive import Archive,IST
import desktop_archive
from desktop_app import make_server


def fixture(path):
    local=shop.open_db(str(path))
    store._set(local,'company','Saarthi Demo Books')
    today=dt.datetime.now(IST).date()
    store.save_masters(local,'Group',[dict(GUID='g',ALTERID='1',NAME='Dewas Debtors',PARENT='Sundry Debtors')])
    store.save_masters(local,'Ledger',[dict(GUID='c',ALTERID='2',NAME='Demo Traders',PARENT='Dewas Debtors'),
        dict(GUID='supplier',ALTERID='3',NAME='Demo Supplier',PARENT='Sundry Creditors')])
    store.save_masters(local,'StockItem',[dict(GUID='p',ALTERID='4',NAME='HARPIC BLUE 500 ML MRP110',PARENT='Home care',BASEUNITS='PCS',STANDARDPRICE='84/PCS')])
    for guid,version,qty,rate,cancelled,optional,kind,days in [
        ('bill',10,3,84,'No','No','Sales',0),('bill',11,8,84,'No','No','Sales',0),
        ('cancelled',12,2,84,'Yes','No','Sales',0),('optional',13,2,84,'No','Yes','Sales',0),
        ('gone',14,2,84,'No','No','Sales',0),('receipt',15,1,100,'No','No','Receipt',0),
        ('older',16,5,80,'No','No','Sales',40)]:
        store.save(local,[dict(GUID=guid,ALTERID=str(version),DATE=(today-dt.timedelta(days=days)).strftime('%Y%m%d'),
            VOUCHERTYPENAME=kind,PARTYLEDGERNAME='Demo Traders',AMOUNT=str(-qty*rate),VOUCHERNUMBER=str(4400+version),
            ISCANCELLED=cancelled,ISOPTIONAL=optional,**{'ALLINVENTORYENTRIES.LIST':[
                dict(STOCKITEMNAME='HARPIC BLUE 500 ML MRP110',BILLEDQTY=f'{qty} PCS',RATE=f'{rate}/PCS',AMOUNT=str(qty*rate))]})])
    local.execute("INSERT INTO deleted_voucher VALUES('gone',14,'2026-10-31')")
    now=dt.datetime.now(IST).isoformat()
    local.execute('INSERT INTO stock_snapshot VALUES(?,?,?,?,?,?)',(now,'p','HARPIC BLUE 500 ML MRP110',-1,'PCS',84))
    local.execute('INSERT INTO party_balance VALUES(?,?,?,?)',(now,'c','Demo Traders',-5984))
    local.execute('INSERT INTO shop_order(order_no,created_at,party_guid,party_name,route,status,subtotal,gst,total,note,tally_guid,tally_vchno) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
        ('WEB-DEMO',now,'c','Demo Traders','Dewas Debtors','invoiced',252,0,252,'[rep@example.test]','bill','4411'))
    local.execute('INSERT INTO shop_order_line VALUES(?,?,?,?,?,?,?,?)',('WEB-DEMO',1,'p','HARPIC BLUE 500 ML MRP110','PCS',3,84,252))
    local.commit();shop.rebuild_party_rates(local,log=lambda *a:None);local.close()


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=pathlib.Path(self.tmp.name)/'fixture.db'
        fixture(self.path);self.archive=Archive(self.path)
    def tearDown(self):self.tmp.cleanup()
    def test_last_sold_price_survives_an_explicit_selling_rate(self):
        with closing(shop.open_db(str(self.path))) as local:
            store.save_masters(local, 'StockItem', [
                dict(GUID='p', ALTERID='100', NAME='HARPIC BLUE 500 ML MRP110',
                     PARENT='Home care', BASEUNITS='PCS', STANDARDPRICE='90/PCS'),
            ])
        with patch('desktop_workflows.confirmed_prices', return_value={'p': 90}):
            product = self.archive.listing('products', sort='last_sold_rate')['rows'][0]
        self.assertEqual(product['rate'], 90)
        self.assertIsNone(product['rate_date'])
        self.assertEqual(product['last_sold_rate'], 84)
        self.assertEqual(product['last_sold_date'], dt.datetime.now(IST).strftime('%Y%m%d'))

    def test_current_metrics_exclude_old_versions_optional_cancelled_and_deleted(self):
        data=self.archive.overview()
        self.assertEqual(data['sales'],672);self.assertEqual(data['sales_count'],1)
        self.assertEqual(data['receipts'],100);self.assertEqual(data['versions'],7)
        self.assertEqual(len(data['daily']),dt.datetime.now(IST).day)
        self.assertEqual(data['daily'][-1]['total'],672)
        self.assertEqual(data['daily'][0]['total'],0)
    def test_switching_after_cache_age_does_not_decode_unchanged_vouchers_again(self):
        self.archive.overview()
        with patch('time.monotonic',return_value=10**12),patch.object(desktop_archive,'unpack',wraps=desktop_archive.unpack) as decode:
            self.archive.listing('orders');self.archive.listing('vouchers');self.archive.overview()
            self.assertEqual(decode.call_count,0)
    def test_new_version_decodes_only_changed_voucher_and_refreshes_immediately(self):
        self.archive.overview()
        local=shop.open_db(str(self.path))
        try:
            store.save(local,[dict(GUID='bill',ALTERID='100',DATE=dt.datetime.now(IST).strftime('%Y%m%d'),
                VOUCHERTYPENAME='Sales',PARTYLEDGERNAME='Demo Traders',AMOUNT='-800',
                VOUCHERNUMBER='4411',ISCANCELLED='No',ISOPTIONAL='No')])
        finally:local.close()
        with patch.object(desktop_archive,'unpack',wraps=desktop_archive.unpack) as decode:
            self.assertEqual(self.archive.overview()['sales'],800)
            self.assertEqual(decode.call_count,1)
    def test_new_tombstone_updates_current_totals_without_redecoding_source(self):
        self.archive.overview()
        with closing(sqlite3.connect(self.path)) as local:
            local.execute("INSERT INTO deleted_voucher VALUES('bill',11,'2026-11-01')");local.commit()
        with patch.object(desktop_archive,'unpack',wraps=desktop_archive.unpack) as decode:
            self.assertEqual(self.archive.overview()['sales'],0)
            self.assertEqual(decode.call_count,0)
        self.assertNotIn('WEB-DEMO', {r['order_no'] for r in self.archive.listing('orders')['rows']})
        archived = self.archive.listing('orders',category='deleted')['rows']
        self.assertIn('WEB-DEMO', {r['order_no'] for r in archived})

    def test_requested_check_detects_a_tally_deletion_during_the_day(self):
        import tally_agent as agent

        request = self.path.parent / '.saarthi-check-deletions'
        request.touch()
        with closing(shop.open_db(str(self.path))) as local:
            live = [dict(GUID=r['guid'], DATE=r['vdate']) for r in local.execute(
                "SELECT DISTINCT guid,vdate FROM voucher WHERE guid NOT IN ('bill','gone')")]
            with patch.object(agent, 'deletion_check_path', return_value=request), \
                 patch.object(agent, 'managed_job_allowed', return_value=True), \
                 patch.object(agent, 'due', return_value=False), \
                 patch.object(store.t, 'company', return_value='Saarthi Demo Books'), \
                 patch.object(store.t, 'vouchers', return_value=live):
                agent.tick(local, dt.datetime(2026, 11, 12, 14))
            self.assertIn('1 voucher(s)', store._get(local, 'deletion_check_result'))
        self.assertFalse(request.exists())
        self.assertEqual(self.archive.overview()['sales'], 0)
        self.assertTrue(any(r['order_no']=='WEB-DEMO' for r in self.archive.listing('orders', category='deleted')['rows']))
        self.assertNotIn('bill', {r['guid'] for r in self.archive.listing('vouchers')['rows']})
        self.assertNotIn('bill', {r['guid'] for r in self.archive.listing('vouchers', category='Sales')['rows']})
        self.assertIn('bill', {r['guid'] for r in self.archive.listing('vouchers', category='deleted')['rows']})
    def test_orders_show_current_invoice_total_while_preserving_original(self):
        row=self.archive.listing('orders')['rows'][0]
        self.assertEqual(row['display_total'],672);self.assertEqual(row['salesperson'],'rep@example.test')
        detail=self.archive.detail('orders','WEB-DEMO')
        self.assertEqual(detail['total'],252);self.assertEqual(abs(detail['invoice']['amount']),672)
        self.assertEqual(len(detail['invoice']['versions']),2)
        self.assertEqual(self.archive.detail('vouchers','bill',10)['record']['ALLINVENTORYENTRIES.LIST'][0]['BILLEDQTY'],'3 PCS')
    def test_orders_include_all_sales_once_and_open_native_tally_details(self):
        data=self.archive.listing('orders',size=100)
        rows=data['rows']
        self.assertEqual(data['total'],4)  # Deleted sales are visible only through the Deleted filter.
        self.assertEqual(sum(r['tally_guid']=='bill' for r in rows),1)
        older=next(r for r in rows if r['tally_guid']=='older')
        self.assertEqual(older['source'],'Tally')
        self.assertEqual(older['detail_kind'],'vouchers')
        self.assertEqual(older['display_total'],400)
        self.assertEqual(self.archive.detail(older['detail_kind'],older['detail_id'])['party'],'Demo Traders')
        for status in ('cancelled','optional','deleted'):
            filtered=self.archive.listing('orders',category=status)['rows']
            self.assertEqual(len(filtered),1)
            self.assertIsNone(filtered[0]['display_total'])
        self.assertEqual(self.archive.listing('orders',q='4406')['total'],0)
        self.assertEqual(self.archive.listing('orders',q='4416')['total'],1)
        page1=self.archive.listing('orders',size=2,page=1)
        page2=self.archive.listing('orders',size=2,page=2)
        self.assertFalse({r['detail_id'] for r in page1['rows']} & {r['detail_id'] for r in page2['rows']})
        today=dt.datetime.now(IST).strftime('%Y%m%d')
        self.assertEqual(self.archive.listing('orders',start=today,end=today)['total'],3)
    def test_tally_only_archive_orders_do_not_require_website_tables(self):
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute('DROP TABLE shop_order_line');conn.execute('DROP TABLE shop_order');conn.commit()
        rows=self.archive.listing('orders')['rows']
        self.assertEqual(len(rows),4)
        self.assertTrue(all(r['detail_kind']=='vouchers' for r in rows))
    def test_products_and_all_accounting_parties_come_from_masters(self):
        product=self.archive.listing('products')['rows'][0]
        self.assertEqual(product['name'],'HARPIC BLUE 500 ML MRP110');self.assertEqual(product['stock'],-1)
        self.assertEqual(self.archive.listing('parties')['total'],2)
        self.assertEqual(self.archive.listing('parties',q='supplier')['rows'][0]['group'],'Sundry Creditors')
    def test_partial_snapshot_round_preserves_older_observation_for_missing_product(self):
        local=shop.open_db(str(self.path))
        try:
            store.save_masters(local,'StockItem',[dict(GUID='older-product',ALTERID='500',NAME='Older product',BASEUNITS='PCS')])
            local.execute('INSERT INTO stock_snapshot VALUES(?,?,?,?,?,?)',('2000-01-01T00:00:00','older-product','Older product',4,'PCS',10));local.commit()
        finally:local.close()
        rows={r['id']:r for r in self.archive.listing('products')['rows']}
        self.assertEqual(rows['older-product']['stock'],4)
        self.assertEqual(rows['older-product']['stock_asof'],'2000-01-01T00:00:00')
        self.assertEqual(rows['p']['stock'],-1)
    def test_full_explorer_decompresses_source_and_rejects_unknown_tables(self):
        rows=self.archive.explorer('voucher',q='bill')['rows'];self.assertEqual(len(rows),2)
        raw=self.archive.explorer('voucher',rowid=rows[0]['__rowid__'])
        self.assertEqual(raw['blob']['GUID'],'bill')
        with self.assertRaises(ValueError):self.archive.explorer('voucher; DROP TABLE voucher')
        with self.archive.read() as conn:
            with self.assertRaises(sqlite3.OperationalError):conn.execute('DELETE FROM voucher')
    def test_backup_is_complete_verified_and_does_not_change_source(self):
        before=self.path.read_bytes()
        result=self.archive.backup(pathlib.Path(self.tmp.name)/'backups')
        with closing(sqlite3.connect(result['path'])) as conn:self.assertEqual(conn.execute('SELECT COUNT(*) FROM voucher').fetchone()[0],7)
        self.assertEqual(self.path.read_bytes(),before)
    def test_csv_contains_all_rows_and_decompressed_source(self):
        data=''.join(self.archive.export('voucher'))
        self.assertIn('ALLINVENTORYENTRIES.LIST',data);self.assertIn('HARPIC BLUE',data)
    def test_installer_preserves_archive_and_records_existing_database_location(self):
        from desktop_install import install
        base=pathlib.Path(self.tmp.name)
        source=base/'viewer.exe';source.write_bytes(b'disposable executable fixture')
        before=self.path.read_bytes()
        with patch.dict('os.environ',{'LOCALAPPDATA':str(base/'local'),'APPDATA':str(base/'roaming')}),patch('desktop_install.subprocess.run') as shortcut:
            installed=install(self.path,source=source)
        self.assertEqual(installed.read_bytes(),source.read_bytes())
        self.assertEqual(json.loads((base/'local/SaarthiAdmin/settings.json').read_text())['database'],str(self.path.resolve()))
        self.assertEqual(before,self.path.read_bytes());shortcut.assert_called_once()
    def test_http_requires_session_and_rejects_cross_origin_commands(self):
        server=make_server(self.archive,backup_dir=pathlib.Path(self.tmp.name)/'backup',token='fixture-secret')
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
        try:
            connection.request('GET','/api/overview');response=connection.getresponse();self.assertEqual(response.status,403);response.read()
            headers={'Cookie':'saarthi_desktop=fixture-secret'}
            connection.request('GET','/api/overview',headers=headers);response=connection.getresponse();self.assertEqual(response.status,200);self.assertEqual(json.load(response)['sales'],672)
            connection.request('POST','/api/backup',headers={**headers,'Origin':'https://external.example'});response=connection.getresponse();self.assertEqual(response.status,403);response.read()
            connection.request('GET','/../.env',headers=headers);response=connection.getresponse();self.assertEqual(response.status,404);response.read()
        finally:
            connection.close();server.shutdown();server.server_close();thread.join(3)

    def test_http_employee_commands_use_scoped_bridge_and_require_local_origin(self):
        from unittest.mock import Mock
        website=Mock();website.call.return_value={'saved':True}
        agent=Mock();agent.start_async.return_value={'queued':True}
        server=make_server(self.archive,token='fixture',website=website,agent=agent)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
        headers={'Cookie':'saarthi_desktop=fixture','Origin':f'http://127.0.0.1:{server.server_port}','Content-Type':'application/json'}
        try:
            person=dict(email='rep@example.test',role='salesperson',name='Fixture Rep')
            connection.request('POST','/api/website/users',json.dumps(person),{**headers,'Origin':'https://external.example'})
            response=connection.getresponse();self.assertEqual(response.status,403);response.read();website.call.assert_not_called()
            connection.request('POST','/api/website/users',json.dumps(person),headers)
            response=connection.getresponse();self.assertEqual(response.status,200);response.read();website.call.assert_called_once_with('users',person)
            connection.request('POST','/api/agent/start','{}',headers)
            response=connection.getresponse();self.assertEqual(response.status,200);response.read();agent.start_async.assert_called_once()
            agent.status.return_value={'managed':True,'state':'running'}
            connection.request('POST','/api/agent/check-deletions',headers={**headers,'Origin':'https://external.example'})
            response=connection.getresponse();self.assertEqual(response.status,403);response.read()
            self.assertFalse((self.path.parent/'.saarthi-check-deletions').exists())
            connection.request('POST','/api/agent/check-deletions','{}',headers)
            response=connection.getresponse();self.assertEqual(response.status,202);response.read()
            connection.request('GET','/api/agent',headers=headers)
            response=connection.getresponse();self.assertEqual(response.status,200)
            self.assertTrue(json.load(response)['deletion_check_pending'])
        finally:
            connection.close();server.shutdown();server.server_close();thread.join(3)


if __name__=='__main__':unittest.main()
