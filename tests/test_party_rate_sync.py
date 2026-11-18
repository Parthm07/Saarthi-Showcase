"""Customer-specific negotiated prices must reach the regular cloud push."""
import pathlib
import sys
from tempfile import TemporaryDirectory
from contextlib import closing
import unittest
from unittest.mock import ANY, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import tally_cloud as cloud
import tally_shop as shop
import tally_store as store


class RateCursor:
    def __init__(self):
        self.rates = {('Sachin', 'Harpic Blue'):(84, '20261029', 1, 84),
                      ('Other shop', 'Harpic Blue'):(84, '20261029', 1, 84)}
        self.rows = []
        self.items = []
    def execute(self, sql, args=()):
        if sql.startswith('SELECT to_regclass('):
            self.rows = [(None,)]
        elif sql.startswith(('SELECT party,item,cyc_lo','SELECT vdate,party,salesperson')):
            self.rows = []
        elif sql.startswith('SELECT party,item,'):
            self.rows = [(*key, *value) for key,value in self.rates.items()]
        elif 'pg_advisory_xact_lock' in sql:
            self.rows = []
        elif sql.startswith(('CREATE TEMP TABLE', 'DELETE FROM item', 'DELETE FROM customer')) or sql.lstrip().startswith(('INSERT INTO item', 'INSERT INTO customer', 'INSERT INTO sync_status')):
            self.rows = []
        else:
            raise AssertionError(sql)
    def executemany(self, sql, rows):
        for row in rows:
            if sql.startswith('INSERT INTO t_item'):
                self.items.append(tuple(row))
            elif sql.startswith('INSERT INTO party_rate'):
                self.rates[tuple(row[:2])] = tuple(row[2:])
            elif sql.startswith('DELETE FROM party_rate'):
                self.rates.pop(tuple(row), None)
            else:
                raise AssertionError(sql)
    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class PartyRateTests(unittest.TestCase):
    def test_customer_catalogue_excludes_creditors_and_cash_ledgers(self):
        local=shop.open_db(':memory:')
        try:
            store.save_masters(local,'Group',[dict(GUID='g',ALTERID='1',
                NAME='Dewas Debtors',PARENT='Sundry Debtors')])
            store.save_masters(local,'Ledger',[
                dict(GUID='buyer',ALTERID='2',NAME='Buyer',PARENT='Dewas Debtors'),
                dict(GUID='supplier',ALTERID='3',NAME='Supplier',PARENT='Sundry Creditors'),
                dict(GUID='cash',ALTERID='4',NAME='Cash',PARENT='Cash-in-Hand')])
            self.assertEqual([c['guid'] for c in shop.customers(local)],['buyer'])
        finally:
            local.close()

    def test_our_rate_comes_from_latest_product_invoice_and_keeps_exact_master_name(self):
        local = shop.open_db(':memory:')
        try:
            name = 'HARPIC BLUE 500 ML MRP110'
            store.save_masters(local,'StockItem',[dict(GUID='p',ALTERID='1',NAME=name,
                PARENT='Cleaning',STANDARDPRICE='84/PCS',BASEUNITS='PCS')])
            for guid,version,party,rate,optional in [('v1',2,'Sachin',80,'No'),
                    ('v2',3,'Other shop',82,'No'),('v3',4,'Other shop',99,'Yes')]:
                store.save(local,[dict(GUID=guid,ALTERID=str(version),DATE='20261030',
                    VOUCHERTYPENAME='Sales',PARTYLEDGERNAME=party,AMOUNT=str(-rate),
                    ISCANCELLED='No',ISOPTIONAL=optional,**{'ALLINVENTORYENTRIES.LIST':[
                        dict(STOCKITEMNAME=name,RATE=f'{rate}/PCS') ]})])
            shop.rebuild_party_rates(local,log=lambda *a:None)
            product = shop.catalogue(local)[0]
            self.assertEqual(product['name'],name)
            self.assertEqual(product['rate'],82)
            self.assertEqual(product['standard_rate'],84)
            self.assertEqual(shop.catalogue(local,party='Sachin')[0]['rate'],80)
            store.save_masters(local,'StockItem',[dict(GUID='p',ALTERID='6',NAME=name,
                PARENT='Cleaning',BASEUNITS='PCS')])
            self.assertEqual(shop.catalogue(local)[0]['rate'],82)
            store.save(local,[dict(GUID='v2',ALTERID='5',DATE='20261030',
                VOUCHERTYPENAME='Sales',PARTYLEDGERNAME='Other shop',ISCANCELLED='Yes')])
            shop.rebuild_party_rates(local,log=lambda *a:None)
            self.assertEqual(shop.catalogue(local)[0]['rate'],80)
            store.save_masters(local,'StockItem',[dict(GUID='unsold',ALTERID='7',NAME='Unsold item',
                PARENT='Cleaning',STANDARDPRICE='50/PCS',BASEUNITS='PCS')])
            unsold = next(p for p in shop.catalogue(local) if p['guid']=='unsold')
            self.assertEqual(unsold['standard_rate'],50)
            self.assertIsNone(unsold['rate'])
        finally:
            local.close()

    def test_purchase_count_counts_distinct_invoices_and_rebuilds_do_not_increment_it(self):
        local=shop.open_db(':memory:')
        try:
            for version in range(1,5):
                store.save(local,[dict(GUID=f'v{version}',ALTERID=str(version),DATE='20261030',
                    VOUCHERTYPENAME='Sales',PARTYLEDGERNAME='Sachin',AMOUNT='-160',
                    ISCANCELLED='No',ISOPTIONAL='No',**{'ALLINVENTORYENTRIES.LIST':[
                        dict(STOCKITEMNAME='Harpic Blue',RATE='80/PCS',BILLEDQTY='1 PCS'),
                        dict(STOCKITEMNAME='Harpic Blue',RATE='80/PCS',BILLEDQTY='1 PCS')]})])
            for _ in range(2):
                shop.rebuild_party_rates(local,log=lambda *a:None)
                self.assertEqual(local.execute('SELECT times FROM party_item_rate').fetchone()[0],4)
                self.assertEqual(local.execute('SELECT COUNT(*) FROM party_item_rate').fetchone()[0],1)
        finally:
            local.close()

    def test_customer_discount_publishes_without_changing_other_customer(self):
        local = shop.open_db(':memory:')
        try:
            store._set(local, 'company', 'Test Books')
            local.executemany('INSERT INTO party_item_rate VALUES(?,?,?,?,?,?)', [
                ('Sachin','Harpic Blue','20261030',80,2,84),
                ('Other shop','Harpic Blue','20261029',84,1,84)])
            cursor = RateCursor()
            with patch.object(cloud, '_guard'):
                self.assertEqual(cloud.push_party_rates(local,cursor,log=lambda *a:None),1)
                self.assertEqual(cursor.rates[('Sachin','Harpic Blue')][0],80)
                self.assertEqual(cursor.rates[('Other shop','Harpic Blue')][0],84)
                self.assertEqual(cloud.push_party_rates(local,cursor,log=lambda *a:None),0)
        finally:
            local.close()

    def test_regular_push_includes_customer_rates(self):
        local = shop.open_db(':memory:')
        local.executescript(cloud.ins.SCHEMA)
        try:
            store._set(local, 'company', 'Test Books')
            local.executemany('INSERT INTO party_item_rate VALUES(?,?,?,?,?,?)', [
                ('Sachin','Harpic Blue','20261030',80,2,84),
                ('Other shop','Harpic Blue','20261029',84,1,84)])
            cursor = RateCursor()
            item=dict(guid='p',name='Exact Tally Name',group='Cleaning',unit='PCS',rate=80,
                      standard_rate=84,mrp=110,on_hand=3,saleable=3,stock_asof=None)
            with patch.object(cloud, '_guard'), \
                 patch.object(shop, 'catalogue', return_value=[item,dict(item,guid='zero',rate=0),
                                                               dict(item,guid='overflow',rate=10_000_000_000)]), \
                 patch.object(shop, 'customers', return_value=[]), \
                 patch.object(cloud.ins, 'metrics', return_value={}), \
                 patch.object(shop, 'freshness', return_value={'company':'Test Books','stock_asof':None,'voucher_watermark':1}), \
                 patch.object(cloud, 'billing_state', return_value={'waiting':0,'oldest_mins':0,'set_aside':0,'blocked_since':'','error':''}):
                cloud.push_fast(local,cursor,log=lambda *a:None)
            self.assertEqual(cursor.rates[('Sachin','Harpic Blue')][0],80)
            self.assertEqual(cursor.rates[('Other shop','Harpic Blue')][0],84)
            self.assertEqual(cursor.items[0][1],'Exact Tally Name')
            self.assertEqual(cursor.items[0][4],80)
            self.assertEqual(cursor.items[1][4],0)
            self.assertIsNone(cursor.items[2][4])
        finally:
            local.close()

    def test_website_visibility_is_reversible_without_removing_the_desktop_product(self):
        from desktop_archive import Archive
        from desktop_management import inventory, save_visibility
        from desktop_workflows import Workflows

        with TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / 'tally_raw.db'
            with closing(shop.open_db(str(path))) as local:
                local.executescript(cloud.ins.SCHEMA)
                store._set(local, 'company', 'Test Books')
                store.save_masters(local, 'StockItem', [
                    dict(GUID='p', ALTERID='1', NAME='Product', BASEUNITS='PCS')])
                work = Workflows(Archive(path))
                with patch.object(cloud, '_guard') as guard:
                    for hidden in (False, True, False):
                        save_visibility(work, {'guid':'p', 'hidden':hidden})
                        self.assertEqual(inventory(work)['products'][0]['website_hidden'], hidden)
                        self.assertEqual(work.context()['products'][0]['id'], 'p')
                        cur = RateCursor()
                        cloud.push_fast(local, cur, log=lambda _:None)
                        self.assertEqual([row[0] for row in cur.items], [] if hidden else ['p'])
                        guard.assert_any_call(cur, 'item', 1, ANY)

                store.save_masters(local, 'StockItem', [
                    dict(GUID='q', ALTERID='2', NAME='Second product', BASEUNITS='PCS')])
                with self.assertRaises(ValueError):
                    save_visibility(work, {'guids':['p','missing'], 'hidden':True})
                self.assertFalse(any(p['website_hidden'] for p in inventory(work)['products']))
                save_visibility(work, {'guids':['p','q'], 'hidden':True})
                self.assertTrue(all(p['website_hidden'] for p in inventory(work)['products']))

    def test_latest_confirmed_rate_wins_over_same_day_older_and_optional_sales(self):
        local = shop.open_db(':memory:')
        try:
            # GUID ordering intentionally differs from the source edit order.
            for guid,version,rate,optional in [('z',10,84,'No'),('a',11,80,'No'),('q',12,60,'Yes')]:
                store.save(local,[dict(GUID=guid,ALTERID=str(version),DATE='20261030',
                    VOUCHERTYPENAME='Sales',PARTYLEDGERNAME='Sachin',AMOUNT=str(-rate),
                    ISCANCELLED='No',ISOPTIONAL=optional,
                    **{'ALLINVENTORYENTRIES.LIST':[dict(STOCKITEMNAME='Harpic Blue',RATE=f'{rate}/PCS') ]})])
            shop.rebuild_party_rates(local, log=lambda *a:None)
            self.assertEqual(shop.party_rates(local,'Sachin')['Harpic Blue']['rate'],80)
        finally:
            local.close()


if __name__ == '__main__':
    unittest.main()
