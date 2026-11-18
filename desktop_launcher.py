"""Stable Windows shortcut target: resolve the active verified version."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from desktop_release import verified
from desktop_updater import current_release,child_env,FLAGS

def launch(root,arguments=()):
    root=Path(root).resolve();current=current_release(root)
    if not current:raise ValueError('Install Saarthi using the complete setup first')
    target=root/current['directory'];manifest=verified((target/'release.json').read_bytes())
    exe=target/'SaarthiAdmin.exe'
    if manifest['version']!=current['version'] or hashlib.sha256(exe.read_bytes()).hexdigest()!=manifest['files']['SaarthiAdmin.exe']['sha256']:raise ValueError('Installed version failed verification. Run setup again.')
    return subprocess.Popen([str(exe),*arguments],cwd=str(root),env=child_env(),creationflags=FLAGS)

if __name__=='__main__':
    try:launch(Path(sys.executable).parent,sys.argv[1:])
    except Exception as error:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None,str(error),'Saarthi could not open',16)
