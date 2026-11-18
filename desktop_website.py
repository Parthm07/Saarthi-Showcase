"""Owner-approved, narrowly scoped website access. Tokens stay in memory."""
import base64
import hashlib
import json
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def website_url(value):
    value=value.strip().rstrip('/')
    parts=urllib.parse.urlsplit(value)
    if (parts.scheme!='https' or not parts.hostname or parts.username or parts.password
        or parts.path or parts.query or parts.fragment):
        raise ValueError('Enter the website HTTPS address, such as https://your-site.example')
    return value


class WebsiteAccess:
    def __init__(self,url='',request=None):
        self.url=website_url(url) if url else ''
        self._request=request or self.request
        self._token=None;self._expires=0;self.email=None;self._pending=None
        self._lock=threading.Lock()

    @staticmethod
    def request(url,body=None,token=None):
        headers={'Accept':'application/json'}
        if token:headers['Authorization']='Bearer '+token
        if body is not None:headers['Content-Type']='application/json'
        request=urllib.request.Request(url,data=json.dumps(body).encode() if body is not None else None,headers=headers)
        try:
            with urllib.request.build_opener(NoRedirect).open(request,timeout=10) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            try:detail=json.load(exc).get('detail','Website request was refused')
            except Exception:detail='Website request was refused'
            raise ValueError(detail if isinstance(detail,str) else 'Check the employee fields') from None
        except (OSError,ValueError):raise ValueError('Could not reach the website. Check the address and internet connection.') from None

    def status(self):
        return {'url':self.url,'connected':bool(self._token and time.monotonic()<self._expires),
                'email':self.email,'pending':bool(self._pending)}

    def connect(self,url,port):
        url=website_url(url)
        verifier=secrets.token_urlsafe(48)
        challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        with self._lock:
            self.url=url;self._token=None;self.email=None
            self._pending={'state':secrets.token_urlsafe(32),'verifier':verifier,'at':time.monotonic()}
            return url+'/desktop-connect.html?'+urllib.parse.urlencode(
                {'challenge':challenge,'state':self._pending['state'],'port':port})

    def callback(self,state,grant):
        with self._lock:
            pending=self._pending
            if not pending or time.monotonic()-pending['at']>300 or not secrets.compare_digest(pending['state'],state):
                raise ValueError('Connection link expired or does not belong to this desktop. Connect again.')
            response=self._request(self.url+'/api/desktop/token',{'grant':grant,'verifier':pending['verifier']})
            self._token=response['access_token'];self.email=response['email']
            self._expires=time.monotonic()+min(3600,int(response['expires_in']))
            self._pending=None

    def disconnect(self):
        with self._lock:self._token=None;self._pending=None;self.email=None

    def call(self,path,body=None):
        if not self.status()['connected']:raise ValueError('Connect the website using an owner account first')
        # Callers choose fixed endpoints; there is no arbitrary URL/SQL proxy.
        if path not in ('users','routes') and not (path.startswith('users/') and path.endswith('/active')):
            raise ValueError('Unsupported website command')
        return self._request(self.url+'/api/desktop/'+path,body,self._token)
