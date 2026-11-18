import json
import os
from concurrent.futures import Future
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import MagicMock, Mock, patch
from urllib.error import URLError

os.environ.setdefault('SESSION_SECRET', 'isolated-remote-sync-test')
from starlette.requests import Request

import api
import tally_agent
from desktop_agent import AgentSupervisor
from remote_sync import RemoteSync
from saarthi_runtime import atomic_json


class RemoteSyncTests(TestCase):
    def test_railway_uses_the_attached_volume_and_rejects_temporary_storage(self):
        with TemporaryDirectory() as folder, patch.dict(os.environ, {
            'RAILWAY_ENVIRONMENT': 'production', 'RAILWAY_VOLUME_MOUNT_PATH': folder,
            'SAARTHI_AGENT_SYNC_TOKEN': 'x' * 64, 'SAARTHI_AGENT_SYNC_DIR': '',
        }):
            self.assertEqual(api._agent_sync_config()[0], Path(folder) / 'agent-sync.json')
            with patch.dict(os.environ, {'SAARTHI_AGENT_SYNC_DIR': str(Path(folder).parent)}):
                with self.assertRaises(api.HTTPException) as rejected:
                    api._agent_sync_config()
                self.assertEqual(rejected.exception.status_code, 503)
            with patch.dict(os.environ, {'RAILWAY_VOLUME_MOUNT_PATH': ''}):
                with self.assertRaises(api.HTTPException) as rejected:
                    api._agent_sync_config()
                self.assertEqual(rejected.exception.status_code, 503)

    def test_only_owner_can_pause_and_agent_can_poll_without_postgres(self):
        token = 'x' * 64
        with TemporaryDirectory() as folder, patch.dict(os.environ, {
            'SAARTHI_AGENT_SYNC_TOKEN': token, 'SAARTHI_AGENT_SYNC_DIR': folder,
        }), patch.object(api, '_get_pool', side_effect=AssertionError('Unexpected PostgreSQL access')):
            request = Request({'type': 'http', 'headers': [(b'authorization', ('Bearer ' + token).encode())]})
            owner = {'role': 'admin'}
            rep = {'role': 'salesperson'}
            pause = api.AgentSyncChange(paused=True)
            with self.assertRaises(api.HTTPException) as rejected:
                api.set_agent_sync(pause, rep)
            self.assertEqual(rejected.exception.status_code, 403)
            with self.assertRaises(api.HTTPException) as rejected:
                api.agent_sync_status(rep)
            self.assertEqual(rejected.exception.status_code, 403)
            body = api.AgentSyncPoll(revision='', live_enabled=True)
            with self.assertRaises(api.HTTPException) as rejected:
                api.poll_agent_sync(body, Request({'type': 'http', 'headers': []}))
            self.assertEqual(rejected.exception.status_code, 401)
            self.assertFalse(api.poll_agent_sync(body, request)['paused'])
            self.assertEqual(api.set_agent_sync(pause, owner)['status'], 'Pausing')
            state = api.poll_agent_sync(body, request)
            self.assertTrue(state['paused'])
            api.poll_agent_sync(api.AgentSyncPoll(revision=state['revision'], live_enabled=True), request)
            self.assertEqual(api.agent_sync_status(owner)['status'], 'Paused')
            self.assertTrue(json.loads((Path(folder) / 'agent-sync.json').read_text())['paused'])
            self.assertFalse(api.set_agent_sync(api.AgentSyncChange(paused=False), owner)['paused'])

    def test_agent_waits_for_queued_cloud_work_and_fails_closed(self):
        agent = RemoteSync('https://fixture.example', 'a' * 64)
        task = Future()
        opener = Mock()
        agent.opener = opener
        opener.open.return_value = MagicMock()
        response = opener.open.return_value.__enter__.return_value
        response.read.return_value = b'{"paused":true,"revision":"new"}'
        agent.revision = 'old'
        agent.scheduler_paused = True
        agent.check({'push': task}, live_enabled=True)
        sent = json.loads(opener.open.call_args.args[0].data)
        self.assertEqual(sent['revision'], '')
        self.assertTrue(agent.paused)
        task.set_result('ok')
        agent.next_check = 0
        agent.check({'push': task}, live_enabled=True)
        self.assertEqual(json.loads(opener.open.call_args.args[0].data)['revision'], 'new')
        agent.paused = False
        opener.open.side_effect = URLError('unavailable')
        agent.next_check = 0
        agent.check({}, live_enabled=True)
        self.assertTrue(agent.paused)

    def test_website_pause_disables_work_and_desktop_start_resumes_same_agent(self):
        token = 'x' * 64
        owner = {'role': 'admin'}
        with TemporaryDirectory() as folder, patch.dict(os.environ, {
            'SAARTHI_AGENT_SYNC_TOKEN': token, 'SAARTHI_AGENT_SYNC_DIR': folder,
        }), patch.object(api, '_get_pool', side_effect=AssertionError('Unexpected PostgreSQL access')):
            root = Path(folder)
            atomic_json(root / 'saarthi-runtime.json', {'billing_enabled': True, 'agent_sync_token': token})
            script = root / 'tally_agent.py'
            script.touch()
            child = Mock()
            child.poll.return_value = None
            start = Mock(return_value=child)
            desktop = AgentSupervisor(root / 'tally_raw.db', python='fixture', spawn=start, inspect=lambda: [])
            self.assertEqual(desktop.ensure()['state'], 'started')
            remote = RemoteSync('https://fixture.example', token, root)
            request = Request({'type': 'http', 'headers': [(b'authorization', ('Bearer ' + token).encode())]})

            def reply(sent, timeout):
                state = api.poll_agent_sync(api.AgentSyncPoll(**json.loads(sent.data)), request)
                response = MagicMock()
                response.__enter__.return_value.read.return_value = json.dumps(state).encode()
                return response

            remote.opener = Mock()
            remote.opener.open.side_effect = reply

            def poll(tasks=None):
                remote.next_check = 0
                remote.check(tasks or {}, live_enabled=True)

            poll()
            api.set_agent_sync(api.AgentSyncChange(paused=True), owner)
            task = Future()
            poll({'stock': task})
            self.assertTrue(remote.scheduler_paused)
            self.assertEqual(desktop.status()['state'], 'started')
            self.assertEqual(api.agent_sync_status(owner)['status'], 'Pausing')
            task.set_result('ok')
            poll()
            self.assertEqual(api.agent_sync_status(owner)['status'], 'Paused')
            self.assertEqual(desktop.status()['state'], 'remote-paused')
            self.assertEqual(desktop.ensure(resume=True)['state'], 'remote-resuming')
            poll()
            poll({'stock': Future()})
            self.assertFalse(remote.scheduler_paused)
            self.assertEqual(api.agent_sync_status(owner)['status'], 'Running')
            self.assertEqual(desktop.status()['state'], 'started')
            start.assert_called_once()

    def test_paused_worker_waits_without_running_tally_jobs(self):
        conn = tally_agent.shop.open_db(':memory:')
        self.addCleanup(conn.close)
        now = tally_agent.dt.datetime(2026, 11, 14, 20)
        with patch.object(tally_agent, 'managed_job_allowed', return_value=True), \
             patch.object(tally_agent, 'agent_stop_requested', return_value=False), \
             patch.object(tally_agent, 'deletion_check_path', return_value=Mock(is_file=lambda:False)), \
             patch.object(tally_agent, 'run_job', return_value='ok') as run:
            remote = Mock(paused=True, scheduler_paused=True)
            self.assertEqual(tally_agent.tick(conn, now=now, remote=remote), [])
            self.assertEqual(tally_agent.run_background('unused', 'push', remote), 'skip')
            run.assert_not_called()
