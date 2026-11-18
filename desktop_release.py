"""Pinned release verification and bounded, executable-only release extraction."""
import base64
import hashlib
import json
import re
import stat
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature
from saarthi_runtime import PROTOCOL

FILES=('SaarthiAdmin.exe','SaarthiAgent.exe','SaarthiLauncher.exe','SaarthiUpdate.exe')
MAX_PACKAGE=500*1024*1024
MAX_MANIFEST=65536

def version(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d{1,4}\.\d{1,4}\.\d{1,4}',value):raise ValueError('Invalid release version')
    return tuple(map(int,value.split('.')))

def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode('utf-8')

def verified(raw,public_key=None):
    from release_public_key import PUBLIC_KEY
    if len(raw)>MAX_MANIFEST:raise ValueError('Release manifest too large')
    try:
        envelope=json.loads(raw);payload=envelope['manifest']
        key=base64.b64decode(public_key or PUBLIC_KEY,validate=True)
        Ed25519PublicKey.from_public_bytes(key).verify(base64.b64decode(envelope['signature'],validate=True),canonical(payload))
    except (ValueError,KeyError,TypeError,InvalidSignature):raise ValueError('Release signature is invalid or from another publisher') from None
    if not isinstance(payload,dict):raise ValueError('Invalid release manifest')
    version(payload.get('version'))
    if payload.get('protocol')!=PROTOCOL or payload.get('schema')!=1:raise ValueError('This update requires a newer installer')
    files=payload.get('files')
    if not isinstance(files,dict) or set(files)!=set(FILES):raise ValueError('Incomplete release')
    total=0
    for meta in files.values():
        if not isinstance(meta,dict) or not re.fullmatch('[0-9a-f]{64}',str(meta.get('sha256',''))):raise ValueError('Invalid release checksum')
        size=meta.get('bytes')
        if type(size)!=int or size<=0 or size>MAX_PACKAGE:raise ValueError('Invalid release size')
        total+=size
    if total>MAX_PACKAGE:raise ValueError('Release exceeds size limit')
    return payload

class SecureRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,request,fp,code,msg,headers,newurl):
        https_url(newurl)
        return super().redirect_request(request,fp,code,msg,headers,newurl)

def https_url(value):
    if not isinstance(value,str):raise ValueError('Enter an HTTPS release URL')
    parts=urllib.parse.urlsplit(value)
    if parts.scheme!='https' or not parts.hostname or parts.username or parts.password or parts.fragment:raise ValueError('Use an HTTPS URL without credentials or fragments')
    return value

def download(url,path,limit=MAX_PACKAGE):
    https_url(url)
    opener=urllib.request.build_opener(SecureRedirect())
    count=0
    try:
        with opener.open(urllib.request.Request(url,headers={'User-Agent':'Saarthi-Updater/1'}),timeout=30) as source,open(path,'wb') as target:
            while block:=source.read(1024*1024):
                count+=len(block)
                if count>limit:raise ValueError('Download exceeds release size limit')
                target.write(block)
    except Exception:
        Path(path).unlink(missing_ok=True);raise
    return count

def inspect_package(package,public_key=None):
    if Path(package).stat().st_size>MAX_PACKAGE:raise ValueError('Package too large')
    with zipfile.ZipFile(package) as archive:
        entries=archive.infolist()
        if len(entries)!=len(FILES)+1 or {e.filename for e in entries}!=set(FILES)|{'release.json'}:raise ValueError('Unexpected, duplicate or missing files in package')
        for entry in entries:
            if stat.S_ISLNK(entry.external_attr>>16) or entry.file_size>MAX_PACKAGE:raise ValueError('Unsafe package entry')
        manifest_entry=archive.getinfo('release.json')
        if manifest_entry.file_size>MAX_MANIFEST:raise ValueError('Manifest too large')
        manifest=verified(archive.read('release.json'),public_key)
        for name,meta in manifest['files'].items():
            if archive.getinfo(name).file_size!=meta['bytes']:raise ValueError('Release size mismatch')
        return manifest

def extract_package(package,target,public_key=None):
    manifest=inspect_package(package,public_key)
    target=Path(target);target.mkdir(parents=True,exist_ok=False)
    # Never extract paths from an untrusted ZIP; copy only this fixed allowlist.
    with zipfile.ZipFile(package) as archive:
        for name,meta in manifest['files'].items():
            digest=hashlib.sha256();count=0
            with archive.open(name) as source,open(target/name,'xb') as destination:
                while block:=source.read(1024*1024):
                    count+=len(block)
                    if count>meta['bytes']:raise ValueError('Expanded release exceeds expected size')
                    digest.update(block);destination.write(block)
            if count!=meta['bytes'] or digest.hexdigest()!=meta['sha256']:raise ValueError('Release checksum mismatch')
        (target/'release.json').write_bytes(archive.read('release.json'))
    return manifest
