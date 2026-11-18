"""Scoped desktop authorization and pairing tests; no real cloud writes."""
import base64
import hashlib
import os
import unittest
import urllib.parse
from unittest.mock import Mock,patch
from starlette.requests import Request
from fastapi import HTTPException
os.environ.setdefault('SESSION_SECRET','isolated-desktop-test-secret')
import api
from desktop_website import WebsiteAccess,website_url

OWNER=dict(email='owner@example.com',name='Owner',role='admin',routes=None)


class PairingTests(unittest.TestCase):
    def test_pairing_binds_state_and_verifier_and_keeps_token_private(self):
        request=Mock(return_value=dict(access_token='private-token',email=OWNER['email'],expires_in=3600))
        access=WebsiteAccess(request=request)
        url=access.connect('https://example.com',8810)
        q=urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        with self.assertRaises(ValueError):access.callback('wrong-state','grant')
        request.assert_not_called()
        access.callback(q['state'][0],'grant')
        verifier=request.call_args.args[1]['verifier']
        challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        self.assertEqual(q['challenge'],[challenge]);self.assertTrue(access.status()['connected'])
        self.assertNotIn('private-token',str(access.status()))
        access.call('users');self.assertEqual(request.call_args.args[2],'private-token')
        with self.assertRaises(ValueError):access.call('../../orders')
        with self.assertRaises(ValueError):access.callback(q['state'][0],'grant')
        access.disconnect()
        with self.assertRaises(ValueError):access.call('users')

    def test_expired_pairing_and_connection_rejected(self):
        access=WebsiteAccess(request=Mock())
        url=access.connect('https://example.com',8810);access._pending['at']-=301
        with self.assertRaises(ValueError):access.callback(urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)['state'][0],'grant')
        access._token='token';access._expires=0
        with self.assertRaises(ValueError):access.call('users')

    def test_url_requires_https_origin(self):
        self.assertEqual(website_url(' https://example.com/ '),'https://example.com')
        for value in ('http://example.com','https://user:password@example.com','https://example.com/path','https://example.com?x=1'):
            with self.subTest(value=value),self.assertRaises(ValueError):website_url(value)


class PermissionTests(unittest.TestCase):
    def request(self,token):return Request({'type':'http','headers':[(b'authorization',('Bearer '+token).encode())]})

    def test_only_owner_can_approve(self):
        cur=Mock()
        with self.assertRaises(HTTPException):api.desktop_approve(api.DesktopApprovalIn(challenge='a'*43),{**OWNER,'role':'salesperson'},cur)
        cur.execute.assert_not_called()

    def test_wrong_verifier_cannot_consume_grant(self):
        cur=Mock();cur.fetchone.return_value=(OWNER['email'],'different-challenge')
        with self.assertRaises(HTTPException):api.desktop_exchange(api.DesktopExchangeIn(grant='g'*43,verifier='v'*43),cur)
        self.assertEqual(cur.execute.call_count,1)

    def test_valid_exchange_consumes_grant_and_returns_scoped_token(self):
        verifier='v'*43;challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        cur=Mock();cur.fetchone.side_effect=[(OWNER['email'],challenge),(OWNER['email'],'Owner','admin',True)]
        result=api.desktop_exchange(api.DesktopExchangeIn(grant='g'*43,verifier=verifier),cur)
        self.assertEqual(api._desktop_signer().loads(result['access_token'])['scope'],'website-users')
        self.assertIn('used_at=now()',cur.execute.call_args.args[0])

    def test_used_or_expired_grant_rejected(self):
        cur=Mock();cur.fetchone.return_value=None
        with self.assertRaises(HTTPException):api.desktop_exchange(api.DesktopExchangeIn(grant='g'*43,verifier='v'*43),cur)

    def test_owner_role_and_active_flag_rechecked_each_request(self):
        token=api._desktop_signer().dumps({'email':OWNER['email'],'scope':'website-users'})
        cur=Mock();cur.fetchone.return_value=(OWNER['email'],'Owner','admin',True)
        self.assertEqual(api.desktop_owner(self.request(token),cur)['role'],'admin')
        for row in ((OWNER['email'],'Owner','admin',False),(OWNER['email'],'Owner','salesperson',True),None):
            cur.fetchone.return_value=row
            with self.assertRaises(HTTPException):api.desktop_owner(self.request(token),cur)

    def test_invalid_expired_or_wrong_scope_token_rejected(self):
        cur=Mock()
        for token in ('invalid',api._desktop_signer().dumps({'email':OWNER['email'],'scope':'all'})):
            with self.assertRaises(HTTPException):api.desktop_owner(self.request(token),cur)
        token=api._desktop_signer().dumps({'email':OWNER['email'],'scope':'website-users'})
        with patch('itsdangerous.timed.time.time',return_value=9999999999),self.assertRaises(HTTPException):
            api.desktop_owner(self.request(token),cur)

    def test_desktop_cannot_demote_or_disable_its_owner(self):
        cur=Mock()
        with self.assertRaises(HTTPException):api.desktop_save_user(api.UserIn(email=OWNER['email'],role='salesperson'),OWNER,cur)
        with self.assertRaises(HTTPException):api.desktop_set_active(OWNER['email'],api.ActiveIn(active=False),OWNER,cur)
        cur.execute.assert_not_called()
