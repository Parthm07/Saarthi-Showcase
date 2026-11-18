"""Admin writes tested with disposable books and mocked Tally only."""
import base64
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock,patch
from uuid import uuid4
import xml.etree.ElementTree as ET

from test_desktop_archive import fixture
from desktop_archive import Archive
from desktop_workflows import Workflows,confirmed_prices
import tally_admin as admin
import tally_shop as shop
import tally_store as store


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'tally_raw.db';fixture(self.path)
        self.local=shop.open_db(str(self.path));self.addCleanup(self.local.close)
        store.save_masters(self.local,'Ledger',[dict(GUID='sales',ALTERID='5',NAME='SALES',PARENT='Sales Accounts'),dict(GUID='purchase',ALTERID='6',NAME='PURCHASES',PARENT='Purchase Accounts')])
        store.save_masters(self.local,'Unit',[dict(GUID='unit',ALTERID='7',NAME='PCS')])
        store.save_masters(self.local,'StockGroup',[dict(GUID='sg',ALTERID='8',NAME='Home care',PARENT='Primary')])
        store.save_masters(self.local,'Godown',[dict(GUID='godown',ALTERID='9',NAME='Main Location')])
        self.work=Workflows(Archive(self.path));self.guard=patch.object(store,'verify_company',return_value='Saarthi Demo Books');self.guard.start();self.addCleanup(self.guard.stop)

    def bill(self,kind='purchase'):
        return dict(party='supplier' if kind=='purchase' else 'c',ledger='purchase' if kind=='purchase' else 'sales',date='2026-10-31',godown='Main Location',bill_no='BILL-7' if kind=='purchase' else '',lines=[dict(item='p',qty='3',rate='84')])

    def submit(self,kind='purchase',body=None):
        return self.work.submit(str(uuid4()),kind,body or self.bill(kind))

    def command(self,saved):
        with self.work.db() as conn:row=dict(conn.execute('SELECT * FROM command WHERE id=?',(saved['id'],)).fetchone())
        row['payload']=json.loads(row['payload']);return row

    def test_queue_is_durable_and_cannot_change_accounting_stock_optimistically(self):
        before=self.path.read_bytes();saved=self.submit()
        self.assertEqual(saved['state'],'queued');self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(Workflows(Archive(self.path)).history()[0]['id'],saved['id'])

    def test_same_request_is_replayed_and_different_body_is_refused(self):
        body=self.bill();saved=self.submit(body=body)
        self.assertEqual(self.work.submit(saved['id'],'purchase',body)['id'],saved['id'])
        body['lines'][0]['qty']='4'
        with self.assertRaises(ValueError):self.work.submit(saved['id'],'purchase',body)
        self.assertEqual(len(self.work.history()),1)

    def test_new_sale_intents_with_identical_lines_are_independent(self):
        first=self.submit('sale');second=self.submit('sale')
        self.assertNotEqual(first['id'],second['id']);self.assertEqual(len(self.work.history()),2)

    def test_history_pages_keep_old_unresolved_requests_accessible(self):
        saved=self.submit();command=self.command(saved)
        with self.work.db() as conn:
            for i in range(55):conn.execute("INSERT INTO command(id,kind,payload,state,created_at,updated_at) VALUES(?,?,?,'confirmed',?,?)",(str(uuid4()),'purchase',json.dumps(command['payload']),'2026-11-01','2026-11-01'))
        first=self.work.history_page();second=self.work.history_page(2)
        self.assertEqual(first['total'],56);self.assertEqual(len(second['rows']),6)
        self.assertEqual(first['rows'][0]['id'],saved['id'])

    def test_party_scope_and_finite_positive_money_are_enforced(self):
        for changes in ({'party':'c'},{'ledger':'sales'},{'godown':'missing'}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):self.submit(body={**self.bill(),**changes})
        for value in ('NaN','Infinity','-1','0'):
            body=self.bill();body['lines'][0]['rate']=value
            with self.subTest(value=value),self.assertRaises(ValueError):self.submit(body=body)

    def test_purchase_and_sale_xml_have_opposite_balanced_postings(self):
        for kind in ('purchase','sale'):
            saved=self.submit(kind);root=ET.fromstring(admin.build_xml(self.command(saved)))
            inventory=root.find('.//ALLINVENTORYENTRIES.LIST');party=root.find('.//LEDGERENTRIES.LIST')
            self.assertEqual(float(inventory.findtext('AMOUNT'))+float(party.findtext('AMOUNT')),0)
            self.assertEqual(inventory.findtext('ISDEEMEDPOSITIVE'),'Yes' if kind=='purchase' else 'No')
            self.assertEqual(inventory.findtext('BILLEDQTY'),'3 PCS')
            self.assertIsNone(root.find('.//VOUCHERNUMBER'))
            self.assertIsNone(root.find('.//GUID'))
            self.assertEqual(root.find('.//VOUCHER').get('REMOTEID'),saved['id'])
            self.assertEqual(root.findtext('.//REFERENCE'),'BILL-7' if kind=='purchase' else 'ADM-'+saved['id'])

    def test_master_creation_and_rename_escape_xml_and_preserve_identity(self):
        with self.assertRaisesRegex(ValueError,'Create products in Tally'):
            self.submit('product_create',dict(name='New product',group='Home care',unit='PCS'))
        saved=self.submit('product_update',dict(item='p',name='New name',price='79.50'))
        root=ET.fromstring(admin.build_xml(self.command(saved)))
        self.assertEqual(root.findtext('.//GUID'),'p');self.assertEqual(root.find('.//STOCKITEM').get('ACTION'),'Alter')
        self.assertEqual(root.find('.//STOCKITEM').get('NAME'),'HARPIC BLUE 500 ML MRP110')

    def test_master_create_only_adopts_a_matching_name_after_preflight_claim(self):
        command=self.command(self.submit('party_create',dict(name='New shop',group='Dewas Debtors')))
        writer=admin.TallyWriter(self.local);writer.master=Mock(return_value=dict(GUID='native-guid',NAME='New shop',PARENT='Dewas Debtors'))
        self.assertIsNone(writer.lookup(command));writer.master.assert_not_called()
        command['state']='processing'
        self.assertEqual(writer.lookup(command)['guid'],'native-guid')

    def test_confirmed_readback_prevents_second_import(self):
        saved=self.submit();writer=Mock();writer.lookup.return_value=dict(guid=saved['id'],number='9')
        self.assertEqual(admin.run_pending(self.local,writer=writer,log=lambda _:None),1)
        writer.send.assert_not_called();self.assertEqual(self.work.history()[0]['state'],'confirmed')

    def test_missing_import_response_is_held_and_recheck_never_resends(self):
        saved=self.submit();writer=Mock();writer.lookup.return_value=None;writer.send.side_effect=TimeoutError('reply lost')
        admin.run_pending(self.local,writer=writer,log=lambda _:None)
        self.assertEqual(self.work.history()[0]['state'],'uncertain')
        self.work.retry(saved['id']);admin.run_pending(self.local,writer=writer,log=lambda _:None)
        self.assertEqual(writer.send.call_count,1);self.assertEqual(self.work.history()[0]['state'],'uncertain')
        writer.lookup.return_value=dict(guid=saved['id'],number='9');self.work.retry(saved['id'])
        admin.run_pending(self.local,writer=writer,log=lambda _:None)
        self.assertEqual(self.work.history()[0]['state'],'confirmed');self.assertEqual(writer.send.call_count,1)

    def test_process_crash_after_claim_only_reconciles(self):
        saved=self.submit()
        with self.work.db() as conn:conn.execute("UPDATE command SET state='processing' WHERE id=?",(saved['id'],))
        writer=Mock();writer.lookup.return_value=None
        admin.run_pending(self.local,writer=writer,log=lambda _:None)
        writer.send.assert_not_called();self.assertEqual(self.work.history()[0]['state'],'uncertain')

    def test_positive_import_reply_still_requires_readback(self):
        self.submit();writer=Mock();writer.lookup.return_value=None;writer.send.return_value=dict(created=1)
        admin.run_pending(self.local,writer=writer,log=lambda _:None)
        self.assertEqual(self.work.history()[0]['state'],'uncertain')

    def test_definite_refusal_can_retry_but_zero_counters_are_uncertain(self):
        for outcome,state in ((dict(created=0,errors=1,messages=['Refused']),'failed'),(dict(created=0,errors=0),'uncertain')):
            saved=self.submit();writer=Mock();writer.lookup.return_value=None;writer.send.return_value=outcome
            admin.run_pending(self.local,writer=writer,log=lambda _:None)
            self.assertEqual(self.work.detail(saved['id'])['state'],state)

    def test_wrong_company_never_imports(self):
        saved=self.submit();store._set(self.local,'company','Other company');self.local.commit()
        writer=Mock();admin.run_pending(self.local,writer=writer,log=lambda _:None)
        writer.send.assert_not_called();self.assertEqual(self.work.detail(saved['id'])['state'],'failed')

    def test_supplier_bill_duplicate_is_checked_within_financial_year(self):
        command=self.command(self.submit());writer=admin.TallyWriter(self.local)
        writer.master=Mock(side_effect=lambda obj,guid=None,name=None:dict(NAME={'supplier':'Demo Supplier','purchase':'PURCHASES','p':'HARPIC BLUE 500 ML MRP110'}[guid]))
        existing=dict(REFERENCE='BILL-7',PARTYLEDGERNAME='Demo Supplier',DATE='20261001')
        with patch.object(admin.t,'collection',return_value=[existing]):
            with self.assertRaisesRegex(ValueError,'already exists'):writer.prepare(command)
        existing['DATE']='20251001'
        with patch.object(admin.t,'collection',return_value=[existing]):self.assertIn('<VOUCHER',writer.prepare(command))

    def test_voucher_readback_uses_request_marker_and_returns_tally_native_guid(self):
        command=self.command(self.submit());writer=admin.TallyWriter(self.local)
        row=dict(GUID='tally-native-guid',VOUCHERNUMBER='P-99',VOUCHERTYPENAME='Purchase',AMOUNT='252',DATE='20261031',PARTYLEDGERNAME='Demo Supplier',REFERENCE='BILL-7',NARRATION='Saarthi Admin '+command['id'])
        with patch.object(admin.t,'collection',return_value=[row]):self.assertEqual(writer.lookup(command)['guid'],'tally-native-guid')
        row['PARTYLEDGERNAME']='Another supplier'
        with patch.object(admin.t,'collection',return_value=[row]),self.assertRaises(ValueError):writer.lookup(command)

    def test_rename_refuses_a_master_altered_after_selection(self):
        command=self.command(self.submit('product_update',dict(item='p',name='Changed name')))
        writer=admin.TallyWriter(self.local);writer.master=Mock(return_value=dict(NAME='HARPIC BLUE 500 ML MRP110',ALTERID='900',GUID='p'))
        with self.assertRaisesRegex(ValueError,'changed in Tally'):writer.prepare(command)

    def test_local_our_rate_overrides_sales_and_previous_confirmed_imports(self):
        from desktop_management import save_our_rates
        saved=self.submit('product_update',dict(item='p',name='HARPIC BLUE 500 ML MRP110',price='80'))
        self.assertEqual(confirmed_prices(self.path),{})
        with self.work.db() as conn:conn.execute("UPDATE command SET state='confirmed',result=? WHERE id=?",(json.dumps({'guid':'p'}),saved['id']))
        self.assertEqual(shop.catalogue(self.local)[0]['rate'],80)
        save_our_rates(self.work, {'rates':[{'item':'p','price':'78.50'}]})
        product=shop.catalogue(self.local)[0];self.assertEqual(product['rate'],78.5);self.assertEqual(product['rate_source'],'saarthi')
        self.assertEqual(Archive(self.path).listing('products')['rows'][0]['rate'],78.5)
        self.assertEqual(len(self.work.history()),1)
        self.assertEqual(Archive(self.path).overview()['sales'],672)

    def test_image_and_request_backups_survive_without_modifying_tally_names(self):
        image=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2nS8AAAAASUVORK5CYII=')
        self.work.save_image('p',base64.b64encode(image).decode());self.submit()
        self.assertEqual(self.work.image('p'),('image/png',image))
        with self.assertRaises(ValueError):self.work.save_image('missing',base64.b64encode(image).decode())
        with self.assertRaises(ValueError):self.work.save_image('p',base64.b64encode(b'<svg/>').decode())
        path=self.work.backup(Path(self.temp.name)/'backups')
        with closing(sqlite3.connect(path)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM command').fetchone()[0],1)
            self.assertEqual(conn.execute('SELECT body FROM product_image').fetchone()[0],image)

    def test_website_rate_is_local_and_replaying_it_preserves_a_later_desktop_edit(self):
        from desktop_management import save_our_rates
        from tally_cloud import apply_website_rates

        cursor = Mock()
        cursor.fetchone.side_effect = [('pending_our_rate',), (store._get(self.local, 'company'),)] * 3
        revision = str(uuid4())
        cursor.fetchall.return_value = [('p', 76.25, revision)]
        self.assertEqual(apply_website_rates(self.local, cursor, str(self.path)), [('p', revision)])
        self.assertEqual(Archive(self.path).listing('products')['rows'][0]['rate'], 76.25)
        save_our_rates(self.work, {'rates': [{'item': 'p', 'price': 77}]})
        self.assertEqual(apply_website_rates(self.local, cursor, str(self.path)), [('p', revision)])
        self.assertEqual(shop.catalogue(self.local)[0]['rate'], 77)

        new_revision = str(uuid4())
        cursor.fetchall.return_value = [('p', 0, new_revision), ('unknown', 12, new_revision)]
        self.assertEqual(apply_website_rates(self.local, cursor, str(self.path)), [('p', new_revision)])
        self.assertEqual(shop.catalogue(self.local)[0]['rate'], 0)


if __name__=='__main__':unittest.main()
