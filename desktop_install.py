"""Per-user installer for the bundled Saarthi Admin executable."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


def install(database,source=None,destination=None,profile=None):
    from desktop_archive import Archive
    archive=Archive(database)  # Validate before installing; never migrate/copy the books.
    base=Path(os.environ['LOCALAPPDATA'])
    destination=Path(destination or base/'Programs'/'Saarthi Admin')
    profile=Path(profile or base/'SaarthiAdmin')
    source=Path(source or Path(__file__).resolve().parent/'SaarthiAdmin.exe')
    if not source.is_file():raise ValueError('The bundled application is missing. Use the complete setup executable.')
    destination.mkdir(parents=True,exist_ok=True);profile.mkdir(parents=True,exist_ok=True)
    target=destination/'SaarthiAdmin.exe'
    shutil.copy2(source,target)
    config_file=profile/'settings.json'
    config=json.loads(config_file.read_text()) if config_file.exists() else {}
    config['database']=str(archive.path)
    config_file.write_text(json.dumps(config,indent=2),encoding='utf-8')
    start=Path(os.environ['APPDATA'])/'Microsoft/Windows/Start Menu/Programs'
    start.mkdir(parents=True,exist_ok=True)
    def ps(value):return "'"+str(value).replace("'","''")+"'"
    script=("$link=(New-Object -ComObject WScript.Shell).CreateShortcut("+ps(start/'Saarthi Admin.lnk')+
        ");$link.TargetPath="+ps(target)+";$link.WorkingDirectory="+ps(destination)+";$link.Save()")
    subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],check=True,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),capture_output=True)
    return target


def main():
    root=tk.Tk();root.title('Install Saarthi Admin');root.geometry('620x370');root.resizable(False,False)
    style=ttk.Style();style.theme_use('clam')
    frame=ttk.Frame(root,padding=30);frame.pack(fill='both',expand=True)
    ttk.Label(frame,text='Saarthi Admin',font=('Segoe UI',23,'bold')).pack(anchor='w')
    ttk.Label(frame,text='Full local history. Available offline.',font=('Segoe UI',12)).pack(anchor='w',pady=(2,22))
    ttk.Label(frame,text='Select your existing Saarthi tally_raw.db archive.').pack(anchor='w')
    path=tk.StringVar();ttk.Entry(frame,textvariable=path,width=75).pack(fill='x',pady=8)
    def choose():
        selected=filedialog.askopenfilename(title='Select local Saarthi archive',filetypes=[('SQLite archive','*.db *.sqlite')])
        if selected:path.set(selected)
    ttk.Button(frame,text='Choose database…',command=choose).pack(anchor='w')
    ttk.Label(frame,text='Your database stays in place. Opening Admin starts or attaches to the Tally\nagent. Windows login-startup settings stay unchanged.',wraplength=550).pack(anchor='w',pady=18)
    def go():
        try:
            target=install(path.get())
            messagebox.showinfo('Saarthi Admin installed','Open Saarthi Admin from the Start menu.\n\nApplication: '+str(target))
            root.destroy()
        except Exception as exc:messagebox.showerror('Installation could not finish',str(exc))
    ttk.Button(frame,text='Install Saarthi Admin',command=go).pack(anchor='e')
    root.mainloop()


if __name__=='__main__':main()
