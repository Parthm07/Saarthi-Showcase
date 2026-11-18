"""Agent lifecycle checks with fake processes. Never start a real worker."""
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock,patch
from desktop_agent import AgentSupervisor,running_agents
from saarthi_runtime import agent_stop_path, managed_job_allowed
import json


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.script=self.root/'tally_agent.py';self.script.write_text('# fixture')
        self.process=Mock(pid=100);self.process.poll.return_value=None
        self.spawn=Mock(return_value=self.process);self.inspect=Mock(return_value=[])
        self.agent=AgentSupervisor(self.root/'tally_raw.db',python='fixture-python',inspect=self.inspect,spawn=self.spawn)

    def test_repeated_start_only_launches_once(self):
        self.assertEqual(self.agent.ensure()['state'],'started')
        self.assertEqual(self.agent.ensure()['state'],'started')
        self.spawn.assert_called_once_with('fixture-python',self.script.resolve())

    def test_attach_existing_without_second_worker(self):
        self.inspect.return_value=[dict(pid=99,script=str(self.script))]
        self.assertEqual(self.agent.ensure()['state'],'running');self.spawn.assert_not_called()

    def test_other_folder_or_ambiguous_relative_agent_blocks_start(self):
        for script in (str(self.root/'old'/'tally_agent.py'),'tally_agent.py'):
            self.inspect.return_value=[dict(pid=99,script=script)]
            self.assertEqual(self.agent.ensure()['state'],'conflict')
        self.spawn.assert_not_called()

    def test_backup_archive_cannot_start_worker(self):
        self.agent.database=self.root/'backup.db'
        self.assertEqual(self.agent.ensure()['state'],'error');self.spawn.assert_not_called()

    def test_missing_interpreter_or_failed_launch_is_reported(self):
        self.agent.python='';self.assertEqual(self.agent.ensure()['state'],'error')
        self.spawn.assert_not_called()
        self.agent.python='fixture';self.spawn.side_effect=OSError('fixture failure')
        self.assertEqual(self.agent.ensure()['state'],'error')

    def test_status_does_not_wait_for_windows_probe(self):
        entered=threading.Event();release=threading.Event()
        def inspect():entered.set();release.wait(3);return []
        self.agent.inspect=inspect
        thread=threading.Thread(target=self.agent.ensure);thread.start()
        try:
            self.assertTrue(entered.wait(2));self.assertEqual(self.agent.status()['state'],'checking')
            with self.assertRaises(ValueError):self.agent.configure(python='new')
        finally:release.set();thread.join(3)

    def test_exited_child_can_be_restarted(self):
        self.agent.ensure();self.process.poll.return_value=1
        self.assertEqual(self.agent.status()['state'],'stopped')
        self.agent.ensure();self.assertEqual(self.spawn.call_count,2)

    def test_manual_stop_survives_reopening_and_explicit_start_resumes(self):
        self.agent.ensure()
        with patch('desktop_agent.threading.Thread'):
            self.agent.stop_async()
        self.assertEqual(self.agent.status()['state'],'stopping')
        self.process.terminate.assert_not_called()
        self.process.poll.return_value=0
        self.agent.wait_for_stop()
        self.assertEqual(self.agent.status()['state'],'stopped')
        reopened=AgentSupervisor(self.agent.database,python='fixture-python',inspect=self.inspect,spawn=self.spawn)
        self.assertEqual(reopened.ensure()['state'],'stopped')
        self.assertEqual(self.spawn.call_count,1)
        self.process.poll.return_value=None
        self.assertEqual(reopened.ensure(resume=True)['state'],'started')
        self.assertFalse(agent_stop_path(self.root).exists())
        self.assertEqual(self.spawn.call_count,2)

    def test_manual_stop_blocks_sync_in_both_worker_modes(self):
        agent_stop_path(self.root).touch()
        for mode in ('0','1'):
            with patch.dict('os.environ',{'SAARTHI_DATA_DIR':str(self.root),'SAARTHI_MANAGED':mode}):
                self.assertFalse(managed_job_allowed('vouchers'))
                self.assertFalse(managed_job_allowed('orders_pull'))

    def test_cannot_change_running_worker_paths(self):
        self.agent.ensure()
        with self.assertRaises(ValueError):self.agent.configure(python='other')

    def test_inventory_ignores_agent_path_given_to_viewer(self):
        rows=[dict(ProcessId=1,CommandLine='python.exe "D:\\code\\desktop_app.py" --agent-script "D:\\code\\tally_agent.py"'),
              dict(ProcessId=2,CommandLine='python.exe "D:\\code\\tally_agent.py" --quiet')]
        with patch('desktop_agent.subprocess.run',return_value=Mock(stdout=json.dumps(rows))):
            self.assertEqual(running_agents(),[dict(pid=2,script='D:\\code\\tally_agent.py')])
    def test_inventory_ignores_setup_probe_but_detects_live_worker(self):
        exe = r'D:\Saarthi\SaarthiAgent.exe'
        worker = f'"{exe}" --data-dir "D:\\Books" --quiet'
        probe = f'"{exe}" --data-dir "D:\\Books" --probe'
        rows = [
            dict(ProcessId=10, ParentProcessId=1, CommandLine=probe),
            dict(ProcessId=11, ParentProcessId=10, CommandLine=probe),
            dict(ProcessId=20, ParentProcessId=1, CommandLine=worker),
        ]
        with patch('desktop_agent.subprocess.run', return_value=Mock(stdout=json.dumps(rows))):
            self.assertEqual(running_agents(), [
                dict(pid=20, script=exe, managed_data=r'D:\Books'),
            ])

    def test_inventory_deduplicates_packaged_bootloader_parent_and_child(self):
        rows=[dict(ProcessId=10,ParentProcessId=1,CommandLine='"D:\\Saarthi\\SaarthiAgent.exe" --data-dir "D:\\Books" --quiet'),dict(ProcessId=11,ParentProcessId=10,CommandLine='"D:\\Saarthi\\SaarthiAgent.exe" --data-dir "D:\\Books" --quiet')]
        with patch('desktop_agent.subprocess.run',return_value=Mock(stdout=json.dumps(rows))):
            self.assertEqual(running_agents(),[dict(pid=11,script='D:\\Saarthi\\SaarthiAgent.exe',managed_data='D:\\Books')])
