"""Purchase rates are read-only; margins respect invoice dates and units."""
import datetime as dt
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from uuid import uuid4
from test_desktop_archive import fixture
from desktop_archive import Archive,IST
from desktop_analysis import Analysis
from desktop_management import Management,inventory
from desktop_workflows import Workflows
from purchase_costs import history,latest,push
import tally_shop as shop
import tally_store as store


class PurchaseCostTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'tally_raw.db';fixture(self.path)
        self.local=shop.open_db(str(self.path));self.addCleanup(self.local.close)
        self.archive=Archive(self.path);self.work=Workflows(self.archive)
        self.today=dt.datetime.now(IST).date()
    def purchase(self,guid='buy',days=1,rate=50,version=900,unit='PCS',cancel='No',qty=10):
        store.save(self.local,[dict(GUID=guid,ALTERID=str(version),DATE=(self.today-dt.timedelta(days=days)).strftime('%Y%m%d'),VOUCHERTYPENAME='Purchase',PARTYLEDGERNAME='Demo Supplier',ISCANCELLED=cancel,AMOUNT=str(-qty*rate),**{'ALLINVENTORYENTRIES.LIST':[dict(STOCKITEMNAME='HARPIC BLUE 500 ML MRP110',BILLEDQTY=f'{qty} {unit}',RATE=f'{rate}/{unit}',AMOUNT=str(-qty*rate))]})])
    def report(self):return Management(self.archive,Analysis(self.archive)).report()
    def test_product_cost_is_tally_derived_and_selling_edit_stays_separate(self):
        self.purchase();before=self.path.read_bytes();p=inventory(self.work)['products'][0]
        self.assertEqual(p['purchase_rate'],50);self.assertEqual(p['purchase_unit'],'PCS')
        self.assertEqual(p['estimated_margin'],40.48);self.assertEqual(before,self.path.read_bytes())
        self.assertEqual(p['rate'],84)
    def test_margin_uses_cost_before_sale_and_excludes_future_purchase(self):
        self.purchase();self.purchase('future',days=-1,rate=70,version=901)
        row=next(r for r in self.report()['customers'] if r['name']=='Demo Traders')
        self.assertEqual(row['purchase_cost'],400);self.assertEqual(row['profit'],272)
        self.assertEqual(row['margin'],40.48);self.assertEqual(row['markup'],68)
        self.assertIn('Estimate',row['cost_note'])
    def test_later_rate_does_not_backfill_earlier_sales_and_units_are_not_guessed(self):
        self.purchase(days=-1)
        self.assertIsNone(self.report()['customers'][0]['profit'])
        self.purchase('boxes',days=1,unit='BOX',version=902)
        self.assertIsNone(self.report()['customers'][0]['profit'])
    def test_corrections_cancellations_and_deletions_invalidate_the_cost_cache(self):
        self.purchase()
        with self.archive.read() as conn:self.assertEqual(latest(history(self.archive,conn),'HARPIC BLUE 500 ML MRP110','PCS')['rate'],50)
        self.purchase(rate=60,version=901)
        with self.archive.read() as conn:self.assertEqual(latest(history(self.archive,conn),'HARPIC BLUE 500 ML MRP110','PCS')['rate'],60)
        self.purchase(rate=60,version=902,cancel='Yes')
        with self.archive.read() as conn:self.assertIsNone(latest(history(self.archive,conn),'HARPIC BLUE 500 ML MRP110','PCS'))
    def test_credit_note_reverses_estimated_cost_and_profit(self):
        self.purchase()
        store.save(self.local,[dict(GUID='return',ALTERID='950',DATE=self.today.strftime('%Y%m%d'),VOUCHERTYPENAME='Credit Note',PARTYLEDGERNAME='Demo Traders',AMOUNT='168',**{'ALLINVENTORYENTRIES.LIST':[dict(STOCKITEMNAME='HARPIC BLUE 500 ML MRP110',BILLEDQTY='2 PCS',AMOUNT='-168')]})])
        row=self.report()['customers'][0]
        self.assertEqual(row['purchase_cost'],300);self.assertEqual(row['profit'],204);self.assertEqual(row['margin'],40.48)

    def test_new_product_creation_is_rejected_without_queuing_any_request(self):
        with self.assertRaisesRegex(ValueError,'Create products in Tally'):
            self.work.submit(str(uuid4()),'product_create',dict(name='New product'))
        self.assertEqual(self.work.history(),[])
    def test_owner_data_does_not_mutate_the_shared_salesperson_catalogue(self):
        import api
        shared=[dict(id='p',name='Product',grp='Home care')];owner=Mock();owner.fetchone.return_value=('owner_purchase_rate',)
        owner.fetchall.side_effect=[[('p',50,'PCS','20261101')],[],[]]
        with patch.object(api,'_catalogue',return_value=(shared,{})):
            data=api.data(user=dict(role='admin',email='owner@example.test'),cur=owner)
            self.assertEqual(data['products'][0]['purchaseRate'],50)
            sales=Mock();sales.fetchall.side_effect=[[],[]]
            other=api.data(user=dict(role='salesperson',email='rep@example.test'),cur=sales)
        self.assertNotIn('purchaseRate',shared[0]);self.assertNotIn('purchaseRate',other['products'][0])
        with self.assertRaises(api.HTTPException) as error:api.purchase_rates(user=dict(role='salesperson'),cur=Mock())
        self.assertEqual(error.exception.status_code,403)
    def test_cloud_upload_contains_only_latest_cost_fact_not_supplier_or_purchase_lines(self):
        self.purchase();cur=Mock();cur.fetchone.return_value=('owner_purchase_rate',)
        push(self.local,cur)
        rows=cur.executemany.call_args.args[1]
        self.assertEqual(len(rows),1);self.assertEqual(rows[0][0],'p');self.assertEqual(rows[0][1],50)
        self.assertNotIn('Demo Supplier',str(rows))

    def test_only_owner_can_queue_an_our_rate_for_a_website_product(self):
        import api

        cursor = Mock()
        rate = api.OurRateIn(price='0')
        with self.assertRaises(api.HTTPException) as error:
            api.update_our_rate('p', rate, user=dict(role='salesperson'), cur=cursor)
        self.assertEqual(error.exception.status_code, 403)
        cursor.execute.assert_not_called()

        cursor.fetchone.side_effect = [('pending_our_rate',), (0,)]
        self.assertEqual(api.update_our_rate('p', rate, user=dict(role='admin'), cur=cursor), {'price': 0})
        query, params = cursor.execute.call_args.args
        self.assertIn('FROM item WHERE guid=%s', query)
        self.assertEqual((params[0], params[2]), (0, 'p'))


if __name__=='__main__':unittest.main()
