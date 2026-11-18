"""Business-laptop installation and trusted-feed helpers."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from desktop_release import verified,https_url,download,MAX_MANIFEST,inspect_package,version
from desktop_updater import current_release,installed_version,child_env,FLAGS
from saarthi_runtime import VERSION,atomic_json

UPDATE_CHECK_SECONDS = 15 * 60


def install_root():return Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'Programs'/'Saarthi Admin'

def profile_root():return Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'SaarthiAdmin'

def make_shortcut(root):
    target=Path(root)/'SaarthiLauncher.exe'
    start=Path(os.environ['APPDATA'])/'Microsoft/Windows/Start Menu/Programs';start.mkdir(parents=True,exist_ok=True)
    q=lambda value:"'"+str(value).replace("'","''")+"'"
    script="$link=(New-Object -ComObject WScript.Shell).CreateShortcut("+q(start/'Saarthi Admin.lnk')+");$link.TargetPath="+q(target)+";$link.WorkingDirectory="+q(root)+";$link.Save()"
    script+=";$recovery=(New-Object -ComObject WScript.Shell).CreateShortcut("+q(start/'Saarthi Recovery.lnk')+");$recovery.TargetPath="+q(Path(root)/'SaarthiUpdate.exe')+";$recovery.Arguments='--recover';$recovery.WorkingDirectory="+q(root)+";$recovery.Save()"
    subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],capture_output=True,check=True,creationflags=FLAGS)

def fetch_feed(url,directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    path=directory/('feed-'+str(time.time_ns())+'.json')
    try:download(https_url(url),path,MAX_MANIFEST);return verified(path.read_bytes())
    finally:path.unlink(missing_ok=True)

def fetch_package(feed,directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    expected=feed.get('package_bytes')
    from desktop_release import MAX_PACKAGE
    if type(expected)!=int or not 0<expected<=MAX_PACKAGE:raise ValueError('Feed has no valid package size')
    path=directory/('Saarthi-'+feed['version']+'-'+str(time.time_ns())+'.zip')
    try:
        download(https_url(feed['package_url']),path,expected)
        if path.stat().st_size!=expected or hashlib.sha256(path.read_bytes()).hexdigest()!=feed.get('package_sha256'):raise ValueError('Downloaded package failed verification')
        inner=inspect_package(path)
        if inner['version']!=feed['version'] or inner['files']!=feed['files']:raise ValueError('Feed and package do not match')
        return path
    except Exception:path.unlink(missing_ok=True);raise

class Updates:
    def __init__(self,archive,profile=None,root=None,settings=None):
        self.archive=archive;self.profile=Path(profile or profile_root());self.root=Path(root or (Path(sys.executable).parent.parent.parent if getattr(sys,'frozen',False) and Path(sys.executable).parent.parent.name=='versions' else install_root()));self.settings=settings or (lambda:{})
        self._status={'state':'idle','message':'Choose a signed local release ZIP or configure your publisher HTTPS feed.'}
        self._lock=threading.Lock();self._last=0;self._checked_feed=''
        self._tool_lock=threading.Lock();self._tool=None
    def status(self):
        feed=self.settings().get('update_feed','')
        if feed and (feed!=self._checked_feed or time.time()-self._last>=UPDATE_CHECK_SECONDS):self.check()
        state=self._status
        if not feed:state={'state':'idle','message':'Choose a signed local release ZIP or configure your publisher HTTPS feed.'}
        elif feed!=self._checked_feed:state={'state':'checking','message':'Waiting to check the new feed…'}
        recovery=self.profile/'update-recovery.json'
        pending=json.loads(recovery.read_text()).get('state')=='prepared' if recovery.exists() else False
        return {**state,'version':VERSION,'installed_version':installed_version(self.root),'managed_install':current_release(self.root) is not None,'feed':feed,'recovery_pending':pending}
    def check(self):
        if not self._lock.acquire(blocking=False):return {'checking':True}
        self._status={'state':'checking','message':'Checking publisher signature…'}
        feed=self._checked_feed=self.settings().get('update_feed','')
        self._last=time.time()
        def work():
            try:
                if not feed:raise ValueError('Set the HTTPS publisher feed first, or install a downloaded signed ZIP.')
                manifest=fetch_feed(feed,self.profile/'update-cache')
                newer=version(manifest['version'])>version(VERSION)
                self._status={'state':'available' if newer else 'current','message':'Version '+manifest['version']+(' is available.' if newer else ' is not newer than this app.'),'available_version':manifest['version']}
            except Exception:self._status={'state':'error','message':'Update check failed. Verify the HTTPS feed, connection and publisher signing key; the installed version is unchanged.'}
            finally:self._lock.release()
        threading.Thread(target=work,daemon=True).start();return {'checking':True}
    def open_tool(self,online=False,recover=False):
        exe=Path(sys.executable).parent/'SaarthiUpdate.exe'
        if not getattr(sys,'frozen',False) or not exe.is_file():raise ValueError('Use the complete managed setup to enable one-click updates. Source-mode checks do not install executables.')
        args=[str(exe),'--root',str(self.root),'--profile',str(self.profile),'--database',str(self.archive.path)]
        if recover:args+=['--recover']
        elif online:args+=['--feed',https_url(self.settings().get('update_feed','')),'--apply-online']
        else:args+=['--choose-package']
        with self._tool_lock:
            if self._tool is not None and self._tool.poll() is None:
                raise ValueError('The Saarthi update window is already open.')
            self._tool=subprocess.Popen(args,env=child_env(),creationflags=FLAGS)
        return {'opened':True}
