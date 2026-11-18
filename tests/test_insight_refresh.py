"""Reorder facts age with time, and frequency counts invoices, not dates."""
import datetime as dt
import pathlib
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import tally_agent as agent
import tally_insights as insights
import tally_shop as shop
import tally_store as store


class InsightRefreshTests(unittest.TestCase):
    def test_scheduled_rebuild_runs_even_without_new_vouchers(self):
        local = shop.open_db(':memory:')
        try:
            store._set(local,'voucher_alterid','10')
            store._set(local,'rates_built_at_alterid','10')
            with patch.object(shop,'rebuild_party_rates',return_value=1) as rates, \
                 patch.object(insights,'rebuild') as rebuild:
                self.assertEqual(agent.run_job(local,'rates'),'ok')
            rates.assert_called_once();rebuild.assert_called_once()
            self.assertEqual(agent.JOBS['rates'][0],900)
        finally:
            local.close()

    def test_frequency_counts_separate_same_day_bills_and_recency_ages(self):
        local = shop.open_db(':memory:')
        try:
            for guid,date,items in [('a','20261020',['Harpic']),('b','20261020',['Soap']),
                                    ('c','20261027',['Harpic'])]:
                store.save(local,[dict(GUID=guid,ALTERID=str(ord(guid)),DATE=date,
                    VOUCHERTYPENAME='Sales',PARTYLEDGERNAME='Test Party',AMOUNT='-80',
                    ISCANCELLED='No',ISOPTIONAL='No',
                    **{'ALLINVENTORYENTRIES.LIST':[dict(STOCKITEMNAME=item,
                        BILLEDQTY='1 PCS',RATE='80/PCS') for item in items]})])
            with patch.object(shop,'customers',return_value=[{'name':'Test Party'}]), \
                 patch.object(insights,'_today',return_value=dt.date(2026,10,30)):
                insights.rebuild(local,log=lambda *a:None)
            row = local.execute("SELECT freq_n,freq_m,last_days,cyc_lo FROM party_buy WHERE item='Harpic'").fetchone()
            self.assertEqual(tuple(row),(2,3,3,7))
            with patch.object(shop,'customers',return_value=[{'name':'Test Party'}]), \
                 patch.object(insights,'_today',return_value=dt.date(2026,10,31)):
                insights.rebuild(local,log=lambda *a:None)
            self.assertEqual(local.execute("SELECT last_days FROM party_buy WHERE item='Harpic'").fetchone()[0],4)
            self.assertIsNone(local.execute("SELECT cyc_lo FROM party_buy WHERE item='Soap'").fetchone()[0])
        finally:
            local.close()


if __name__=='__main__':
    unittest.main()
