"""Start/attach one existing laptop agent; never kill another process."""
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from saarthi_runtime import agent_stop_path, atomic_json, pause_path, remote_resume_path, remote_sync_revision, runtime_settings

FLAGS=getattr(subprocess,'CREATE_NO_WINDOW',0)


def python_executable():
    if not getattr(sys,'frozen',False):return sys.executable
    local=Path(os.environ.get('LOCALAPPDATA',''))
    options=[local/'Python/bin/pythonw.exe',local/'Python/bin/python.exe']
    for name in ('pythonw','python','pyw','py'):
        found=shutil.which(name)
        if found and 'windowsapps' not in found.lower():options.append(Path(found))
    return next((str(p) for p in options if p.is_file()),'')


def running_agents():
    if os.name!='nt':raise ValueError('Automatic agent startup requires Windows')
    command="Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe' OR Name='py.exe' OR Name='pyw.exe' OR Name='SaarthiAgent.exe'\" | Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress"
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',command],
        capture_output=True,text=True,timeout=12,creationflags=FLAGS,check=True)
    rows=json.loads(result.stdout) if result.stdout.strip() else []
    if isinstance(rows,dict):rows=[rows]
    found=[]
    for row in rows:
        command=row.get('CommandLine')
        if not command:raise ValueError('Cannot verify all Python processes; no second agent was started')
        args=[a or b for a,b in re.findall(r'"([^"]*)"|(\S+)',command)]
        if Path(args[0]).name.lower()=='saarthiagent.exe':
            # The read-only setup probe runs in the same executable as the worker.
            if '--probe' in args[1:]:continue
            if '--data-dir' not in args or args.index('--data-dir')+1>=len(args):raise ValueError('Cannot verify packaged agent data path')
            found.append({'pid':row['ProcessId'],'script':args[0],'managed_data':args[args.index('--data-dir')+1],'_parent':row.get('ParentProcessId')})
            continue
        # Inspect the program Python runs, not an agent path passed as an
        # option to desktop_app.py (or text inside a python -c command).
        if '-c' in args[1:]:continue
        if '-m' in args[1:]:
            index=args.index('-m')
            if len(args)>index+1 and args[index+1]=='tally_agent':
                found.append({'pid':row['ProcessId'],'script':'tally_agent.py'})
            continue
        script=next((a for a in args[1:] if a.lower().endswith('.py')),None)
        if script and Path(script).name.lower()=='tally_agent.py':
            found.append({'pid':row['ProcessId'],'script':script})
    bootloader_parents={r.get('_parent') for r in found if r.get('managed_data')}
    return [{k:v for k,v in row.items() if k!='_parent'} for row in found if not (row.get('managed_data') and row['pid'] in bootloader_parents)]


def launch(python,script):
    subprocess.run([python,'-c','import psycopg, sqlite3'],cwd=str(script.parent),check=True,
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,stdin=subprocess.DEVNULL,timeout=12,creationflags=FLAGS)
    return subprocess.Popen([python,str(script),'--quiet'],cwd=str(script.parent),
        stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
        creationflags=FLAGS| getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0),close_fds=True)


