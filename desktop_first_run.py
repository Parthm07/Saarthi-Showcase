"""First-run helpers: initialise a Saarthi archive by reading Tally, never .900 files."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import urllib.parse
from desktop_updater import stage_release,apply_release,run_checks,child_env,FLAGS,current_release
from desktop_deployment import make_shortcut,install_root,profile_root
from saarthi_runtime import atomic_json,runtime_settings


def tally_url(value):
    parts=urllib.parse.urlsplit(value)
    if parts.scheme!='http' or parts.hostname not in ('localhost','127.0.0.1') or parts.username or parts.password or parts.query or parts.fragment or parts.path not in ('','/'):
        raise ValueError('Use the local Tally HTTP address, normally http://localhost:9000')
    try:parts.port
    except ValueError:raise ValueError('Invalid Tally port') from None
    return value.rstrip('/')

def validate_cloud_url(value):
    if value and (not isinstance(value,str) or any(ord(c)<32 for c in value) or urllib.parse.urlsplit(value).scheme not in ('postgres','postgresql')):raise ValueError('Enter the PostgreSQL connection URL without line breaks')
    return value

def write_cloud_url(directory,value):
    if not value:return
    validate_cloud_url(value)
    path=Path(directory)/'.env';lines=path.read_text(encoding='utf-8').splitlines() if path.exists() else []
    lines=[line for line in lines if line.partition('=')[0].strip()!='CLOUD_DB_URL']
    temporary=path.with_name('.env.pending');temporary.write_text('\n'.join(lines+['CLOUD_DB_URL='+value])+'\n',encoding='utf-8');os.replace(temporary,path)

def probe_company(target,directory,endpoint,run=subprocess.run):
    endpoint=tally_url(endpoint)
    # Probe endpoint goes through an environment variable, never modifies existing settings.
    env=child_env();env['TALLY_URL']=endpoint
    result=run([str(Path(target)/'SaarthiAgent.exe'),'--data-dir',str(Path(directory).resolve()),'--probe','--tally-url',endpoint],capture_output=True,text=True,check=True,timeout=45,creationflags=FLAGS,env=env)
    data=json.loads(result.stdout.strip().splitlines()[-1]);name=data.get('company')
    if not isinstance(name,str) or not name.strip():raise ValueError('Open the business company in Tally first')
    return name

def import_archive(target,directory,company,endpoint,run=subprocess.run):
    directory=Path(directory).resolve();directory.mkdir(parents=True,exist_ok=True)
    path=directory/'tally_raw.db';config=runtime_settings(directory)
    if path.exists() and not config.get('setup_in_progress'):raise ValueError('An archive already exists here. Select Existing archive to preserve its orders and history.')
    if config.get('setup_company') and config['setup_company']!=company:raise ValueError('Resume the import with the same Tally company')
    atomic_json(directory/'saarthi-runtime.json',{**config,'billing_enabled':False,'setup_in_progress':True,'setup_company':company,'tally_url':tally_url(endpoint)})
    log=directory/'initial-import.log'
    with log.open('ab') as output:
        run([str(Path(target)/'SaarthiAgent.exe'),'--data-dir',str(directory),'--bootstrap','--company',company],env=child_env(),stdin=subprocess.DEVNULL,stdout=output,stderr=output,check=True,timeout=7200,creationflags=FLAGS)
    from desktop_archive import Archive
    with Archive(path).read() as conn:
        row=conn.execute("SELECT value FROM sync_state WHERE key='bootstrap_complete'").fetchone()
        if not row or row[0]!='1':raise ValueError('Initial import did not finish; resume it before enabling live work')
    atomic_json(directory/'saarthi-runtime.json',{**runtime_settings(directory),'setup_in_progress':False})
    return path

def install_managed(package,database,*,root=None,profile=None,progress=lambda message:None,inspect_agents=None,run=subprocess.run,shortcut=make_shortcut):
    root=Path(root or install_root()).resolve();profile=Path(profile or profile_root()).resolve();database=Path(database).resolve()
    profile.mkdir(parents=True,exist_ok=True)
    result=apply_release(package,root,profile,database,progress=progress,reopen=False,inspect_agents=inspect_agents,run=run)
    current=current_release(root);target=root/current['directory']
    shutil.copy2(target/'SaarthiLauncher.exe',root/'SaarthiLauncher.exe')
    if not (root/'SaarthiUpdate.exe').exists():shutil.copy2(target/'SaarthiUpdate.exe',root/'SaarthiUpdate.exe')
    path=profile/'settings.json';settings=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    atomic_json(path,{**settings,'database':str(database),'managed_install':True})
    runtime=database.parent/'saarthi-runtime.json'
    if not runtime.exists():atomic_json(runtime,{'billing_enabled':False,'tally_url':'http://localhost:9000'})
    shortcut(root)
    return result
