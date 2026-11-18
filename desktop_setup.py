"""Complete setup and update GUI. Runs on the business laptop without Python."""
import argparse
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk,filedialog,messagebox
from desktop_release import inspect_package,version,https_url
from desktop_updater import stage_release,apply_release,recover_interrupted,installed_version,child_env,FLAGS
from desktop_deployment import install_root,profile_root,fetch_feed,fetch_package
from desktop_first_run import probe_company,import_archive,install_managed,write_cloud_url,validate_cloud_url
from saarthi_runtime import VERSION,atomic_json,runtime_settings


def main(argv=None):
    parser=argparse.ArgumentParser();parser.add_argument('--install',action='store_true');parser.add_argument('--choose-package',action='store_true');parser.add_argument('--package');parser.add_argument('--feed');parser.add_argument('--root');parser.add_argument('--profile');parser.add_argument('--database');parser.add_argument('--recover',action='store_true')
    parser.add_argument('--apply-online',action='store_true')
    args=parser.parse_args(argv)
    if args.apply_online and (not args.feed or args.install or args.recover or args.package or args.choose_package):
        parser.error('--apply-online requires an online update feed')
    root_path=Path(args.root or install_root()).resolve();profile=Path(args.profile or profile_root()).resolve()
    embedded=Path(__file__).resolve().parent/'release.zip'
    install=args.install or (not args.choose_package and not args.package and not args.feed and not args.recover)
    root=tk.Tk();root.title('Saarthi '+VERSION+' · '+('First-time setup' if install else 'Update & recovery'));root.geometry('760x690');root.minsize(680,600)
    style=ttk.Style();style.theme_use('clam')
    body=ttk.Frame(root,padding=24);body.pack(fill='both',expand=True)
    ttk.Label(body,text='Saarthi business laptop',font=('Segoe UI',22,'bold')).pack(anchor='w')
    ttk.Label(body,text='Dashboard + packaged Tally agent. No Python installation required.',wraplength=690).pack(anchor='w',pady=(3,16))
    messages=queue.Queue();status=tk.StringVar(value='Choose an existing Saarthi archive, or import from the company open in Tally.');busy=False;package=None;target=None
    mode=tk.StringVar(value='existing');database=tk.StringVar(value=args.database or '')
    if not database.get() and (profile/'settings.json').exists():database.set(json.loads((profile/'settings.json').read_text()).get('database',''))
    new_folder=tk.StringVar(value=str(profile.parent/'SaarthiData'))
    endpoint=tk.StringVar(value='http://localhost:9000');company=tk.StringVar();cloud=tk.StringVar();feed=tk.StringVar(value=args.feed or '')
    if install:
        ttk.Radiobutton(body,text='Use an existing Saarthi tally_raw.db (keep orders, history and settings)',variable=mode,value='existing').pack(anchor='w')
        row=ttk.Frame(body);row.pack(fill='x',pady=5);ttk.Entry(row,textvariable=database).pack(side='left',fill='x',expand=True)
        def select_db():
            choice=filedialog.askopenfilename(filetypes=[('Saarthi SQLite archive','*.db')])
            if choice:database.set(choice);mode.set('existing')
        ttk.Button(row,text='Choose archive…',command=select_db).pack(side='right',padx=(6,0))
        ttk.Radiobutton(body,text='Create tally_raw.db by reading Tally (not by choosing a .900 file)',variable=mode,value='new').pack(anchor='w',pady=(12,0))
        row=ttk.Frame(body);row.pack(fill='x',pady=5);ttk.Entry(row,textvariable=new_folder).pack(side='left',fill='x',expand=True)
        def select_dir():
            choice=filedialog.askdirectory()
            if choice:new_folder.set(choice);mode.set('new')
        ttk.Button(row,text='Data folder…',command=select_dir).pack(side='right',padx=(6,0))
        row=ttk.Frame(body);row.pack(fill='x',pady=(12,5));ttk.Label(row,text='Local Tally URL').pack(side='left');ttk.Entry(row,textvariable=endpoint,width=30).pack(side='left',padx=8)
        ttk.Label(body,text='Stop any existing agent and open the correct company in Tally. The first import may keep Tally busy; do it outside billing hours.',wraplength=690).pack(anchor='w')
        ttk.Label(body,text='Loaded company (verify before importing):').pack(anchor='w',pady=(10,0));ttk.Label(body,textvariable=company,font=('Segoe UI',11,'bold')).pack(anchor='w')
        ttk.Button(body,text='Read company from Tally',command=lambda:work(probe)).pack(anchor='w',pady=5)
        ttk.Label(body,text='Cloud database URL (optional; stays on this laptop)').pack(anchor='w',pady=(10,0));ttk.Entry(body,textvariable=cloud,show='•').pack(fill='x')
        ttk.Label(body,text='Publisher HTTPS latest.json URL (optional)').pack(anchor='w',pady=(8,0));ttk.Entry(body,textvariable=feed).pack(fill='x')
        ttk.Label(body,text='Installation starts in local read-only sync mode. Automatic invoicing is enabled separately inside Saarthi.',wraplength=690).pack(anchor='w',pady=(10,0))
    else:
        ttk.Label(body,text='The update waits for the current job, closes the viewer, verifies database backups, installs both components, and validates the release before switching versions.',wraplength=690).pack(anchor='w',pady=14)
        ttk.Label(body,text='Save any unfinished forms before continuing. Legacy Python agents must be stopped manually for the first managed installation.',wraplength=690).pack(anchor='w',pady=10)
    ttk.Separator(body).pack(fill='x',pady=14);ttk.Label(body,textvariable=status,wraplength=690).pack(anchor='w');bar=ttk.Progressbar(body,mode='indeterminate');bar.pack(fill='x',pady=12)
    def tell(message):messages.put(('status',message))
    def prepare():
        nonlocal package,target
        if package:return
        if args.feed:
            tell('Verifying publisher feed and downloading release…');manifest=fetch_feed(args.feed,profile/'update-cache');package=fetch_package(manifest,profile/'update-cache')
        elif args.package:package=Path(args.package)
        elif install:
            if not embedded.is_file():raise ValueError('Use the complete setup executable with its embedded release')
            package=embedded
        else:raise ValueError('Choose a signed release ZIP first')
        manifest,target=stage_release(package,root_path)
        tell('Verified publisher release '+manifest['version'])
    def probe():
        prepare();name=probe_company(target,Path(new_folder.get()),endpoint.get());messages.put(('company',name));return 'Loaded company: '+name
    def execute():
        if args.recover:
            recover_interrupted(root_path,profile,Path(database.get()),progress=tell);return 'Interrupted update recovered. You can reopen Saarthi.'
        prepare()
        if install:
            validate_cloud_url(cloud.get())
            if feed.get():https_url(feed.get())
            if mode.get()=='new':
                if not company.get():raise ValueError('Read and verify the loaded Tally company first')
                tell('Importing Tally masters, vouchers, stock and balances. No invoices are posted…')
                archive=import_archive(target,new_folder.get(),company.get(),endpoint.get())
            else:archive=Path(database.get()).resolve()
            install_managed(package,archive,root=root_path,profile=profile,progress=tell)
            write_cloud_url(archive.parent,cloud.get())
            config=json.loads((profile/'settings.json').read_text())
            if feed.get():config['update_feed']=feed.get();atomic_json(profile/'settings.json',config)
            return 'Installed successfully. Open Saarthi from the Start menu. Local Tally sync is enabled; automatic invoicing is still controlled by your existing/live-mode setting.'
        result=apply_release(package,root_path,profile,Path(database.get()),progress=tell)
        return 'Updated to '+result['version']+'. Verified backup: '+result['backup']
    def work(action):
        nonlocal busy
        if busy:return
        busy=True;bar.start(12);go.state(['disabled']);root.protocol('WM_DELETE_WINDOW',lambda:messagebox.showinfo('Operation running','Wait for setup/update to finish.'))
        def run():
            try:messages.put(('done',action()))
            except Exception as error:
                text=str(error) if isinstance(error,ValueError) else 'Operation could not finish. Check Tally/connectivity and the local import log. An interrupted update remains paused until recovery.'
                messages.put(('error',text))
        threading.Thread(target=run,daemon=True).start()
    def start():
        nonlocal package
        if busy:return
        if not install and not args.recover and not args.feed and not args.package:
            choice=filedialog.askopenfilename(filetypes=[('Signed Saarthi release','*.zip')])
            if not choice:return
            package=Path(choice)
            try:manifest=inspect_package(package)
            except Exception as error:messagebox.showerror('Invalid release',str(error));return
            if not messagebox.askokcancel('Update Saarthi','Install version '+manifest['version']+'? Save your forms first. The agent will finish its current job before updating.'):return
        elif not messagebox.askokcancel('Continue', 'Continue with '+('setup/import' if install else 'recovery' if args.recover else 'the signed update')+'? Save unfinished forms first.'):return
        work(execute)
    go=ttk.Button(body,text='Install / import' if install else 'Recover interrupted update' if args.recover else 'Install signed update',command=start);go.pack(anchor='e',pady=8)
    def poll():
        nonlocal busy
        try:
            while True:
                kind,value=messages.get_nowait()
                if kind=='company':company.set(value)
                else:status.set(value)
                if kind in ('done','error'):
                    busy=False;bar.stop();go.state(['!disabled']);root.protocol('WM_DELETE_WINDOW',root.destroy)
                    if kind=='error':messagebox.showerror('Saarthi',value)
        except queue.Empty:pass
        root.after(150,poll)
    poll()
    if args.apply_online:
        root.after_idle(lambda:work(execute))
    root.mainloop()

if __name__=='__main__':main()
