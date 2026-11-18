"""Pairing and employee access against TEMP tables only; no permanent writes.

python tests/check_desktop_access_postgres.py --temporary-tables
"""
import argparse
import base64
import hashlib
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('SESSION_SECRET','isolated-desktop-check')
import psycopg
from fastapi import HTTPException
from starlette.requests import Request
import api
import cloud_db


def denied(fn):
    try:fn()
    except HTTPException as error:assert error.status_code in (400,401,403);return
    raise AssertionError('Expected authorization refusal')


def run():
    owner=dict(email='desktop-owner@example.test',name='Fixture Owner',role='admin',routes=None)
    with psycopg.connect(cloud_db.env('CLOUD_DB_URL'),connect_timeout=5,sslmode='require',
        options='-c default_transaction_read_only=on -c statement_timeout=15000') as connection:
        with connection.cursor() as cur:
            cur.execute('SET TRANSACTION READ WRITE');cur.execute('SET LOCAL search_path=pg_temp')
            cur.execute('CREATE TEMP TABLE app_user(email TEXT PRIMARY KEY,name TEXT,role TEXT,active BOOLEAN)')
            cur.execute('CREATE TEMP TABLE user_route(email TEXT,route TEXT)')
            cur.execute('CREATE TEMP TABLE customer(route TEXT)')
            cur.execute(cloud_db.DESKTOP_ACCESS_MIGRATION.replace('CREATE TABLE IF NOT EXISTS','CREATE TEMP TABLE IF NOT EXISTS'))
            connection.commit();cur.execute('SET LOCAL search_path=pg_temp')
            cur.execute('SHOW transaction_read_only');assert cur.fetchone()==('on',)
            cur.execute("INSERT INTO app_user VALUES(%s,%s,'admin',TRUE)",(owner['email'],owner['name']))
            verifier='v'*43
            challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
            grant=api.desktop_approve(api.DesktopApprovalIn(challenge=challenge),owner,cur)['grant']
            denied(lambda:api.desktop_exchange(api.DesktopExchangeIn(grant=grant,verifier='x'*43),cur))
            token=api.desktop_exchange(api.DesktopExchangeIn(grant=grant,verifier=verifier),cur)['access_token']
            denied(lambda:api.desktop_exchange(api.DesktopExchangeIn(grant=grant,verifier=verifier),cur))
            request=Request({'type':'http','headers':[(b'authorization',('Bearer '+token).encode())]})
            user=api.desktop_owner(request,cur)
            api.desktop_save_user(api.UserIn(email='rep@example.test',name='Fixture Rep'),user,cur)
            people=api.desktop_users(user,cur)
            assert len(people)==2 and any(p['email']=='rep@example.test' and p['active'] for p in people)
            api.desktop_set_active('rep@example.test',api.ActiveIn(active=False),user,cur)
            cur.execute("SELECT active FROM app_user WHERE email='rep@example.test'");assert cur.fetchone()==(False,)
            denied(lambda:api.desktop_set_active(owner['email'],api.ActiveIn(active=False),user,cur))
            cur.execute("UPDATE app_user SET active=FALSE WHERE email=%s",(owner['email'],))
            denied(lambda:api.desktop_owner(request,cur))
            connection.rollback()
    print('PASS: TEMP-only pairing, one-use grant, employee creation, access toggle and owner revocation')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--temporary-tables',required=True,action='store_true');parser.parse_args();run()
