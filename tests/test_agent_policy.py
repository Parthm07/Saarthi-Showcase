"""Agent scheduling checks; never start the agent or contact Tally/cloud."""
import datetime as dt
from contextlib import closing
from concurrent.futures import Future
import pathlib
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import tally_agent as agent
import tally_invoice as invoice
import tally_cloud as cloud


class InvoiceScheduleTests(unittest.TestCase):
    def test_stop_waits_for_current_job_and_skips_remaining_jobs(self):
        with closing(agent.shop.open_db(':memory:')) as conn:
            stopped = False

            def finish_job(connection, job):
                nonlocal stopped
                stopped = True
                return 'ok'

            with patch.object(agent, 'agent_stop_requested', side_effect=lambda: stopped), \
                 patch.object(agent, 'managed_job_allowed', side_effect=lambda job: not stopped), \
                 patch.object(agent, 'run_job', side_effect=finish_job) as run:
                self.assertEqual(agent.tick(conn), ['orders_pull'])
                run.assert_called_once_with(conn, 'orders_pull')

    def test_order_jobs_precede_the_catalogue_and_can_run_in_one_pass(self):
        jobs = list(agent.JOBS)
        self.assertEqual(jobs[:4], ['orders_pull', 'invoice', 'vouchers', 'orders_publish'])
        self.assertLessEqual(agent.JOBS['orders_pull'][0] + agent.TICK, 15)

    def test_invoice_runs_during_the_working_day(self):
        for hour in (0, 10, 11, 14, 16, 20):
            now = dt.datetime(2026, 10, 30, hour, 0)
            with self.subTest(hour=hour):
                self.assertTrue(agent.due("invoice", now-dt.timedelta(seconds=10), now))

    def test_invoice_waits_for_the_interval(self):
        now = dt.datetime(2026, 10, 30, 14, 0)
        self.assertFalse(agent.due("invoice", now-dt.timedelta(seconds=9), now))

    def test_maintenance_keeps_its_working_hours_gate(self):
        now = dt.datetime(2026, 10, 30, 14, 0)
        for job in ("archive", "reconcile"):
            self.assertTrue(agent.JOBS[job][1])
            self.assertFalse(agent.due(job, now-dt.timedelta(days=2), now))

    def test_new_tally_product_is_published_in_the_same_pass_as_its_stock(self):
        conn = agent.shop.open_db(':memory:')
        self.addCleanup(conn.close)
        now = dt.datetime.now().replace(microsecond=0)
        for job in agent.JOBS:
            agent._mark(conn, job, now)
        agent._mark(conn, 'masters', now - dt.timedelta(seconds=agent.JOBS['masters'][0]))
        events = []
        with patch.object(agent, 'managed_job_allowed', return_value=True), \
             patch.object(agent, 'deletion_check_path', return_value=Mock(is_file=lambda:False)), \
             patch.object(agent.s, 'sync_masters', side_effect=lambda *a, **k: events.append('masters') or 1), \
             patch.object(agent.s, 'snapshot_stock', side_effect=lambda *a, **k: events.append('stock') or 1), \
             patch.object(agent.s, 'snapshot_balances', return_value=1), \
             patch.object(agent.s, 'verify_company'), \
             patch.object(cloud, 'env', return_value='mock'), patch.object(cloud, 'connect'), \
             patch.object(cloud, 'push_fast', side_effect=lambda *a, **k: events.append('publish')):
            agent.s._set(conn, 'company', 'Test company')
            conn.commit()
            cloud.connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value.fetchone.return_value = None
            self.assertEqual(agent.tick(conn, now), ['masters', 'stock', 'push'])
        self.assertEqual(events, ['masters', 'stock', 'publish'])

    def test_new_orders_still_run_when_a_catalogue_upload_is_in_flight(self):
        now = dt.datetime(2026, 11, 13, 14)
        conn = agent.shop.open_db(':memory:')
        self.addCleanup(conn.close)
        for job in agent.JOBS:
            if job != 'orders_pull':
                agent._mark(conn, job, now)
        worker = Mock()
        tasks = {'push': Future()}
        def run(local, job):
            next_job = {'orders_pull': 'invoice', 'invoice': 'vouchers', 'vouchers': 'orders_publish'}.get(job)
            if next_job:
                agent._mark(local, next_job, now-dt.timedelta(seconds=agent.JOBS[next_job][0]))
            return 'ok'
        with patch.object(agent, 'run_job', side_effect=run), \
             patch.object(agent, 'managed_job_allowed', return_value=True), \
             patch.object(agent, 'deletion_check_path', return_value=Mock(is_file=lambda:False)):
            self.assertEqual(agent.tick(conn, now, worker=worker, tasks=tasks),
                             ['orders_pull', 'invoice', 'vouchers', 'orders_publish'])
        self.assertFalse(tasks['push'].done())
        worker.submit.assert_not_called()

    def test_failed_cloud_ack_replays_complete_local_order_once(self):
        conn = agent.shop.open_db(':memory:')
        self.addCleanup(conn.close)
        cur = Mock()
        header = ('WEB-TEST', dt.datetime(2026,11,13,14), 'rep@test', 'party',
                  'Test Party', 'Route', 'placed', 50, 0, 50, None, None, None)
        line = ('WEB-TEST', 1, 'product', 'Test item', 'PCS', 2, 25, 50)
        cur.fetchall.side_effect = [[header], [line], [header], [line]]
        def fail_ack(sql, *_):
            if sql.startswith('UPDATE cloud_order'):
                raise RuntimeError('lost acknowledgement')
        cur.execute.side_effect = fail_ack
        with self.assertRaisesRegex(RuntimeError, 'lost acknowledgement'):
            cloud.pull_orders(conn, cur, log=lambda _:None, include_visits=False)
        self.assertFalse(conn.in_transaction)
        cur.execute.side_effect = None
        cloud.pull_orders(conn, cur, log=lambda _:None, include_visits=False)
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM shop_order').fetchone()[0], 1)
        self.assertEqual(conn.execute('SELECT COUNT(*) FROM shop_order_line').fetchone()[0], 1)

    def test_future_invoice_date_survives_pull_and_reaches_tally_xml(self):
        conn = agent.shop.open_db(':memory:')
        self.addCleanup(conn.close)
        header = ('WEB-FUTURE', dt.datetime(2026,11,13,14), 'rep@test', 'party',
                  'Test Party', 'Route', 'placed', 50, 0, 50, None, None, None, '2026-11-15')
        line = ('WEB-FUTURE', 1, 'product', 'Test item', 'PCS', 2, 25, 50)
        cloud._store_pulled_orders(conn, [header], {'WEB-FUTURE':[line]})
        order = agent.shop.get_order(conn, 'WEB-FUTURE')
        self.assertTrue(order['created_at'].startswith('2026-11-13'))
        self.assertIn('<DATE>20261115</DATE>', invoice.build_xml(order, 'Test company'))

    def test_wrong_company_blocks_automatic_invoicing(self):
        conn = agent.shop.open_db(':memory:')
        try:
            with patch.object(invoice, 'waiting', return_value={'waiting':1,'quarantined':0}), \
                 patch.object(agent.s, 'verify_company', side_effect=agent.s.CompanyMismatch('wrong company')), \
                 patch.object(invoice, 'post_all') as post, self.assertLogs('tally_agent', level='ERROR'):
                self.assertEqual(agent.run_job(conn, 'invoice'), 'skip')
                post.assert_not_called()
        finally:
            conn.close()

    def test_company_check_precedes_automatic_invoice_write(self):
        conn = agent.shop.open_db(':memory:')
        events = []
        try:
            with patch.object(invoice, 'waiting', return_value={'waiting':1,'quarantined':0}), \
                 patch.object(agent.s, 'verify_company', side_effect=lambda *a,**k:events.append('verify')), \
                 patch.object(invoice, 'post_all', side_effect=lambda *a,**k:events.append('post') or {}), \
                 patch.object(cloud, 'env', return_value=None):
                self.assertEqual(agent.run_job(conn, 'invoice'), 'ok')
                self.assertEqual(events, ['verify','post'])
        finally:
            conn.close()

    def test_stuck_tally_keeps_backoff_instead_of_attempting_every_two_minutes(self):
        conn = agent.shop.open_db(':memory:')
        agent.s._set(conn, 'invoice_blocked_since', dt.datetime.now().isoformat())
        try:
            with patch.object(invoice, 'waiting', return_value={'waiting':1,'quarantined':0,'oldest_mins':5}), \
                 patch.object(agent.s, 'verify_company') as verify, \
                 patch.object(invoice, 'post_all') as post, self.assertLogs('tally_agent', level='WARNING'):
                self.assertEqual(agent.run_job(conn, 'invoice'), 'ok')
                verify.assert_not_called()
                post.assert_not_called()
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
