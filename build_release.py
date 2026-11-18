"""Development-machine release signing; private keys never enter a release."""
import argparse
import base64
import datetime as dt
import hashlib
import json
from pathlib import Path
import zipfile
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PrivateFormat,PublicFormat,NoEncryption,load_pem_private_key
from desktop_release import FILES,canonical,version,https_url
from saarthi_runtime import VERSION,PROTOCOL

ROOT=Path(__file__).resolve().parent
KEY=ROOT/'.release-signing'/'release-key.pem'

def signing_key(initialize=False):
    if not KEY.exists():
        if not initialize:raise ValueError('Signing key missing. Initialise it once with --init-key; preserve it for every later release.')
        if (ROOT/'release_public_key.py').exists():raise ValueError('Publisher private key was lost. Restore its backup; do not generate a new publisher key.')
        KEY.parent.mkdir(exist_ok=True)
        key=Ed25519PrivateKey.generate()
        with KEY.open('xb') as stream:stream.write(key.private_bytes(Encoding.PEM,PrivateFormat.PKCS8,NoEncryption()))
    key=load_pem_private_key(KEY.read_bytes(),password=None)
    public=base64.b64encode(key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)).decode()
    pin=ROOT/'release_public_key.py'
    if pin.exists():
        from release_public_key import PUBLIC_KEY
        if PUBLIC_KEY!=public:raise ValueError('The signing key does not match the pinned publisher. Do not regenerate production keys.')
    else:pin.write_text('# Public verification key; private key is excluded from builds.\nPUBLIC_KEY='+repr(public)+'\n',encoding='utf-8')
    return key

def envelope(payload,key):return json.dumps({'manifest':payload,'signature':base64.b64encode(key.sign(canonical(payload))).decode()},indent=2).encode()

def release(folder,base_url='',release_version=VERSION):
    version(release_version);folder=Path(folder);key=signing_key()
    files={}
    for name in FILES:
        p=folder/name
        files[name]={'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
    manifest={'version':release_version,'protocol':PROTOCOL,'schema':1,'created_at':dt.datetime.now(dt.timezone.utc).isoformat(),'files':files}
    raw=envelope(manifest,key);(folder/'release.json').write_bytes(raw)
    package=folder/('Saarthi-'+release_version+'.zip')
    with zipfile.ZipFile(package,'w',zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('release.json',raw)
        for name in FILES:archive.write(folder/name,name)
    if base_url:
        https_url(base_url)
        feed={**manifest,'package_url':base_url.rstrip('/')+'/'+package.name,'package_bytes':package.stat().st_size,'package_sha256':hashlib.sha256(package.read_bytes()).hexdigest()}
        (folder/'latest.json').write_bytes(envelope(feed,key))
    return package

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--set-version');parser.add_argument('--init-key',action='store_true');parser.add_argument('--folder');parser.add_argument('--base-url',default='');parser.add_argument('--version',default=VERSION)
    args=parser.parse_args()
    if args.set_version:
        import re
        version(args.set_version)
        source=ROOT/'saarthi_runtime.py';source.write_text(re.sub(r"VERSION='[^']+'","VERSION="+repr(args.set_version),source.read_text(encoding='utf-8'),count=1),encoding='utf-8')
    if args.init_key:signing_key(True);print('Release signing key ready. Keep .release-signing private and backed up.')
    if args.folder:print('Signed package:',release(args.folder,args.base_url,args.version))