class AgentSupervisor:
    def __init__(self,database,script='',python='',inspect=None,spawn=None):
        self.database=Path(database).resolve()
        self.script=Path(script).expanduser().resolve() if script else self.database.parent/'tally_agent.py'
        self.python=python or python_executable()
        candidate=Path(sys.executable).parent/'SaarthiAgent.exe'
        self.managed_exe=candidate if getattr(sys,'frozen',False) and candidate.is_file() else None
        self.inspect=inspect or running_agents;self.spawn=spawn or launch
        self._lock=threading.Lock();self._child=None
        self._status={'state':'not-started','message':'Agent has not been checked','pid':None}

    def stopped_status(self):
        self._child=None
        self._status={'state':'stopped','message':'Agent stopped. Select Start agent to resume sync.','pid':None}

    def matching_agents(self):
        return [row for row in self.inspect() if (
            Path(row['managed_data']).resolve()==self.database.parent if row.get('managed_data')
            else Path(row['script']).is_absolute() and Path(row['script']).resolve()==self.script
        )]

    def status(self):
        # Status stays immediate while the background launch probes Windows.
        if self._child is not None and self._child.poll() is not None and not agent_stop_path(self.database.parent).exists():
            self._status={'state':'stopped','message':'Agent exited. Check its log before retrying.','pid':None}
        runtime = runtime_settings(self.database.parent)
        status = dict(self._status)
        if status['state'] in ('started', 'running') and runtime.get('agent_sync_token') and remote_sync_revision(self.database.parent):
            resuming = remote_resume_path(self.database.parent).is_file()
            status.update(state='remote-resuming' if resuming else 'remote-paused',
                          message='Resuming laptop sync…' if resuming else 'Paused from the website. Select Start agent to resume syncing.')
        return {**status,'script':str(self.script),'python':self.python,'managed':self.managed_exe is not None,'billing_enabled':runtime.get('billing_enabled',False)}

    def ensure(self, *, resume=False):
        with self._lock:
            marker=agent_stop_path(self.database.parent)
            was_stopped=marker.exists()
            try:
                if resume and not was_stopped and self.status()['state'] == 'remote-paused':
                    revision = remote_sync_revision(self.database.parent)
                    if revision:
                        atomic_json(remote_resume_path(self.database.parent), {'revision': revision})
                    return self.status()
                if was_stopped and not resume:
                    if self.matching_agents() or (self._child is not None and self._child.poll() is None):
                        self._status={'state':'stop-pending','message':'Stop requested. Select Check stop to verify the agent has exited.','pid':None}
                    else:
                        self.stopped_status()
                    return dict(self._status)
                if was_stopped and (self._child is not None and self._child.poll() is None):
                    raise ValueError('The agent is still finishing its current work. Select Check stop before starting.')
                if self._child is not None and self._child.poll() is None:
                    return dict(self._status)
                self._status={'state':'checking','message':'Checking for an existing Tally agent…','pid':None}
                if pause_path(self.database.parent).exists():raise ValueError('Update/recovery in progress; agent remains paused')
                if self.managed_exe is None and (self.script.parent/'tally_raw.db').resolve()!=self.database:
                    raise ValueError('The agent must use this same active tally_raw.db. A backup archive cannot start a worker.')
                agents=self.inspect()
                if agents:
                    if was_stopped:
                        self._status={'state':'stop-pending','message':'A Tally agent is still running. Select Check stop before starting.','pid':None}
                        return dict(self._status)
                    if self.managed_exe is not None and len(agents)==1 and agents[0].get('managed_data') and Path(agents[0]['managed_data']).resolve()==self.database.parent:
                        self._status={'state':'running','message':'Attached to the packaged agent','pid':agents[0]['pid']};return dict(self._status)
                    if len(agents)==1 and Path(agents[0]['script']).is_absolute() and Path(agents[0]['script']).resolve()==self.script:
                        self._status={'state':'running','message':'Attached to the existing Tally agent','pid':agents[0]['pid']}
                    else:
                        self._status={'state':'conflict','message':'Another Tally agent or source folder is already running. No second worker was started.','pid':None}
                    return dict(self._status)
                if self.managed_exe is not None:
                    from desktop_updater import child_env
                    marker.unlink(missing_ok=True)
                    self._child=subprocess.Popen([str(self.managed_exe),'--data-dir',str(self.database.parent),'--quiet'],cwd=str(self.database.parent),env=child_env(),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=FLAGS| getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0))
                    self._status={'state':'started','message':'Packaged agent started; see live-mode setting before enabling invoicing','pid':self._child.pid};return dict(self._status)
                if not self.script.is_file() or self.script.name.lower()!='tally_agent.py':raise ValueError('Agent script not found next to this archive. Configure its source path.')
                if not self.python:raise ValueError('Choose the installed Python interpreter used by your Tally agent')
                marker.unlink(missing_ok=True)
                self._child=self.spawn(self.python,self.script)
                self._status={'state':'started','message':'Tally agent started; its normal sync schedule is running','pid':self._child.pid}
            except Exception as exc:
                if was_stopped and not marker.exists():
                    atomic_json(marker, {'requested_at':time.time()})
                message=str(exc) if isinstance(exc,ValueError) else 'Could not start/check the agent. Verify Python, psycopg and the agent path.'
                self._status={'state':'error','message':message,'pid':None}
            return dict(self._status)

    def start_async(self, *, resume=False):
        # The controller lock covers inventory + launch so repeated clicks serialize.
        thread=threading.Thread(target=self.ensure,kwargs={'resume':resume},daemon=True);thread.start()
        return {'queued':True}

    def stop_async(self):
        with self._lock:
            marker=agent_stop_path(self.database.parent)
            if self._status['state']=='stopping':
                return {'queued':True}
            atomic_json(marker, {'requested_at':time.time()})
            self._status={'state':'stopping','message':'Stopping after the current work finishes…','pid':self._status['pid']}
        threading.Thread(target=self.wait_for_stop,daemon=True).start()
        return {'queued':True}

    def wait_for_stop(self):
        marker=agent_stop_path(self.database.parent)
        deadline=time.monotonic()+120
        try:
            while marker.exists():
                with self._lock:
                    if not marker.exists():return
                    if not self.matching_agents() and (self._child is None or self._child.poll() is not None):
                        self.stopped_status()
                        return
                    if time.monotonic()>=deadline:
                        self._status={'state':'stop-pending','message':'Still waiting for the agent to exit. Select Check stop to check again.','pid':self._status['pid']}
                        return
                time.sleep(2)
        except Exception:
            with self._lock:
                if marker.exists():
                    self._status={'state':'stop-pending','message':'Stop requested. Windows could not confirm exit. Select Check stop to check again.','pid':self._status['pid']}

    def configure(self,script='',python=''):
        if not self._lock.acquire(blocking=False):raise ValueError('Wait until the agent check finishes before changing paths')
        try:
            if self._status['state'] in ('running','started'):
                raise ValueError('Agent is already running. Stop it before changing its paths.')
            if script:self.script=Path(script).expanduser().resolve()
            if python:self.python=python
            return {'agent_script':str(self.script),'agent_python':self.python}
        finally:self._lock.release()
