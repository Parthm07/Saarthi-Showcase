"""Release installation/update transactions. No Tally imports are executed here."""
from contextlib import closing,contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
from desktop_release import FILES,inspect_package,extract_package,verified,version,https_url,download,MAX_MANIFEST
from saarthi_runtime import VERSION,atomic_json,shared_lock,pause_path

FLAGS=getattr(subprocess,'CREATE_NO_WINDOW',0)

def child_env():return {**os.environ,'PYINSTALLER_RESET_ENVIRONMENT':'1'}

class FileLock:
    def __init__(self,path):self.path=Path(path);self.file=None
    def __enter__(self):
        self.path.parent.mkdir(parents=True,exist_ok=True);self.file=open(self.path,'a+b');self.file.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:self.file.close();self.file=None;raise
        return self
    def __exit__(self,*args):
        if self.file:self.file.close();self.file=None

@contextmanager
def wait_lock(path,seconds=120):
    deadline=time.monotonic()+seconds
    while True:
        lock=FileLock(path)
        try:lock.__enter__();break
        except OSError:
            if time.monotonic()>=deadline:raise ValueError('A sync/import is still running. Update cancelled safely; retry after it finishes.') from None
            time.sleep(.25)
    try:yield
    finally:lock.__exit__()

def snapshot(source,target):
    source=Path(source);target=Path(target)
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True,timeout=10)) as src,closing(sqlite3.connect(target)) as dst:
        src.backup(dst)
        if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Backup validation failed; update cancelled')

def restore(source,target):
    with closing(sqlite3.connect(source)) as src,closing(sqlite3.connect(target,timeout=10)) as dst:
        src.backup(dst)
        if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Rollback validation failed; keep agent stopped')

def current_release(root):
    root=Path(root).resolve();pointer=root/'current.json'
    if not pointer.exists():return None
    value=json.loads(pointer.read_text(encoding='utf-8'));version(value['version'])
    expected=root/'versions'/value['version']
    if value.get('directory')!='versions/'+value['version']:raise ValueError('Invalid installation pointer')
    if expected.resolve().parent!=(root/'versions').resolve():raise ValueError('Invalid version directory')
    return value

def installed_version(root):
    current=current_release(root)
    return current['version'] if current else '0.0.0'

def stage_release(package,root,public_key=None):
    root=Path(root).resolve();manifest=inspect_package(package,public_key)
    target=root/'versions'/manifest['version']
    if target.exists():
        existing=verified((target/'release.json').read_bytes(),public_key)
        if existing['files']!=manifest['files']:raise ValueError('A different package already uses this version number. Build a new version.')
        for name,meta in manifest['files'].items():
            if hashlib.sha256((target/name).read_bytes()).hexdigest()!=meta['sha256']:raise ValueError('Existing version files are damaged')
        return manifest,target
    temporary=root/'versions'/('.staging-'+uuid.uuid4().hex)
    extract_package(package,temporary,public_key)
    temporary.rename(target)
    return manifest,target

def run_checks(target,database,run=subprocess.run):
    for command in ([str(target/'SaarthiAgent.exe'),'--data-dir',str(database.parent),'--migrate-only'],[str(target/'SaarthiAdmin.exe'),'--db',str(database),'--self-check']):
        run(command,cwd=str(target),env=child_env(),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=90,check=True,creationflags=FLAGS)

