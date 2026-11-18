"""Remote sync control shared by the API and the packaged laptop agent."""
import json
import time
from urllib.error import URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from saarthi_runtime import atomic_json, remote_resume_path, remote_sync_path, remote_sync_revision


CLOUD_JOBS = frozenset({'orders_pull', 'orders_publish', 'parties_visits', 'push',
                        'push_slow', 'retention', 'invoice'})
POLL_SECONDS = 20
OFFLINE_SECONDS = 75


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class RemoteSync:
    def __init__(self, url, token, directory=None):
        self.url = url
        self.token = token
        self.revision = ''
        self.paused = True
        self.directory = directory
        self.scheduler_paused = bool(directory and remote_sync_revision(directory))
        self.next_check = 0
        self.opener = build_opener(NoRedirect())

    def check(self, tasks, *, live_enabled):
        now = time.monotonic()
        if now < self.next_check:
            return
        self.next_check = now + POLL_SECONDS
        # A synchronous job has already returned to the scheduler. Background
        # jobs must also finish before the owner sees the pause acknowledged.
        draining = any(not future.done() for future in tasks.values())
        payload = {'revision': '' if draining and self.scheduler_paused else self.revision,
                   'live_enabled': live_enabled}
        resume = remote_resume_path(self.directory) if self.directory is not None else None
        requested_revision = ''
        if resume and not draining:
            try:
                requested_revision = json.loads(resume.read_text(encoding='utf-8'))['revision']
            except FileNotFoundError:
                pass
            if requested_revision and requested_revision == self.revision:
                payload['resume_revision'] = requested_revision
        body = json.dumps(payload).encode('utf-8')
        request = Request(self.url + '/api/agent-sync/poll', data=body,
                          headers={'Authorization': 'Bearer ' + self.token,
                                   'Content-Type': 'application/json'})
        try:
            with self.opener.open(request, timeout=5) as response:
                state = json.loads(response.read(4096))
            if type(state['paused']) is not bool or not isinstance(state['revision'], str) or not state['revision']:
                raise ValueError('Invalid remote sync status')
        except (OSError, URLError, ValueError, KeyError, TypeError):
            self.paused = True
            self.revision = ''
            return
        self.paused = state['paused']
        self.revision = state['revision']
        self.scheduler_paused = self.paused
        if self.directory is not None:
            status = remote_sync_path(self.directory)
            if self.paused and not draining:
                if remote_sync_revision(self.directory) != self.revision:
                    atomic_json(status, {'revision': self.revision})
            elif not self.paused:
                status.unlink(missing_ok=True)
            if requested_revision and (not self.paused or self.revision != requested_revision):
                resume.unlink(missing_ok=True)
