"""Analysis checks use disposable archives; no Tally or cloud calls."""
import datetime as dt
import json
import pathlib
import sqlite3
import tempfile
import unittest
from contextlib import closing
from unittest.mock import patch
from test_desktop_archive import fixture
from desktop_archive import Archive,IST
from desktop_analysis import Analysis,period
import desktop_analysis
import tally_store as store
import tally_shop as shop

class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=pathlib.Path(self.tmp.name)/'tally_raw.db'
        fixture(self.path);self.archive=Archive(self.path);self.analysis=Analysis(self.archive)
        self.today=dt.datetime.now(IST).date();self.start=(self.today-dt.timedelta(days=29)).strftime('%Y%m%d');self.end=self.today.strftime('%Y%m%d')
        conn=shop.open_db(str(self.path))
        for guid,alter,days,kind,total,qty in [('native',100,0,'Sales',300,'2 BOX'),('credit',101,0,'Credit Note',100,'1 PCS'),('previous',102,30,'Sales',100,'1 PCS'),('oldest',103,80,'Sales',50,'1 PCS')]:
            store.save(conn,[dict(GUID=guid,ALTERID=str(alter),DATE=(self.today-dt.timedelta(days=days)).strftime('%Y%m%d'),VOUCHERTYPENAME=kind,PARTYLEDGERNAME='Demo Traders',AMOUNT=str(-total),VOUCHERNUMBER=guid,**{'ALLINVENTORYENTRIES.LIST':[dict(STOCKITEMNAME='HARPIC BLUE 500 ML MRP110',BILLEDQTY=qty,RATE='100/PCS',AMOUNT=str(total))]})])
        now=dt.datetime.now(IST)
        for no,days,status in [('waiting',3,'placed'),('rejected',0,'rejected'),('draft',0,'draft'),('ancient-waiting',80,'placed')]:
            conn.execute('INSERT INTO shop_order(order_no,created_at,party_guid,party_name,route,status,subtotal,gst,total,note) VALUES(?,?,?,?,?,?,?,?,?,?)',(no,(now-dt.timedelta(days=days)).isoformat(),'c','Demo Traders','Dewas Debtors',status,250,0,250,'[rep@example.test]'))
        conn.commit();conn.close()
    def tearDown(self):self.tmp.cleanup()
    def report(self,**args):return self.analysis.report(self.start,self.end,**args)
    def test_current_sales_credits_growth_and_separate_units(self):
        before=self.path.read_bytes();d=self.report();m=d['metrics']
        self.assertEqual((m['gross'],m['credits'],m['net'],m['invoices']),(972,100,872,2))
        self.assertEqual(m['average'],486);self.assertEqual(d['previous']['net'],500);self.assertEqual(d['growth'],74.4)
        self.assertEqual(len(d['daily']),30);self.assertEqual(sum(x['net'] for x in d['daily']),872)
        self.assertEqual(d['products'][0]['units'],{'PCS':7,'BOX':2})
        self.assertEqual(d['products'][0]['net'],872);self.assertEqual(d['products'][0]['stock'],-1)
        self.assertEqual(d['customers'][0]['balance'],5984)
        self.assertEqual(self.path.read_bytes(),before)
    def test_exact_salesman_filter_and_pipeline_are_not_opportunity_win_rates(self):
        d=self.report(rep='rep@example.test')
        self.assertEqual(d['metrics']['gross'],672);self.assertEqual(d['metrics']['credits'],0)
        self.assertEqual(d['pipeline_counts'],{'invoiced':1,'waiting':1,'rejected':1})
        self.assertEqual(d['billing_completion'],33.3);self.assertEqual(len(d['waiting']),2)
        self.assertEqual(d['waiting'][0]['order'],'ancient-waiting')
        row=d['team'][0];self.assertEqual(row['submitted'],3);self.assertEqual(row['waiting_orders'],2)
        unattributed=self.report(rep='Unattributed');self.assertEqual(unattributed['metrics']['gross'],300)
        self.assertEqual(unattributed['unattributed_credits'],100)
        self.assertEqual(self.report(route='Unknown')['metrics']['gross'],0)
    def test_target_is_scoped_persistent_and_uses_only_auxiliary_database(self):
        before=self.path.read_bytes();body=dict(start=self.start,end=self.end,route='',rep='',amount='1000')
        self.analysis.save_goal(body);self.assertEqual(Analysis(self.archive).report(self.start,self.end)['target_progress'],87.2)
        self.assertIsNone(self.report(rep='rep@example.test')['target'])
        self.analysis.save_goal({**body,'rep':'rep@example.test','amount':'800'})
        row=next(r for r in self.report()['team'] if r['name']=='rep@example.test')
        self.assertEqual(row['target_progress'],84)
        self.assertEqual(self.path.read_bytes(),before)
        with self.assertRaises(ValueError):self.analysis.save_goal({**body,'amount':'NaN'})
        self.analysis.save_goal({**body,'amount':0});self.assertIsNone(self.report()['target'])
    def test_confirmed_admin_sales_keep_remote_attribution_without_guessing_conflicts(self):
        from desktop_workflows import Workflows
        now=dt.datetime.now(IST).isoformat()
        with Workflows(self.archive).db() as conn:
            for guid in ('native','bill'):
                conn.execute('INSERT INTO command(id,kind,payload,state,created_at,updated_at,result) VALUES(?,?,?,?,?,?,?)',(guid,'sale',json.dumps({'company':'Saarthi Demo Books'}),'confirmed',now,now,json.dumps({'guid':guid})))
        admin=self.report(rep='Admin desktop')
        self.assertEqual(admin['metrics']['gross'],300)
        self.assertEqual(self.report(rep='rep@example.test')['metrics']['gross'],0)
        self.assertEqual(self.report(rep='Unattributed')['metrics']['gross'],672)
    def test_latest_invoice_edit_updates_report_without_redecoding_unchanged_lines(self):
        self.report();conn=shop.open_db(str(self.path))
        store.save(conn,[dict(GUID='bill',ALTERID='200',DATE=self.end,VOUCHERTYPENAME='Sales',PARTYLEDGERNAME='Demo Traders',AMOUNT='-800',**{'ALLINVENTORYENTRIES.LIST':[dict(STOCKITEMNAME='HARPIC BLUE 500 ML MRP110',BILLEDQTY='10 PCS',AMOUNT='800')]})]);conn.close()
        with patch.object(desktop_analysis,'unpack',wraps=desktop_analysis.unpack) as decode:
            self.assertEqual(self.report()['metrics']['net'],1000);self.assertEqual(decode.call_count,1)
        with closing(sqlite3.connect(self.path)) as conn:conn.execute("INSERT INTO deleted_voucher VALUES('native',100,'2026-10-31')");conn.commit()
        self.assertEqual(self.report()['metrics']['net'],700)
    def test_missing_item_details_do_not_remove_invoice_revenue(self):
        conn=shop.open_db(str(self.path));store.save(conn,[dict(GUID='no-lines',ALTERID='201',DATE=self.end,VOUCHERTYPENAME='Sales',PARTYLEDGERNAME='Demo Traders',AMOUNT='-30')]);conn.close()
        d=self.report();self.assertEqual(d['metrics']['net'],902);self.assertEqual(d['missing_inventory_vouchers'],1)
        self.assertEqual(d['products'][0]['net'],872)
    def test_baseline_coverage_and_calendar_comparisons(self):
        self.assertEqual(period('20240229','20240229','year')[2:],('20230228','20230228'))
        self.assertEqual(period('20260531','20260531','quarter')[2:],('20260228','20260228'))
        d=self.analysis.report((self.today-dt.timedelta(days=100)).strftime('%Y%m%d'),self.end)
        self.assertFalse(d['baseline_covered']);self.assertIsNone(d['growth'])
        for start,end,mode in [('2026101','20261031','previous'),('20261031','20261001','previous'),('20261001','20261031','bogus')]:
            with self.assertRaises(ValueError):period(start,end,mode)
    def historical_sales(self):
        conn=shop.open_db(str(self.path))
        for guid,day,party,total,quantity,kind in [
            ('coverage','20220101','Demo Traders',10,'1 PCS','Sales'),
            ('prior-year','20230105','Demo Traders',100,'1 PCS','Sales'),
            ('jan','20240105','Demo Traders',200,'2 PCS','Sales'),
            ('feb','20240206','Demo Traders',300,'3 PCS','Sales'),
            ('return','20240210','Demo Traders',50,'1 PCS','Credit Note'),
            ('other-party','20240110','Other Traders',400,'1 BOX','Sales')]:
            store.save(conn,[dict(GUID=guid,ALTERID=str(300+len(guid)),DATE=day,VOUCHERTYPENAME=kind,PARTYLEDGERNAME=party,AMOUNT=str(-total),VOUCHERNUMBER=guid,**{'ALLINVENTORYENTRIES.LIST':[dict(STOCKITEMNAME='HARPIC BLUE 500 ML MRP110',BILLEDQTY=quantity,RATE=f'{total/int(quantity.split()[0])}/'+quantity.split()[1],AMOUNT=str(total))]})])
        conn.close()
    def test_year_has_twelve_months_and_all_time_compares_more_than_three_years(self):
        self.historical_sales()
        d=self.analysis.report(mode='year',year='2024')
        self.assertEqual((d['start'],d['end']),('20240101','20241231'))
        self.assertEqual(len(d['monthly']),12)
        self.assertEqual([r['net'] for r in d['monthly'][:3]],[600,250,0])
        self.assertEqual(d['metrics']['net'],850)
        self.assertEqual(d['monthly'][0]['previous_net'],100)
        self.assertEqual(d['monthly'][0]['change'],500)
        all_time=self.analysis.report(mode='all')
        years={r['label']:r for r in all_time['yearly']}
        self.assertEqual(all_time['start'],'20220101');self.assertEqual(all_time['daily'],[])
        self.assertEqual(years['2024']['net'],850);self.assertEqual(years['2025']['net'],0)
        self.assertIn(2025,all_time['years'])
        self.assertAlmostEqual(sum(r['net'] for r in all_time['yearly']),all_time['metrics']['net'])
        self.assertEqual(years['2024']['change'],750)
    def test_party_product_purchases_keep_quantities_rates_and_returns_isolated(self):
        self.historical_sales()
        d=self.analysis.report(mode='year',year='2024',party='Demo Traders')
        self.assertEqual(d['metrics']['net'],450)
        self.assertEqual(len(d['party_products']),1)
        product=d['party_products'][0]
        self.assertEqual((product['party'],product['unit'],product['quantity'],product['returned'],product['net_quantity']),('Demo Traders','PCS',5,1,4))
        self.assertEqual(product['last_price'],100);self.assertEqual(product['last_purchase'],'20240206')
        self.assertEqual(product['invoices'],2);self.assertEqual(len(d['party_invoices']),3)
        self.assertEqual(d['monthly'][0]['net'],200)
        other=self.analysis.report(mode='year',year='2024',party='Other Traders')
        self.assertEqual(other['metrics']['net'],400);self.assertEqual(other['waiting'],[])
        self.assertEqual(other['party_products'][0]['unit'],'BOX')
        self.assertTrue(all(r['party']=='Other Traders' for r in other['party_products']))
    def test_current_year_uses_elapsed_dates_and_future_months_have_no_comparison(self):
        d=self.analysis.report(mode='year',year=str(self.today.year))
        self.assertEqual(d['end'],self.today.strftime('%Y%m%d'))
        self.assertEqual(d['prior_end'],self.today.replace(year=self.today.year-1).strftime('%Y%m%d') if not (self.today.month==2 and self.today.day==29) else f'{self.today.year-1}0228')
        future=[r for r in d['monthly'] if r['future']]
        self.assertEqual(len(future),12-self.today.month)
        self.assertTrue(all(r['previous_net'] is None and r['change'] is None for r in future))
        with self.assertRaises(ValueError):self.analysis.report(mode='year',year='nonsense')
        with self.assertRaises(ValueError):self.analysis.report(mode='invalid')

if __name__=='__main__':unittest.main()