def close_viewer(profile,database):
    path=Path(profile)/'instance.json'
    if not path.exists():return False
    info=json.loads(path.read_text(encoding='utf-8'))
    if Path(info['database']).resolve()!=database.resolve():raise ValueError('Close the other company archive before updating')
    parsed=urllib.parse.urlsplit(info['url'])
    if parsed.scheme!='http' or parsed.hostname!='127.0.0.1' or parsed.username or parsed.password:raise ValueError('Invalid local viewer address')
    request=urllib.request.Request(info['url']+'/api/exit',data=b'{}',headers={'Cookie':'saarthi_desktop='+info['token'],'Origin':info['url'],'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(request,timeout=5) as response:
            if response.status!=200:raise ValueError('Could not close the viewer safely')
        return True
    except urllib.error.URLError:
        # A stale instance file is harmless only if its instance lock is free.
        with FileLock(Path(profile)/'admin.lock'):pass
        return False

def apply_release(package,root,profile,database,*,inspect_agents=None,run=subprocess.run,reopen=True,public_key=None,progress=lambda message:None):
    root=Path(root).resolve();profile=Path(profile).resolve();database=Path(database).resolve()
    from desktop_archive import Archive
    Archive(database)
    if database.name!='tally_raw.db':raise ValueError('Update the active tally_raw.db, not a backup file')
    manifest=inspect_package(package,public_key);old=current_release(root)
    if old and version(manifest['version'])<=version(old['version']):raise ValueError('Choose a newer release; older code may be incompatible with current data')
    from desktop_agent import running_agents
    agents=(inspect_agents or running_agents)()
    if any(not a.get('managed_data') or Path(a['managed_data']).resolve()!=database.parent for a in agents):raise ValueError('Stop the legacy Python agent or other-company agent manually before this first managed update. No process was killed.')
    marker=pause_path(database.parent);closed=False;success=False;backed=[]
    original_settings=(profile/'settings.json').read_bytes() if (profile/'settings.json').exists() else None
    backup_dir=profile/'backups'/('before-update-'+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8])
    with FileLock(root/'update.lock'):
        if marker.exists():raise ValueError('An update/recovery is already pending. Use Recover previous version in the setup tool.')
        # Journal the non-mutating preparation phase before pausing workers.
        # If power is lost while waiting/backing up, recovery only unpauses:
        # migrations cannot start until the durable "prepared" record exists.
        atomic_json(profile/'update-recovery.json',{'state':'preparing','database':str(database),'root':str(root)})
        atomic_json(marker,{'version':manifest['version'],'started':time.time()})
        try:
            progress('Waiting for the current agent job to finish…')
            with wait_lock(shared_lock()),wait_lock(database.parent/'tally_agent.lock'):
                closed=close_viewer(profile,database)
                with wait_lock(profile/'admin.lock',15):
                    backup_dir.mkdir(parents=True,exist_ok=False)
                    for name in ('tally_raw.db','saarthi_admin.db'):
                        source=database.parent/name
                        if source.exists():snapshot(source,backup_dir/name);backed.append(name)
                    atomic_json(backup_dir/'recovery.json',{'database':str(database),'root':str(root),'profile':str(profile),'old_pointer':old,'files':backed,'settings_existed':original_settings is not None})
                    if original_settings is not None:(backup_dir/'settings.json').write_bytes(original_settings)
                    atomic_json(profile/'update-recovery.json',{'backup':str(backup_dir),'state':'prepared'})
                    progress('Backups verified. Installing and validating the new version…')
                    try:
                        manifest,target=stage_release(package,root,public_key)
                        run_checks(target,database,run)
                        atomic_json(root/'current.json',{'version':manifest['version'],'directory':'versions/'+manifest['version']})
                        atomic_json(profile/'update-recovery.json',{'backup':str(backup_dir),'state':'complete'})
                        success=True
                    except Exception:
                        progress('Update failed. Restoring the verified backups…')
                        for name in backed:restore(backup_dir/name,database.parent/name)
                        if 'saarthi_admin.db' not in backed:
                            # New auxiliary DB was created only by this failed migration.
                            # Keep it as a recovery artifact, never silently delete it.
                            aux=database.parent/'saarthi_admin.db'
                            if aux.exists():aux.rename(backup_dir/'failed-new-saarthi_admin.db')
                        if original_settings is not None:(profile/'settings.json').write_bytes(original_settings)
                        if old:atomic_json(root/'current.json',old)
                        atomic_json(profile/'update-recovery.json',{'backup':str(backup_dir),'state':'rolled-back'})
                        raise
        finally:
            recovery=profile/'update-recovery.json'
            state=json.loads(recovery.read_text()).get('state') if recovery.exists() else None
            # Interrupted migration/recovery must keep all managed agents paused.
            if success or state in ('preparing','rolled-back','complete') or not backed:marker.unlink(missing_ok=True)
            if reopen and closed and not marker.exists() and (root/'SaarthiLauncher.exe').exists():
                subprocess.Popen([str(root/'SaarthiLauncher.exe'),'--profile-dir',str(profile),'--no-agent'],env=child_env(),creationflags=FLAGS)
            if agents and not marker.exists():
                current=current_release(root)
                if current:
                    target=root/current['directory']/'SaarthiAgent.exe'
                    subprocess.Popen([str(target),'--data-dir',str(database.parent),'--quiet'],env=child_env(),cwd=str(database.parent),creationflags=FLAGS)
    return {'version':manifest['version'],'backup':str(backup_dir),'updated':True}

def recover_interrupted(root,profile,database,*,inspect_agents=None,progress=lambda message:None):
    root=Path(root).resolve();profile=Path(profile).resolve();database=Path(database).resolve()
    pointer=profile/'update-recovery.json'
    if not pointer.exists():raise ValueError('No interrupted migration to recover')
    pending=json.loads(pointer.read_text())
    if pending.get('state')=='preparing':
        if Path(pending.get('database','')).resolve()!=database or Path(pending.get('root','')).resolve()!=root:raise ValueError('Recovery does not match this installation/database')
        from desktop_agent import running_agents
        if (inspect_agents or running_agents)():raise ValueError('Wait for agents to stop before recovering')
        with FileLock(root/'update.lock'),wait_lock(shared_lock()),wait_lock(database.parent/'tally_agent.lock'):
            close_viewer(profile,database)
            with wait_lock(profile/'admin.lock',15):
                # No migration began, so business data must not be replaced.
                atomic_json(pointer,{**pending,'state':'cancelled-before-migration'})
                pause_path(database.parent).unlink(missing_ok=True)
        return {'recovered':True,'backup':None}
    if pending.get('state')!='prepared' or not pause_path(database.parent).exists():raise ValueError('Recovery is only for an interrupted update, not a completed release with newer business transactions')
    backup=Path(pending['backup']).resolve()
    if not backup.is_relative_to((profile/'backups').resolve()):raise ValueError('Invalid recovery backup location')
    info=json.loads((backup/'recovery.json').read_text())
    if Path(info['database']).resolve()!=database or Path(info['root']).resolve()!=root or set(info['files'])-{'tally_raw.db','saarthi_admin.db'}:raise ValueError('Recovery does not match this installation/database')
    from desktop_agent import running_agents
    if (inspect_agents or running_agents)():raise ValueError('Wait for agents to stop before recovering')
    with FileLock(root/'update.lock'),wait_lock(shared_lock()),wait_lock(database.parent/'tally_agent.lock'):
        close_viewer(profile,database)
        with wait_lock(profile/'admin.lock',15):
            progress('Restoring verified pre-update data and version pointer…')
            for name in info['files']:restore(backup/name,database.parent/name)
            aux=database.parent/'saarthi_admin.db'
            if 'saarthi_admin.db' not in info['files'] and aux.exists():aux.rename(backup/('failed-new-admin-'+uuid.uuid4().hex+'.db'))
            if info['settings_existed']:atomic_json(profile/'settings.json',json.loads((backup/'settings.json').read_text()))
            if info['old_pointer']:atomic_json(root/'current.json',info['old_pointer'])
            else:(root/'current.json').unlink(missing_ok=True)
            atomic_json(pointer,{'backup':str(backup),'state':'rolled-back'})
            pause_path(database.parent).unlink()
    return {'recovered':True,'backup':str(backup)}
