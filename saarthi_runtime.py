"""Durable paths and control flags shared by packaged app, agent and updater."""
import json
import os
from pathlib import Path
import tempfile

VERSION='0.6.19'
PROTOCOL=1

def data_directory():
    return Path(os.environ.get('SAARTHI_DATA_DIR') or Path(__file__).resolve().parent).resolve()

def shared_lock():
    return Path(os.environ.get('LOCALAPPDATA') or Path.home())/'SaarthiAgent'/'connector.lock'

def atomic_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix=path.name+'.',suffix='.tmp',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            json.dump(value,stream,indent=2);stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)

def runtime_settings(directory=None):
    path=Path(directory or data_directory())/'saarthi-runtime.json'
    if not path.exists():return {'billing_enabled':False}
    value=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value,dict):raise ValueError('Invalid runtime settings')
    return value

def pause_path(directory=None):return Path(directory or data_directory())/'.saarthi-update-pause'

def agent_stop_path(directory=None):
    return Path(directory or data_directory())/'.saarthi-agent-stopped'

def remote_sync_path(directory=None):
    return Path(directory or data_directory()) / '.saarthi-remote-sync.json'

def remote_resume_path(directory=None):
    return Path(directory or data_directory()) / '.saarthi-remote-resume'

def remote_sync_revision(directory=None):
    try:
        return json.loads(remote_sync_path(directory).read_text(encoding='utf-8'))['revision']
    except FileNotFoundError:
        return ''

def agent_stop_requested():
    return agent_stop_path().exists() or (os.environ.get('SAARTHI_MANAGED')=='1' and pause_path().exists())

def deletion_check_path(directory=None):
    return Path(directory or data_directory()) / '.saarthi-check-deletions'

def managed_job_allowed(job):
    if agent_stop_requested():return False
    if os.environ.get('SAARTHI_MANAGED')!='1':return True
    return job in ('vouchers','masters','stock','rates','archive','reconcile','admin_backup') or runtime_settings().get('billing_enabled') is True
