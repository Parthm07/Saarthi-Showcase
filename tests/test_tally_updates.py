"""Tally edits must replace cloud projections, never the submitted order."""
import pathlib
import sys
import unittest
import os
import datetime as dt
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import tally_shop as shop
import tally_store as store
import tally_updates as updates
os.environ['SESSION_SECRET'] = 'isolated-invoice-tests'
import api


class TallyEditTests(unittest.TestCase):
    def setUp(self):
        self.local = shop.open_db(':memory:')
        self.local.execute("INSERT INTO shop_order(order_no,created_at,party_guid,party_name,status,subtotal,gst,total,tally_guid,tally_vchno) VALUES('WEB-TEST','2026-10-30','p','Test Party','invoiced',252,0,252,'v','4440')")

    def tearDown(self):
        self.local.close()

    def save(self, version, qty=8, **changes):
        row = {'GUID':'v','ALTERID':str(version),'DATE':'20261030',
               'VOUCHERTYPENAME':'Sales','VOUCHERNUMBER':'4440',
               'PARTYLEDGERNAME':'Test Party','AMOUNT':str(-qty*84),
               'ISCANCELLED':'No','ISOPTIONAL':'No',
               'ALLINVENTORYENTRIES.LIST':[{'STOCKITEMNAME':'Test item',
                 'BILLEDQTY':f'{qty} PCS','RATE':'84/PCS','AMOUNT':str(qty*84)}]}
        row.update(changes)
        store.save(self.local,[row])

    def test_quantity_edit_uses_latest_version_and_keeps_original_order(self):
        self.save(10,3); self.save(11,8)
        plan = updates.plan(self.local, {'v':(10,'active')}, {'Test Party'})
        self.assertEqual(len(plan['headers']),1)
        self.assertEqual(plan['headers'][0][5],672)
        self.assertEqual(plan['lines'][0][3:7],(8,'PCS',84,672))
        self.assertEqual(self.local.execute('SELECT total FROM shop_order').fetchone()[0],252)
        self.assertEqual(self.local.execute('SELECT count(*) FROM voucher').fetchone()[0],2)

    def test_unchanged_and_older_versions_do_not_replace_cloud(self):
        self.save(11)
        for version in (11,12):
            self.assertEqual(updates.plan(self.local,{'v':(version,'active')},{'Test Party'})['headers'],[])

    def test_cancellation_clears_active_lines_but_retains_source_state(self):
        self.save(12,ISCANCELLED='Yes')
        plan = updates.plan(self.local,{'v':(11,'active')},{'Test Party'})
        self.assertEqual(plan['headers'][0][7],'cancelled')
        self.assertEqual(plan['lines'],[])
        self.assertEqual(plan['clear'],['v'])

    def test_confirmed_deletion_and_reappearance_are_propagated(self):
        self.save(11)
        self.local.execute("INSERT INTO deleted_voucher(guid,detected_at) VALUES('v','2026-10-30')")
        plan = updates.plan(self.local,{'v':(11,'active')},{'Test Party'})
        self.assertEqual(plan['headers'][0][7],'deleted')
        self.local.execute("DELETE FROM deleted_voucher WHERE guid='v'")
        plan = updates.plan(self.local,{'v':(11,'deleted')},{'Test Party'})
        self.assertEqual(plan['headers'][0][7],'active')

    def test_purchase_and_out_of_projection_parties_are_withheld(self):
        self.save(12,VOUCHERTYPENAME='Purchase',PARTYLEDGERNAME='Private Supplier')
        plan = updates.plan(self.local,{'v':(11,'active')},{'Test Party'})
        self.assertEqual(plan['headers'],[])
        self.assertEqual(plan['withheld'],[(12,'v')])
        self.assertEqual(plan['clear'],['v'])

    def test_backdated_edit_is_detected_without_todays_date_filter(self):
        self.save(12,DATE='20260401')
        plan = updates.plan(self.local,{'v':(11,'active')},{'Test Party'})
        self.assertEqual(plan['headers'][0][2],'20260401')

    def test_expired_details_are_not_reuploaded_but_retained_legacy_rows_can_be_verified(self):
        self.save(12,DATE='20260401')
        self.assertEqual(updates.plan(self.local,{}, {'Test Party'},retention_floor='20261023')['headers'],[])
        self.assertEqual(len(updates.plan(self.local,{}, {'Test Party'},retention_floor='20261023',protected={'v'})['headers']),1)
        self.assertEqual(len(updates.plan(self.local,{'v':(None,'active')}, {'Test Party'},retention_floor='20261023')['headers']),1)


