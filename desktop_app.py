"""Saarthi Windows admin: offline local archive viewer in an application window.

python desktop_app.py --db tally_raw.db
python desktop_app.py --db tally_raw.db --serve --port 8810
"""
import argparse
import hmac
import json
import mimetypes
import os
import secrets
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from desktop_archive import Archive
from desktop_analysis import Analysis
from desktop_management import Management,inventory,save_policy,rate_preview,save_visibility,save_our_rates
from desktop_deployment import Updates
from saarthi_runtime import VERSION,atomic_json,runtime_settings,pause_path,deletion_check_path
from desktop_agent import AgentSupervisor
from desktop_website import WebsiteAccess,website_url
from desktop_workflows import Workflows

ASSETS=Path(__file__).resolve().parent/'desktop'


def user_directory():
    return Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'.local/share')))/'SaarthiAdmin'


def launch_window(url):
    for root,relative in [('PROGRAMFILES(X86)','Microsoft/Edge/Application/msedge.exe'),
                          ('PROGRAMFILES','Microsoft/Edge/Application/msedge.exe'),
                          ('PROGRAMFILES','Google/Chrome/Application/chrome.exe')]:
        exe=Path(os.environ.get(root,''))/relative
        if exe.is_file():
            subprocess.Popen([str(exe),'--app='+url,'--window-size=1440,950'],
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            return
    webbrowser.open(url)


def make_server(archive,port=0,backup_dir=None,token=None,agent=None,website=None,save_settings=None,profile_dir=None,settings=None):
    token=token or secrets.token_urlsafe(32)
    website=website or WebsiteAccess()
    work=Workflows(archive)
    analysis=Analysis(archive)
    management=Management(archive,analysis)
    updates=Updates(archive,profile=profile_dir,settings=lambda:settings or {})
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):
            pass # Never put session tokens or customer query text into access logs.

        def respond(self,status,body,ctype='application/json; charset=utf-8',headers=None):
            if not isinstance(body,bytes):body=json.dumps(body,ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type',ctype)
            self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Referrer-Policy','no-referrer')
            self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'; img-src 'self' data:; connect-src 'self' https:")
            for k,v in (headers or {}).items():self.send_header(k,v)
            self.end_headers();self.wfile.write(body)

        def authorised(self):
            expected=f'127.0.0.1:{self.server.server_port}'
            if self.headers.get('Host')!=expected:return False
            try:
                cookies=SimpleCookie(self.headers.get('Cookie',''))
                supplied=cookies['saarthi_desktop'].value if 'saarthi_desktop' in cookies else ''
                return hmac.compare_digest(supplied,token)
            except Exception:return False

        def do_GET(self):
            parts=urllib.parse.urlsplit(self.path)
            if self.headers.get('Host')!=f'127.0.0.1:{self.server.server_port}':
                return self.respond(403,{'error':'Local application requests only'})
            if parts.path=='/desktop/callback':
                params=urllib.parse.parse_qs(parts.query)
                try:
                    website.callback(params.get('state',[''])[0],params.get('grant',[''])[0])
                    message='Website connected. You can close this tab and return to Saarthi Admin.'
                    return self.respond(200,message.encode(),'text/plain; charset=utf-8')
                except ValueError as exc:return self.respond(400,{'error':str(exc)})
            if parts.path=='/start/'+token:
                return self.respond(303,b'',headers={'Location':'/',
                    'Set-Cookie':'saarthi_desktop='+token+'; HttpOnly; SameSite=Strict; Path=/'})
            if not self.authorised():return self.respond(403,{'error':'Open Saarthi Admin from its Windows shortcut'})
            query=urllib.parse.parse_qs(parts.query)
            arg=lambda key,default='':query.get(key,[default])[0]
            try:
                if parts.path=='/api/health':
                    updates.status()  # daily metadata check; download/install stays explicit
                    return self.respond(200,dict(app='saarthi-admin',database=str(archive.path),version=VERSION))
                if parts.path=='/api/agent':
                    status=agent.status() if agent else {'state':'disabled','message':'Automatic startup disabled for this preview'}
                    runtime=runtime_settings(archive.path.parent)
                    return self.respond(200,{**status,'deletion_check_pending':deletion_check_path(archive.path.parent).is_file(),
                                             'agent_sync_url':runtime.get('agent_sync_url',''),
                                             'agent_sync_linked':bool(runtime.get('agent_sync_token'))})
                if parts.path=='/api/website/status':return self.respond(200,website.status())
                if parts.path=='/api/website/users':return self.respond(200,website.call('users'))
                if parts.path=='/api/website/routes':return self.respond(200,website.call('routes'))
                if parts.path=='/api/work/context':return self.respond(200,work.context())
                if parts.path=='/api/work/party-rates':
                    party=next((p for p in work.context()['customers'] if p['id']==arg('guid')),None)
                    if not party:raise ValueError('Choose an existing sales party')
                    import tally_shop
                    with archive.read() as conn:rates=tally_shop.party_rates(conn,party['name'])
                    return self.respond(200,rates)
                if parts.path=='/api/work/history':return self.respond(200,work.history_page(arg('page','1'),arg('sort'),arg('direction','asc')))
                if parts.path=='/api/work/detail':return self.respond(200,work.detail(arg('id')))
                if parts.path=='/api/work/image':
                    saved=work.image(arg('guid'))
                    return self.respond(200,saved[1],saved[0]) if saved else self.respond(404,{'error':'No image attached'})
                if parts.path=='/api/updates':return self.respond(200,updates.status())
                if parts.path=='/api/analysis':return self.respond(200,analysis.report(arg('start'),arg('end'),arg('route'),arg('rep'),arg('comparison','previous'),arg('mode','custom'),arg('year'),arg('party')))
                if parts.path=='/api/overview':return self.respond(200,management.overview(arg('start'),arg('end')))
                if parts.path=='/api/management':return self.respond(200,management.report(arg('start'),arg('end'),arg('route'),arg('rep'),arg('party'),arg('mode','custom'),arg('year')))
                if parts.path=='/api/inventory':return self.respond(200,inventory(work))
                if parts.path=='/api/list':return self.respond(200,archive.listing(arg('kind'),arg('q'),arg('page','1'),category=arg('category'),start=arg('start'),end=arg('end'),sort=arg('sort'),direction=arg('direction','asc')))
                if parts.path=='/api/detail':return self.respond(200,archive.detail(arg('kind'),arg('id'),arg('version') or None))
                if parts.path=='/api/explorer':return self.respond(200,archive.explorer(arg('table'),arg('page','1'),arg('q'),arg('rowid') or None,arg('sort'),arg('direction','desc')))
                if parts.path=='/api/export':
                    table=arg('table')
                    with archive.read() as conn:
                        if table not in archive.tables(conn):raise ValueError('Unknown table')
                    self.send_response(200)
                    self.send_header('Content-Type','text/csv; charset=utf-8')
                    self.send_header('Content-Disposition','attachment; filename="archive.csv"')
                    self.send_header('Cache-Control','no-store');self.end_headers()
                    for chunk in archive.export(table):self.wfile.write(chunk.encode())
                    return
                files={'/':'index.html','/admin.js':'admin.js','/admin.css':'admin.css','/transactions.js':'transactions.js','/analysis.js':'analysis.js','/updates.js':'updates.js','/inventory.js':'inventory.js','/list-tools.js':'list-tools.js','/product-image.js':'product-image.js'}
                if parts.path not in files:return self.respond(404,{'error':'Not found'})
                file=ASSETS/files[parts.path]
                return self.respond(200,file.read_bytes(),(mimetypes.guess_type(file.name)[0] or 'application/octet-stream')+'; charset=utf-8')
            except (ValueError,FileNotFoundError) as exc:self.respond(400,{'error':str(exc)})
            except Exception:
                self.respond(500,{'error':'Could not read the archive. It may be busy; retry in a few seconds.'})

        def do_POST(self):
            expected=f'http://127.0.0.1:{self.server.server_port}'
            if not self.authorised() or self.headers.get('Origin')!=expected:
                return self.respond(403,{'error':'Local application session required'})
            try:
                length=int(self.headers.get('Content-Length','0'))
                maximum=2100000 if self.path=='/api/work/image' else 550000 if self.path in ('/api/inventory/rates/preview','/api/inventory/rates') else 65536
                if length<0 or length>maximum:return self.respond(413,{'error':'Request too large'})
                body=json.loads(self.rfile.read(length) or b'{}')
                if not isinstance(body,dict):raise ValueError('Request must be an object')
                if self.path=='/api/inventory/policy':return self.respond(200,save_policy(work,body))
                if self.path=='/api/inventory/visibility':return self.respond(200,save_visibility(work,body))
                if self.path=='/api/inventory/rates/preview':return self.respond(200,rate_preview(work,body.get('csv')))
                if self.path=='/api/inventory/rates':return self.respond(200,save_our_rates(work,body))
                if self.path=='/api/updates/check':return self.respond(200,updates.check())
                if self.path=='/api/updates/open':return self.respond(200,updates.open_tool(body.get('online') is True,body.get('recover') is True))
                if self.path=='/api/agent/remote':
                    if not agent or not getattr(agent,'managed_exe',None):raise ValueError('Install the managed package before linking remote sync')
                    url=body.get('url',''); token=body.get('token','')
                    runtime=runtime_settings(archive.path.parent)
                    if body.get('remove') is True:
                        runtime.pop('agent_sync_url',None);runtime.pop('agent_sync_token',None)
                    else:
                        url=website_url(url)
                        if not isinstance(token,str) or not 32<=len(token)<=256 or not token.isascii() or not token.isalnum():
                            raise ValueError('Enter the shared remote sync token (32–256 letters or digits).')
                        runtime.update(agent_sync_url=url,agent_sync_token=token)
                    atomic_json(archive.path.parent/'saarthi-runtime.json',runtime)
                    return self.respond(200,{'saved':True,'message':'Saved. Use Stop agent then Start agent to apply the connection.'})
                if self.path=='/api/agent/live':
                    enabled=body.get('enabled')
                    if type(enabled)!=bool:raise ValueError('Choose enabled or disabled')
                    if not agent or not getattr(agent,'managed_exe',None):raise ValueError('Install the complete managed package before changing live mode')
                    if pause_path(archive.path.parent).exists():raise ValueError('An update/recovery is pending')
                    if enabled:
                        with archive.read() as connection:
                            bound=connection.execute("SELECT value FROM sync_state WHERE key='company'").fetchone()
                            if not bound or not bound[0]:raise ValueError('Import and bind the correct company first')
                    atomic_json(archive.path.parent/'saarthi-runtime.json',{**runtime_settings(archive.path.parent),'billing_enabled':enabled})
                    return self.respond(200,{'saved':True,'message':'Live-mode setting saved. The current job finishes first; the agent verifies the bound Tally company before each live job.'})
                if self.path=='/api/work/submit':return self.respond(200,work.submit(body.get('id'),body.get('kind'),body.get('body')))
                if self.path=='/api/work/retry':return self.respond(200,work.retry(body.get('id')))
                if self.path=='/api/work/image':return self.respond(200,work.save_image(body.get('guid'),body.get('image')))
                if self.path=='/api/agent/start':
                    if not agent:raise ValueError('Agent startup disabled in this preview')
                    return self.respond(200,agent.start_async(resume=True))
                if self.path=='/api/agent/stop':
                    if not agent:raise ValueError('Agent controls disabled in this preview')
                    return self.respond(200,agent.stop_async())
                if self.path=='/api/agent/check-deletions':
                    status=agent.status() if agent else {}
                    if not status.get('managed') or status.get('state') not in ('started','running'):
                        raise ValueError('Start the packaged Tally agent first.')
                    if pause_path(archive.path.parent).exists():
                        raise ValueError('The agent is paused for an update or recovery.')
                    deletion_check_path(archive.path.parent).touch(exist_ok=True)
                    return self.respond(202,{'queued':True})
                if self.path=='/api/settings':
                    changes={}
                    if 'update_feed' in body:
                        from desktop_release import https_url
                        changes['update_feed']=https_url(body['update_feed']) if body['update_feed'] else ''
                    if 'website_url' in body:
                        changes['website_url']=website_url(body['website_url']) if body['website_url'] else ''
                        if changes['website_url']!=website.url:website.disconnect();website.url=changes['website_url']
                    if agent:
                        if body.get('agent_script') or body.get('agent_python'):
                            changes.update(agent.configure(body.get('agent_script',''),body.get('agent_python','')))
                    if save_settings:save_settings(changes)
                    return self.respond(200,{'saved':True})
                if self.path=='/api/website/connect':
                    url=website.connect(body.get('url',website.url),self.server.server_port)
                    if save_settings:save_settings({'website_url':website.url})
                    webbrowser.open(url)
                    return self.respond(200,{'pending':True})
                if self.path=='/api/website/disconnect':
                    website.disconnect();return self.respond(200,{'disconnected':True})
                if self.path=='/api/website/users':return self.respond(200,website.call('users',body))
                if self.path=='/api/website/active':
                    path='users/'+urllib.parse.quote(body['email'],safe='')+'/active'
                    return self.respond(200,website.call(path,{'active':bool(body['active'])}))
                if self.path=='/api/backup':
                    data=archive.backup(self.server.backup_dir)
                    if work.path.exists():data['admin_path']=work.backup(self.server.backup_dir)
                    return self.respond(200,data)
                if self.path=='/api/analysis/target':return self.respond(200,analysis.save_goal(body))
                if self.path=='/api/exit':
                    self.respond(200,{'closed':True})
                    threading.Thread(target=self.server.shutdown,daemon=True).start();return
                self.respond(404,{'error':'Unknown command'})
            except (ValueError,KeyError) as exc:self.respond(400,{'error':str(exc)})
            except Exception:self.respond(500,{'error':'Command failed. Check the connection, disk space or configured paths.'})
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    server.daemon_threads=True
    server.token=token
    server.backup_dir=Path(backup_dir or user_directory()/'backups')
    return server


class InstanceLock:
    def __init__(self,path):self.path=path;self.file=None
    def __enter__(self):
        self.file=open(self.path,'a+b');self.file.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.file.close();raise RuntimeError('already-running')
        return self
    def __exit__(self,*args):self.file.close()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db');parser.add_argument('--serve',action='store_true')
    parser.add_argument('--port',type=int,default=0)
    parser.add_argument('--backup-dir');parser.add_argument('--choose-db',action='store_true')
    parser.add_argument('--profile-dir',help='Override local settings directory (isolated testing)')
    parser.add_argument('--no-agent',action='store_true',help='Read-only archive review without starting the sync worker')
    parser.add_argument('--agent-script');parser.add_argument('--agent-python')
    parser.add_argument('--self-check',action='store_true',help='Validate archive and bundled assets without starting agent or UI')
    args=parser.parse_args(argv)
    if args.self_check:
        if not args.db:raise ValueError('Supply --db for self-check')
        check=Archive(args.db)
        with check.read() as connection:
            if connection.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Archive integrity check failed')
        for name in ('index.html','admin.js','analysis.js','transactions.js','updates.js','inventory.js','list-tools.js'):
            if not (ASSETS/name).is_file():raise ValueError('Bundled UI asset missing: '+name)
        Analysis(check).report()
        return 0
    home=Path(args.profile_dir) if args.profile_dir else user_directory();home.mkdir(parents=True,exist_ok=True)
    config_file=home/'settings.json';config={}
    if config_file.exists():config=json.loads(config_file.read_text(encoding='utf-8'))
    path=args.db or config.get('database') or str(Path(__file__).resolve().parent/'tally_raw.db')
    if args.choose_db or not Path(path).is_file():
        if args.serve:raise ValueError('Database missing: supply --db')
        import tkinter as tk
        from tkinter import filedialog
        root=tk.Tk();root.withdraw()
        path=filedialog.askopenfilename(title='Choose Saarthi local tally_raw.db',filetypes=[('SQLite archive','*.db *.sqlite'),('All files','*.*')])
        root.destroy()
        if not path:return 0
    archive=Archive(path)
    # Explicit command-line fixture previews must not change the user's saved setup.
    if not args.serve:
        config['database']=str(archive.path)
        config_file.write_text(json.dumps(config,indent=2),encoding='utf-8')
    instance=home/'instance.json'
    try:
        with InstanceLock(home/'admin.lock'):
            settings_lock=threading.Lock()
            def save_settings(changes):
                with settings_lock:
                    config.update(changes)
                    temporary=config_file.with_suffix('.pending')
                    temporary.write_text(json.dumps(config,indent=2),encoding='utf-8');temporary.replace(config_file)
            agent=AgentSupervisor(archive.path,args.agent_script or config.get('agent_script',''),args.agent_python or config.get('agent_python',''))
            server=make_server(archive,args.port,args.backup_dir or home/'backups',agent=agent,
                website=WebsiteAccess(config.get('website_url','')),save_settings=save_settings,profile_dir=home,settings=config)
            url=f'http://127.0.0.1:{server.server_port}'
            info=dict(url=url,token=server.token,database=str(archive.path))
            instance.write_text(json.dumps(info),encoding='utf-8')
            if not args.serve and not args.no_agent:agent.start_async()
            if args.serve:
                if sys.stdout is not None:print(url+'/start/'+server.token,flush=True)
            else:launch_window(url+'/start/'+server.token)
            # Edge can pause a window's timers while the window is still open.
            # The viewer stays available until Exit admin or the updater stops it.
            try:server.serve_forever(poll_interval=.3)
            finally:server.server_close();instance.unlink(missing_ok=True)
    except RuntimeError as exc:
        if str(exc)!='already-running':raise
        info=json.loads(instance.read_text())
        request=urllib.request.Request(info['url']+'/api/health',headers={'Cookie':'saarthi_desktop='+info['token']})
        with urllib.request.urlopen(request,timeout=3) as response:
            health=json.load(response)
        if health.get('app')!='saarthi-admin':raise ValueError('Another program is using the saved port')
        if health['database']!=str(archive.path):raise ValueError('Saarthi Admin already has a different archive open. Exit that window first.')
        launch_window(info['url']+'/start/'+info['token'])
    return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except KeyboardInterrupt:pass
    except Exception as exc:
        if getattr(sys,'frozen',False):
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,str(exc),'Saarthi Admin',16)
        else:raise
