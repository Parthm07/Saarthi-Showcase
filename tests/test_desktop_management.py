"""Owner reports, uploads and visit handoff use disposable data only."""
import datetime as dt
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock
from uuid import uuid4
from test_desktop_archive import fixture
from desktop_archive import Archive,IST
from desktop_analysis import Analysis,shift_months
from desktop_management import Management,inventory,save_policy,rate_preview
from desktop_workflows import Workflows
import tally_shop as shop
import tally_store as store
from sales_visits import pull


class ManagementTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'tally_raw.db';fixture(self.path)
        self.archive=Archive(self.path);self.management=Management(self.archive,Analysis(self.archive));self.work=Workflows(self.archive)
        self.today=dt.datetime.now(IST).date()
    def sale(self,guid,date,party='Demo Traders',item='Old product',total=100):
        conn=shop.open_db(str(self.path))
        store.save(conn,[dict(GUID=guid,ALTERID='500',DATE=date.strftime('%Y%m%d'),VOUCHERTYPENAME='Sales',PARTYLEDGERNAME=party,AMOUNT=str(-total),**{'ALLINVENTORYENTRIES.LIST':[dict(STOCKITEMNAME=item,AMOUNT=str(total),BILLEDQTY='1 PCS')]})]);conn.close()
    def test_overview_defaults_to_mtd_and_shows_debtors_routes_and_products(self):
        before=self.path.read_bytes();d=self.management.overview()
        self.assertEqual(d['start'],self.today.replace(day=1).strftime('%Y%m%d'))
        self.assertEqual(d['end'],self.today.strftime('%Y%m%d'));self.assertEqual(d['debtors'],5984)
        self.assertEqual(d['routes'][0]['name'],'Dewas Debtors');self.assertTrue(d['top_products'])
        self.assertEqual(before,self.path.read_bytes())
    def test_focus_finds_dormant_parties_and_missing_products_but_respects_pending_orders(self):
        prior=shift_months(self.today.replace(day=1),-1)
        self.sale('dormant',prior,'Dormant Party');self.sale('previous-product',prior,item='Previous soap')
        d=self.management.report();focus={r['name']:r for r in d['customer_focus']}
        self.assertIn('Dormant Party',focus);self.assertIn('Previous soap',focus['Demo Traders']['missing_items'])
        conn=shop.open_db(str(self.path));conn.execute("INSERT INTO shop_order(order_no,created_at,party_guid,party_name,route,status,subtotal,gst,total,note) VALUES(?,?,?,?,?,?,?,?,?,?)",('NEW',dt.datetime.now(IST).isoformat(),'d','Dormant Party','Route','placed',10,0,10,'[rep@example.test]'));conn.commit();conn.close()
        focus={r['name']:r for r in self.management.report()['customer_focus']}
        self.assertNotIn('no order this month',focus['Dormant Party']['reason'])
    def test_month_on_month_compares_matching_duration_and_does_not_invent_margins(self):
        self.sale('coverage',self.today-dt.timedelta(days=200))
        self.sale('previous',shift_months(self.today,-1),total=200)
        d=self.management.report()
        expected_end=shift_months(self.today,-1)
        if self.today.day==__import__('calendar').monthrange(self.today.year,self.today.month)[1]:expected_end=expected_end.replace(day=__import__('calendar').monthrange(expected_end.year,expected_end.month)[1])
        self.assertEqual(d['mom_end'],expected_end.strftime('%Y%m%d'))
        self.assertIsNotNone(d['mom_growth']);self.assertTrue(all(r['profit'] is None and r['margin'] is None for r in d['customers']))
    def test_visits_are_not_guessed_from_sales_and_individual_scope_filters_them(self):
        self.assertIsNone(self.management.report()['team'][0]['visits'])
        conn=shop.open_db(str(self.path));store._set(conn,'visit_sync_at',dt.datetime.now(IST).isoformat())
        conn.execute('INSERT INTO sales_visit VALUES(?,?,?,?,?,?)',('rep@example.test',self.today.strftime('%Y%m%d'),'c','Demo Traders','Dewas Debtors','now'))
        conn.execute('INSERT INTO sales_visit VALUES(?,?,?,?,?,?)',('other@example.test',self.today.strftime('%Y%m%d'),'c','Demo Traders','Dewas Debtors','now'));conn.commit();conn.close()
        d=self.management.report(rep='rep@example.test')
        self.assertEqual(sum(r['visits'] for r in d['team']),1)
        self.assertEqual(sum(r['visits'] for r in d['team_activity']),1)
    def test_reorder_policy_changes_only_auxiliary_database_and_calculates_quantity(self):
        before=self.path.read_bytes();save_policy(self.work,{'guid':'p','minimum':5,'target':20})
        p=inventory(self.work)['products'][0]
        self.assertTrue(p['restock']);self.assertEqual(p['reorder_qty'],21)
        self.assertEqual(before,self.path.read_bytes())
        for body in ({'guid':'missing'},{'guid':'p','minimum':'NaN'},{'guid':'p','minimum':8,'target':2}):
            with self.assertRaises(ValueError):save_policy(self.work,body)
    def test_csv_is_fully_validated_before_any_rate_is_queued(self):
        d=rate_preview(self.work,'item_guid,product,rate\np,,80\n')
        self.assertEqual(d['rows'][0]['price'],'80');self.assertEqual(self.work.history(),[])
        for csv in ('item_guid,price\np,80\n','item_guid,rate\np,80\np,90\n','product,rate\nNot a product,80\n','item_guid,rate\np,NaN\n','item_guid,rate\np,free\n','item_guid,rate\np,1,00,000\n'):
            with self.assertRaises(ValueError):rate_preview(self.work,csv)
        self.assertEqual(self.work.history(),[])
    def test_csv_skips_blank_cells_and_queues_numeric_rates_without_a_business_range(self):
        from tally_admin import build_xml,TallyWriter
        self.assertEqual(rate_preview(self.work,'item_guid,rate\np,\n'),{'rows':[],'skipped':1})
        for price in ('0','-3.25','12000001.123'):
            with self.subTest(price=price):
                row,=rate_preview(self.work,'item_guid,rate\nplaceholder,\np,'+price+'\n')['rows']
                saved=self.work.submit(str(uuid4()),'product_update',row)
                details=self.work.detail(saved['id'])['details']
                self.assertEqual(details['price'],price)
                command={**saved,'payload':details}
                self.assertIn('<RATE>'+price+'</RATE>',build_xml(command))
                writer=TallyWriter(None)
                writer.master=Mock(return_value={'GUID':'p','NAME':details['name'],
                                                'STANDARDPRICE':price+'/PCS' if price!='0' else ''})
                self.assertEqual(writer.lookup(command)['guid'],'p')
                writer.master.return_value['STANDARDPRICE']='0.001/PCS'
                self.assertIsNone(writer.lookup(command))
    def test_visit_handoff_survives_cloud_ack_failure_without_duplicating_local_visit(self):
        rows=[('rep@example.test',self.today.strftime('%Y%m%d'),'c','Demo Traders','Dewas Debtors',dt.datetime.now(IST))]
        cur=Mock();cur.fetchone.return_value=('sales_visit',);cur.fetchall.return_value=rows
        def fail_ack(query,*args):
            if query.startswith('UPDATE sales_visit'):raise RuntimeError('connection lost')
        cur.execute.side_effect=fail_ack;local=shop.open_db(str(self.path));self.addCleanup(local.close)
        with self.assertRaises(RuntimeError):pull(local,cur)
        self.assertEqual(local.execute('SELECT COUNT(*) FROM sales_visit').fetchone()[0],1)
        cur.execute.side_effect=None;self.assertEqual(pull(local,cur),1)
        self.assertEqual(local.execute('SELECT COUNT(*) FROM sales_visit').fetchone()[0],1)
        self.assertIn('pulled_at IS NOT NULL',cur.execute.call_args.args[0])
    def test_rate_import_preserves_a_tally_rename_after_preview(self):
        row=rate_preview(self.work,'item_guid,rate\np,80\n')['rows'][0]
        context=self.work.context();context['products'][0]['name']='New Tally product name'
        validated=self.work.validate('product_update',row,context=context)
        self.assertEqual(validated['name'],'New Tally product name')
    def test_visit_api_uses_authenticated_identity_and_ignores_duplicate_party_day(self):
        os.environ.setdefault('SESSION_SECRET','isolated-test-secret')
        import api
        from fastapi import HTTPException
        cur=Mock();cur.fetchone.side_effect=[('Books',),('Demo Traders','Dewas Debtors'),('sales_visit',)]
        body=api.VisitBatchIn(email='rep@example.test',company='Books',rows=[{'cid':'c','at':dt.datetime.now(IST).isoformat()}])
        self.assertTrue(api.record_visits(body,{'email':'rep@example.test'},cur)['saved'])
        self.assertIn('ON CONFLICT',cur.execute.call_args.args[0])
        with self.assertRaises(HTTPException):api.record_visits(body,{'email':'other@example.test'},cur)


if __name__=='__main__':unittest.main()