class InvoiceCursor:
    def __init__(self, allowed=True):
        self.allowed = allowed
        self.rows = []
        self.invoice_lines_read = False

    def execute(self, sql, args=()):
        if sql.startswith('SELECT o.order_no'):
            self.rows = [('WEB-TEST',dt.datetime(2026,10,30,tzinfo=dt.timezone.utc),
              'rep@example.com','Test Party','Route','invoiced',252,'4440',True,
              'v','Test Party','20261030','4440',672,11,'active',dt.datetime.now(dt.timezone.utc),'2026-10-31')]
        elif sql.startswith('SELECT name FROM customer'):
            self.rows = [('Test Party',)] if self.allowed else []
        elif 'FROM cloud_order_line' in sql:
            self.rows = [('WEB-TEST',1,'Test item','PCS',3,84,252)]
        elif sql.startswith('SELECT h.guid,h.party'):
            self.rows = [('native','Test Party','20261030','4441',840,12,'active',None)]
        elif 'FROM history_line' in sql:
            self.invoice_lines_read = True
            self.rows = [('v',1,'Test item','PCS',8,84,672)]
        else:
            raise AssertionError(sql)

    def fetchall(self):
        return self.rows


class InvoiceReadTests(unittest.TestCase):
    def test_api_returns_current_invoice_separately_from_original_order(self):
        result = api.my_orders({'role':'admin','email':'office@example.com','routes':None},InvoiceCursor())[0]
        self.assertEqual(result['total'],252)
        self.assertEqual(result['lines'][0]['qty'],3)
        self.assertEqual(result['invoice']['total'],672)
        self.assertEqual(result['invoice']['lines'][0]['qty'],8)

    def test_salesman_sees_current_invoice_only_for_an_authorized_party(self):
        user = {'role':'salesperson','email':'rep@example.com','routes':['Route']}
        self.assertEqual(api.my_orders(user,InvoiceCursor())[0]['invoice']['total'],672)
        cursor = InvoiceCursor(allowed=False)
        result = api.my_orders(user,cursor)[0]
        self.assertIsNone(result['invoice'])
        self.assertTrue(result['invoice_restricted'])
        self.assertFalse(cursor.invoice_lines_read)

    def test_only_owners_can_request_sales_entered_directly_in_tally(self):
        owner = {'role':'admin','email':'office@example.com','routes':None}
        result = api.my_orders(owner, InvoiceCursor(), include_tally=True)
        native = next(order for order in result if order['order_no']=='TALLY-native')
        self.assertEqual(native['invoice']['total'], 840)
        self.assertEqual(native['invoice']['number'], '4441')
        self.assertEqual(native['source'], 'tally')
        self.assertEqual(native['desk_date'], '2026-10-30')
        website = next(order for order in result if order['order_no']=='WEB-TEST')
        self.assertEqual(website['source'], 'saarthi')
        self.assertEqual(website['invoice_date'], '2026-10-31')
        self.assertEqual(website['desk_date'], '2026-10-30')
        self.assertEqual(len(result), 2)
        rep = {'role':'salesperson','email':'rep@example.com','routes':['Route']}
        self.assertEqual([order['order_no'] for order in api.my_orders(rep, InvoiceCursor(), include_tally=True)],
                         ['WEB-TEST'])


if __name__ == '__main__':
    unittest.main()
