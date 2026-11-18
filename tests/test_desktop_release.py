"""Release/update invariants. Only disposable DBs and fake executables/processes."""
import base64
import hashlib
import json
import os
from pathlib import Path
from contextlib import closing
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch,Mock
import zipfile
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
from desktop_release import FILES,canonical,verified,inspect_package,extract_package
from desktop_updater import apply_release,recover_interrupted,FileLock
from desktop_first_run import import_archive,probe_company,tally_url,write_cloud_url
from saarthi_runtime import atomic_json,managed_job_allowed
from test_desktop_archive import fixture
from desktop_archive import Archive
from desktop_workflows import Workflows

class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.base=Path(self.tmp.name)
        self.key=Ed25519PrivateKey.generate();self.public=base64.b64encode(self.key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)).decode()
        self.data=self.base/'data';self.data.mkdir();self.db=self.data/'tally_raw.db';fixture(self.db)
        with Workflows(Archive(self.db)).db() as conn:conn.execute("INSERT INTO product_image VALUES('p','image/png',X'010203','now')")
        self.profile=self.base/'profile';self.profile.mkdir();atomic_json(self.profile/'settings.json',{'database':str(self.db),'website_url':'https://example.test','custom_setting':'preserve'})
        self.root=self.base/'app';self.root.mkdir();self.lock=self.base/'shared.lock'
        self.addCleanup(patch.stopall);patch('desktop_updater.shared_lock',return_value=self.lock).start()
    def package(self,release='0.6.1',mutate=None):
        bodies={name:b'MZ'+name.encode()+release.encode() for name in FILES}
        manifest={'version':release,'protocol':1,'schema':1,'files':{name:{'bytes':len(body),'sha256':hashlib.sha256(body).hexdigest()} for name,body in bodies.items()}}
        raw=json.dumps({'manifest':manifest,'signature':base64.b64encode(self.key.sign(canonical(manifest))).decode()}).encode()
        path=self.base/(release+'.zip')
        with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('release.json',raw)
            for name,body in bodies.items():archive.writestr(name,body if not mutate else mutate(name,body))
        return path
    def apply(self,package,run=None,inspect=None):
        return apply_release(package,self.root,self.profile,self.db,public_key=self.public,reopen=False,inspect_agents=inspect or (lambda:[]),run=run or Mock())
    def test_rejects_wrong_publisher_tampering_and_zip_paths_before_database_changes(self):
        package=self.package();before=self.db.read_bytes()
        wrong=Ed25519PrivateKey.generate();public=base64.b64encode(wrong.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)).decode()
        with self.assertRaises(ValueError):inspect_package(package,public)
        with zipfile.ZipFile(package,'a') as archive:archive.writestr('../escape.exe',b'bad')
        with self.assertRaises(ValueError):self.apply(package)
        self.assertEqual(self.db.read_bytes(),before);self.assertFalse((self.data/'.saarthi-update-pause').exists())
    def test_extract_rejects_signed_file_checksum_mismatch(self):
        package=self.package(mutate=lambda name,body:b'X'+body[1:])
        with self.assertRaises(ValueError):extract_package(package,self.base/'stage',self.public)
        self.assertFalse((self.root/'current.json').exists())
    def test_successful_update_preserves_queue_images_settings_and_creates_verified_backups(self):
        before=(self.profile/'settings.json').read_bytes();result=self.apply(self.package())
        self.assertEqual(result['version'],'0.6.1');self.assertEqual(json.loads((self.root/'current.json').read_text())['version'],'0.6.1')
        with closing(sqlite3.connect(self.db)) as conn, conn:self.assertEqual(conn.execute("SELECT total FROM shop_order WHERE order_no='WEB-DEMO'").fetchone()[0],252)
        with closing(sqlite3.connect(self.data/'saarthi_admin.db')) as conn, conn:self.assertEqual(conn.execute('SELECT hex(body) FROM product_image').fetchone()[0],'010203')
        self.assertEqual((self.profile/'settings.json').read_bytes(),before)
        self.assertTrue((Path(result['backup'])/'tally_raw.db').exists());self.assertFalse((self.data/'.saarthi-update-pause').exists())
        with self.assertRaises(ValueError):self.apply(self.package('0.6.0'))
    def test_failed_migration_restores_both_databases_and_old_pointer(self):
        self.apply(self.package('0.6.0'))
        def fail(command,**kwargs):
            with closing(sqlite3.connect(self.db)) as conn, conn:conn.execute("UPDATE shop_order SET total=999999")
            with closing(sqlite3.connect(self.data/'saarthi_admin.db')) as conn, conn:conn.execute("DELETE FROM product_image")
            raise subprocess.CalledProcessError(1,command)
        with self.assertRaises(subprocess.CalledProcessError):self.apply(self.package(),run=fail)
        self.assertEqual(json.loads((self.root/'current.json').read_text())['version'],'0.6.0')
        with closing(sqlite3.connect(self.db)) as conn, conn:self.assertEqual(conn.execute('SELECT total FROM shop_order').fetchone()[0],252)
        with closing(sqlite3.connect(self.data/'saarthi_admin.db')) as conn, conn:self.assertEqual(conn.execute('SELECT COUNT(*) FROM product_image').fetchone()[0],1)
        self.assertFalse((self.data/'.saarthi-update-pause').exists())
    def test_legacy_agent_blocks_update_without_killing_or_mutating_anything(self):
        before=self.db.read_bytes()
        with self.assertRaisesRegex(ValueError,'legacy'):self.apply(self.package(),inspect=lambda:[{'pid':123,'script':'old/tally_agent.py'}])
        self.assertEqual(self.db.read_bytes(),before);self.assertFalse((self.data/'.saarthi-update-pause').exists())
    def test_interrupted_migration_stays_paused_and_recovers_from_its_backup(self):
        self.apply(self.package('0.6.0'))
        def crash(command,**kwargs):
            with closing(sqlite3.connect(self.db)) as conn, conn:conn.execute('UPDATE shop_order SET total=999999')
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):self.apply(self.package(),run=crash)
        self.assertTrue((self.data/'.saarthi-update-pause').exists())
        self.assertEqual(json.loads((self.profile/'update-recovery.json').read_text())['state'],'prepared')
        recover_interrupted(self.root,self.profile,self.db,inspect_agents=lambda:[])
        with closing(sqlite3.connect(self.db)) as conn, conn:self.assertEqual(conn.execute('SELECT total FROM shop_order').fetchone()[0],252)
        self.assertFalse((self.data/'.saarthi-update-pause').exists())
        with self.assertRaises(ValueError):recover_interrupted(self.root,self.profile,self.db,inspect_agents=lambda:[])
    def test_read_only_mode_never_schedules_cloud_or_tally_writes(self):
        with patch.dict(os.environ,{'SAARTHI_MANAGED':'1','SAARTHI_DATA_DIR':str(self.data)}):
            for job in ('invoice','admin','push','push_slow','retention'):self.assertFalse(managed_job_allowed(job))
            self.assertTrue(managed_job_allowed('vouchers'));atomic_json(self.data/'saarthi-runtime.json',{'billing_enabled':True})
            self.assertTrue(managed_job_allowed('invoice'));(self.data/'.saarthi-update-pause').touch()
            self.assertFalse(managed_job_allowed('invoice'));self.assertFalse(managed_job_allowed('vouchers'))
    def test_power_loss_during_preparation_unpauses_without_restoring_business_data(self):
        atomic_json(self.profile/'update-recovery.json',{'state':'preparing','database':str(self.db),'root':str(self.root)})
        (self.data/'.saarthi-update-pause').touch()
        before=self.db.read_bytes()
        result=recover_interrupted(self.root,self.profile,self.db,inspect_agents=lambda:[])
        self.assertTrue(result['recovered']);self.assertIsNone(result['backup'])
        self.assertEqual(self.db.read_bytes(),before);self.assertFalse((self.data/'.saarthi-update-pause').exists())
    def test_initial_import_requires_an_empty_or_explicitly_resumable_folder(self):
        run=Mock()
        with self.assertRaises(ValueError):import_archive(self.root,self.data,'Company','http://localhost:9000',run)
        run.assert_not_called()
        fresh=self.base/'fresh'
        def imported(command,**kwargs):
            self.assertIn('--bootstrap',command);self.assertIn('Company',command)
            fixture(fresh/'tally_raw.db')
            with closing(sqlite3.connect(fresh/'tally_raw.db')) as conn, conn:conn.execute("INSERT INTO sync_state VALUES('bootstrap_complete','1')")
        result=import_archive(self.root,fresh,'Company','http://localhost:9000',imported)
        self.assertTrue(result.exists());config=json.loads((fresh/'saarthi-runtime.json').read_text());self.assertFalse(config['billing_enabled']);self.assertFalse(config['setup_in_progress'])
    def test_tally_probe_and_cloud_settings_validation(self):
        with self.assertRaises(ValueError):tally_url('http://external.example:9000')
        with self.assertRaises(ValueError):write_cloud_url(self.data,'postgresql://secret\nINJECTED=1')
        (self.data/'.env').write_text('OTHER=keep\n')
        write_cloud_url(self.data,'postgresql://user:password@example.test/database')
        self.assertIn('OTHER=keep',(self.data/'.env').read_text())
        run=Mock(return_value=Mock(stdout='{"company":"Demo Books"}\n'))
        self.assertEqual(probe_company(self.root,self.data,'http://localhost:9000',run),'Demo Books')
        self.assertIn('--tally-url',run.call_args.args[0])
    def test_packaged_migration_uses_the_requested_folder_and_refuses_newer_schema(self):
        source=Path(__file__).resolve().parents[1]
        run=subprocess.run([sys.executable,str(source/'packaged_agent.py'),'--data-dir',str(self.data),'--migrate-only'],capture_output=True,timeout=30)
        self.assertEqual(run.returncode,0,run.stderr.decode())
        with closing(sqlite3.connect(self.db)) as conn, conn:self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0],1);conn.execute('PRAGMA user_version=2')
        run=subprocess.run([sys.executable,str(source/'packaged_agent.py'),'--data-dir',str(self.data),'--migrate-only'],capture_output=True,timeout=30)
        self.assertNotEqual(run.returncode,0)
        with closing(sqlite3.connect(self.db)) as conn, conn:self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0],2)

    def test_update_waits_for_the_agent_lock_before_migration(self):
        import threading
        package=self.package();entered=threading.Event();result=[];errors=[];run=Mock()
        def update():
            try:result.append(apply_release(package,self.root,self.profile,self.db,public_key=self.public,reopen=False,inspect_agents=lambda:[],run=run,progress=lambda message:entered.set()))
            except Exception as error:errors.append(error)
        with FileLock(self.lock):
            thread=threading.Thread(target=update);thread.start();self.assertTrue(entered.wait(3))
            run.assert_not_called();self.assertTrue((self.data/'.saarthi-update-pause').exists())
        thread.join(8);self.assertFalse(thread.is_alive());self.assertEqual(errors,[]);self.assertTrue(result[0]['updated'])

if __name__=='__main__':unittest.main()
