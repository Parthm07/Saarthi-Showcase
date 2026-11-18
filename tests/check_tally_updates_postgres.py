"""Real SQL check in temporary tables on a READ ONLY cloud connection.

Run explicitly: python tests/check_tally_updates_postgres.py --temporary-tables
No permanent tables, website orders, or Tally vouchers are changed.
"""
import argparse
import pathlib
import sys
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import psycopg
import cloud_db
import tally_shop as shop
import tally_store as store
import tally_updates as updates
import tally_cloud as cloud
import tally_insights as insights


def run():
    local = shop.open_db(':memory:')
    local.executescript(insights.SCHEMA)
    store._set(local, 'company', 'isolated-tally-update-check')
    def save(version, qty, cancelled='No'):
        store.save(local,[dict(GUID='test-v',ALTERID=str(version),DATE='20261030',
          VOUCHERTYPENAME='Sales',VOUCHERNUMBER='TEST',PARTYLEDGERNAME='Test Party',
          AMOUNT=str(-qty*84),ISCANCELLED=cancelled,ISOPTIONAL='No',
          **{'ALLINVENTORYENTRIES.LIST':[dict(STOCKITEMNAME='Test Item',
             BILLEDQTY=f'{qty} PCS',RATE='84/PCS',AMOUNT=str(qty*84))]})])
    with psycopg.connect(cloud_db.env('CLOUD_DB_URL'),connect_timeout=5,sslmode='require',
         options='-c default_transaction_read_only=on -c statement_timeout=10000') as cx:
        with cx.cursor() as cur:
            # Postgres requires a read-write transaction to CREATE temporary
            # tables. Only TEMP objects are created, then all sync
            # statements run in a fresh read-only transaction.
            cur.execute('SET TRANSACTION READ WRITE')
            cur.execute('CREATE TEMP TABLE history_voucher (guid TEXT PRIMARY KEY,party TEXT,'
              'vdate TEXT,vchtype TEXT,number TEXT,amount NUMERIC,source_alterid BIGINT,'
              "source_state TEXT DEFAULT 'active',observed_at TIMESTAMPTZ,retention_hold BOOLEAN DEFAULT FALSE)")
            cur.execute('CREATE TEMP TABLE cloud_order(tally_guid TEXT)')
            cur.execute('CREATE TEMP TABLE history_line (voucher_guid TEXT REFERENCES history_voucher(guid),'
              'line_no INTEGER,item TEXT,qty NUMERIC,unit TEXT,rate NUMERIC,amount NUMERIC,'
              'PRIMARY KEY(voucher_guid,line_no))')
            cur.execute('CREATE TEMP TABLE party_rate (party TEXT,item TEXT,last_rate NUMERIC,'
              'last_date TEXT,times INTEGER,item_median NUMERIC,PRIMARY KEY(party,item))')
            cur.execute('CREATE TEMP TABLE party_buy (party TEXT,item TEXT,cyc_lo INTEGER,'
              'cyc_hi INTEGER,last_days INTEGER,typical_qty NUMERIC,freq_n INTEGER,'
              'freq_m INTEGER,is_core BOOLEAN,PRIMARY KEY(party,item))')
            cx.commit()
            cur.execute('SET LOCAL search_path=pg_temp')
            cur.execute('SHOW transaction_read_only')
            assert cur.fetchone()==('on',)
            with patch.object(updates.shop,'customers',return_value=[{'name':'Test Party'}]):
                save(10,3); assert updates.publish(local,cur,log=lambda *a:None)==(1,1)
                save(11,8); assert updates.publish(local,cur,log=lambda *a:None)==(1,1)
                cur.execute('SELECT amount,source_alterid FROM history_voucher')
                assert cur.fetchone()==(672,11)
                cur.execute('SELECT qty,amount FROM history_line')
                assert cur.fetchone()==(8,672)
                assert updates.publish(local,cur,log=lambda *a:None)==(0,0)
                save(12,8,'Yes'); updates.publish(local,cur,log=lambda *a:None)
                cur.execute('SELECT source_state FROM history_voucher')
                assert cur.fetchone()==('cancelled',)
                cur.execute('SELECT COUNT(*) FROM history_line')
                assert cur.fetchone()==(0,)
            local.executemany('INSERT INTO party_item_rate VALUES(?,?,?,?,?,?)',[
                ('Sachin','Harpic Blue','20261030',80,2,84),
                ('Other shop','Harpic Blue','20261030',84,2,84)])
            assert cloud.push_party_rates(local,cur,log=lambda *a:None)==2
            assert cloud.push_party_rates(local,cur,log=lambda *a:None)==0
            local.execute("UPDATE party_item_rate SET last_rate=79 WHERE party='Sachin'")
            assert cloud.push_party_rates(local,cur,log=lambda *a:None)==1
            cur.execute('SELECT party,last_rate FROM party_rate ORDER BY party')
            assert cur.fetchall()==[('Other shop',84),('Sachin',79)]
            local.execute("INSERT INTO party_buy VALUES('Sachin','Harpic Blue',7,9,3,1,2,3,1)")
            assert cloud.push_recommendations(local,cur,log=lambda *a:None)==1
            assert cloud.push_recommendations(local,cur,log=lambda *a:None)==0
            local.execute('UPDATE party_buy SET last_days=4')
            assert cloud.push_recommendations(local,cur,log=lambda *a:None)==1
            cur.execute('SELECT last_days,freq_n,freq_m,is_core FROM party_buy')
            assert cur.fetchone()==(4,2,3,True)
        cx.rollback()
    local.close()
    print('PASS: real Postgres invoice edits, customer prices and recommendation updates in temporary tables; no permanent writes')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--temporary-tables',action='store_true',required=True)
    parser.parse_args()
    try:
        run()
    except Exception as exc:
        print('Temporary-table check failed:',type(exc).__name__)
        sys.exit(1)
