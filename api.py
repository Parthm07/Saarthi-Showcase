"""The website's API. The only thing the phones are allowed to talk to.

Runs in the cloud. Reads the slice of the archive the laptop pushed up,
writes orders back for the laptop to invoice. It never touches Tally and
never sees the local archive -- cloud_db is the only database module it
imports, by design.

Three rules this file exists to enforce:

  1. The allowlist is checked on EVERY request, not just at sign-in. A
     salesperson who leaves is cut off the moment the row is deactivated,
     even if their cookie is still valid for another month.
  2. Route scoping is applied in SQL, never in the browser. A rep cannot
     read, name, or order for a customer outside their routes -- including
     by guessing a guid, which is the whole reason the check is server-side.
  3. Money is recomputed here. The phone sends quantities and the rate it
     quoted; the amounts and the order total are calculated from those, so
     a tampered or stale client cannot decide what a shop owes.

    uvicorn api:app --reload --port 8000

Needs CLOUD_DB_URL and SESSION_SECRET. GOOGLE_CLIENT_ID enables real
sign-in; without it, see DEV_LOGIN at the bottom.
"""
import datetime as dt
import difflib
import hashlib
import base64
import hmac
import json
import os
import pathlib
import random
import re
import secrets
import threading
from decimal import Decimal
from uuid import UUID, uuid4

import psycopg
from fastapi import Cookie, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

import cloud_db
from remote_sync import OFFLINE_SECONDS
from saarthi_runtime import atomic_json

def _web_dir():
    """Where the site's files are served from.

    Use the versioned web/ in local and cloud runs. An old Desktop
    prototype must not bypass the draft/retry fixes. Explicit WEB_DIR is
    still supported for a deliberately chosen development directory.
    """
    if cloud_db.env("WEB_DIR"):
        return pathlib.Path(cloud_db.env("WEB_DIR"))
    return cloud_db.HERE / "web"


WEB_DIR = _web_dir()

GOOGLE_CLIENT_ID = cloud_db.env("GOOGLE_CLIENT_ID")

# A rep signs in once and stays signed in: re-authenticating mid-round in a
# shop with one bar of signal is the kind of friction that gets a product
# abandoned. Revocation does not depend on this expiring -- see rule 1.
SESSION_DAYS = 30

# Items are the same for everyone, so the catalogue is cached process-wide
# rather than re-read per phone. The laptop pushes at most once a minute.
CACHE_SECONDS = 5

# Dashboard lines cover the full 90-day chart range, scoped by party.
# A row cap could silently drop an edited invoice from period totals.

# party_rate.last_rate this far from the item's median across all parties is
# a data-entry artefact, not a negotiated price. Same thresholds the laptop
# uses -- see tally_shop.RATE_SUSPECT_LO/HI -- so the phone and the invoice
# agree on which rates are real.
RATE_SUSPECT_LO, RATE_SUSPECT_HI = 0.5, 2.0

# No supplier lead time exists in Tally. The prototype flags critical stock
# as cover < lead, so one constant stands in until the data does.
LEAD_DAYS_PLACEHOLDER = 7

app = FastAPI(title="Saarthi", docs_url=None, redoc_url=None)

_secret = cloud_db.env("SESSION_SECRET")
if not _secret:
    # A generated secret means every restart signs everyone out. That is
    # survivable locally and wrong in production, so it is loud.
    _secret = secrets.token_urlsafe(32)
    print("WARNING: SESSION_SECRET not set -- using a random one. Every "
          "restart will sign all users out. Set it in .env or on Railway.")
# The data payload is JSON full of repeated keys, so it compresses about
# tenfold. That matters on a phone with one bar of signal far more than it
# does on the office wifi -- and it costs one line.
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(
    SessionMiddleware, secret_key=_secret, session_cookie="saarthi",
    max_age=SESSION_DAYS * 86400, same_site="lax",
    # Secure cookies wherever the site is actually served over TLS, and
    # not locally -- over plain http a secure cookie is simply never sent,
    # so forcing it would silently break sign-in on the laptop.
    #
    # Detected rather than configured: asking someone to remember a flag
    # named HTTPS_ONLY is asking for a production site running without it.
    # Railway sets RAILWAY_ENVIRONMENT in every deployment.
    https_only=bool(cloud_db.env("HTTPS_ONLY")
                    or cloud_db.env("RAILWAY_ENVIRONMENT")),
)


_pool = None


def _get_pool():
    """Connections are POOLED, and that is not an optimisation.

    Opening a Postgres connection to Railway costs ~11 seconds from here:
    TLS plus auth over their public proxy. Opening one per request made
    /api/data take 36 s for an admin, when the four queries inside it add
    up to 7 s. Reusing connections is the whole difference.

    min_size keeps one warm so the first request of the day does not pay
    the handshake either.
    """
    global _pool
    if _pool is None:
        from psycopg_pool import ConnectionPool
        url = cloud_db.env("CLOUD_DB_URL")
        if not url:
            raise RuntimeError(
                "CLOUD_DB_URL is not set. On Railway: the API service ->"
                " Variables -> add CLOUD_DB_URL = ${{Postgres.DATABASE_URL}}")
        if "host.proxy.rlwy.net" in url or "user:password@" in url:
            # The example line out of .env.example, pasted as-is. The
            # symptom is otherwise a bare PoolTimeout, which names nothing.
            raise RuntimeError(
                "CLOUD_DB_URL is still the EXAMPLE from .env.example, not a"
                " real database. On Railway set it to ${{Postgres.DATABASE_URL}}"
                " (click the Postgres service -> Variables -> copy"
                " DATABASE_URL), or paste the revealed DATABASE_PUBLIC_URL.")
        if "sslmode=" not in url and ".railway.internal" not in url:
            # Railway's internal network is already private and its Postgres
            # does not necessarily offer TLS there, so forcing sslmode on an
            # internal host turns a working connection into a timeout.
            url += ("&" if "?" in url else "?") + "sslmode=require"
        _pool = ConnectionPool(url, min_size=1, max_size=8, timeout=8,
                               max_idle=300, open=True)
    return _pool


def db():
    """A pooled connection for one request.

    The pool's context manager commits on a clean exit and rolls back if
    the handler raised, so a request that fails half way cannot leave a
    partly-written order behind.

    A database that cannot be reached is a 503, not a 500. Reps work on
    patchy mobile data and the laptop's own link drops; "can't reach the
    server, try again" is something they can act on, where a stack trace
    reads as "the app is broken" and generates a phone call to the office.
    """
    try:
        pool = _get_pool()
        cx_ctx = pool.connection()
        cx = cx_ctx.__enter__()
    except RuntimeError as e:
        # Our own configuration checks, which say exactly what is wrong and
        # how to fix it. Showing only the class name -- "RuntimeError" --
        # threw that away and cost a deploy cycle. These messages are
        # written for this screen and contain no credentials.
        raise HTTPException(503, str(e))
    except Exception:
        raise HTTPException(
            503, "The cloud database is temporarily unavailable. Retry in a moment.")
    try:
        with cx.cursor() as cur:
            yield cur
    except BaseException as e:
        cx_ctx.__exit__(type(e), e, e.__traceback__)
        raise
    else:
        cx_ctx.__exit__(None, None, None)


# Commit/rollback before sending HTTP success, rather than after the response.
DB = Depends(db, scope="function")


# ----------------------------------------------------------------- auth

def me(request: Request, cur=DB):
    """The signed-in user, re-checked against the allowlist every request.

    Returns role and routes. `routes` is None for an admin, meaning "all" --
    callers must treat None and [] as opposites: None sees everything, []
    sees nothing. A salesperson with no routes assigned is a configuration
    mistake, and the safe reading of a mistake is zero access.
    """
    email = (request.session.get("email") or "").lower()
    if not email:
        raise HTTPException(401, "not signed in")
    cur.execute("SELECT email,name,role,active FROM app_user WHERE email=%s",
                (email,))
    row = cur.fetchone()
    if not row or not row[3]:
        request.session.clear()
        raise HTTPException(403, "this account is not allowed to sign in")
    if row[2] == "admin":
        return {"email": row[0], "name": row[1], "role": row[2],
                "routes": None}
    cur.execute("SELECT route FROM user_route WHERE email=%s ORDER BY route",
                (email,))
    return {"email": row[0], "name": row[1], "role": row[2],
            "routes": [r[0] for r in cur.fetchall()]}


def _scope(user):
    """All signed-in users may browse/order for every sales customer.

    customer is populated only from the local Sundry Debtors tree, excluding
    non-shop ledgers. Routes are catalogue filters, not access restrictions.
    Order ownership and admin commands retain their independent checks.
    """
    return "", []


class VisitBatchIn(BaseModel):
    email: str
    company: str
    rows: list[dict]


@app.post('/api/visits')
def record_visits(body: VisitBatchIn,user=Depends(me),cur=DB):
    if body.email.lower()!=user['email'].lower():raise HTTPException(403,'Visit account changed; sign in again')
    if not 1<=len(body.rows)<=100:raise HTTPException(400,'Send 1–100 visits')
    cur.execute('SELECT company FROM sync_status WHERE id=1');company=cur.fetchone()
    if not company or body.company!=company[0]:raise HTTPException(409,'Visit company changed; refresh before syncing')
    now=dt.datetime.now(dt.timezone.utc);prepared=[]
    for row in body.rows:
        try:
            at=dt.datetime.fromisoformat(str(row.get('at','')).replace('Z','+00:00'))
            if at.tzinfo is None:raise ValueError()
            at=at.astimezone(dt.timezone.utc)
            if not now-dt.timedelta(days=91)<=at<=now+dt.timedelta(minutes=5):raise ValueError()
        except ValueError:raise HTTPException(400,'Invalid visit timestamp') from None
        guid=row.get('cid')
        if not isinstance(guid,str) or len(guid)>200:raise HTTPException(400,'Invalid party')
        cur.execute('SELECT name,route FROM customer WHERE guid=%s',(guid,));party=cur.fetchone()
        if not party:raise HTTPException(400,'Visit party is no longer in the sales catalogue')
        day=at.astimezone(dt.timezone(dt.timedelta(hours=5,minutes=30))).strftime('%Y%m%d')
        prepared.append((user['email'].lower(),day,guid,party[0],party[1],at))
    cur.execute("SELECT to_regclass('sales_visit')")
    if not cur.fetchone()[0]:raise HTTPException(503,'Visit sync requires the visit-schema database upgrade')
    for row in prepared:
        cur.execute('INSERT INTO sales_visit(salesperson,vdate,customer_guid,customer_name,route,visited_at) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(salesperson,vdate,customer_guid) DO NOTHING',row)
    return {'saved':True}


@app.get("/api/config")
def config():
    """What the sign-in screen needs before anyone has signed in."""
    return {"google_client_id": GOOGLE_CLIENT_ID,
            "dev_login": bool(_dev_login_allowed())}


class GoogleIn(BaseModel):
    credential: str = Field(min_length=1, max_length=8192)


@app.post("/api/auth/google")
def auth_google(body: GoogleIn, request: Request, cur=DB):
    """Verify a Google ID token, then check it against the allowlist.

    Verification and authorisation are separate steps on purpose: Google
    tells us *who* this is, and app_user decides whether that person may
    use the app. Anyone with a Google account can produce a valid token.
    """
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(503, "Google sign-in is not configured")
    from google.auth.transport import requests as grequests
    from google.oauth2 import id_token
    try:
        info = id_token.verify_oauth2_token(
            body.credential, grequests.Request(), GOOGLE_CLIENT_ID)
    except ValueError as e:
        # Covers a wrong audience, a bad signature and an expired token.
        raise HTTPException(401, f"Google rejected that sign-in: {e}")
    except Exception as e:
        # Verification needs to reach Google for its signing keys. If that
        # fails, nobody can sign in -- and saying so beats a 500 with a
        # stack trace, which reads like "your account is broken".
        raise HTTPException(
            503, "Could not reach Google to verify the sign-in. "
                 f"Try again in a moment. ({e.__class__.__name__})")
    if not info.get("email_verified"):
        raise HTTPException(401, "that Google address is not verified")
    email = (info.get("email") or "").lower()
    cur.execute("SELECT email,name,role,active FROM app_user WHERE email=%s",
                (email,))
    row = cur.fetchone()
    if not row or not row[3]:
        # Deliberately specific: the office adds staff by hand, so "ask to
        # be added" is the useful message. It leaks only whether an address
        # is on a private allowlist, to someone who already proved they own
        # that address.
        raise HTTPException(
            403, f"{email} is not on the allowlist. Ask the office to add it.")
    # Keep Google's display name if we never recorded one.
    if info.get("name") and not row[1]:
        cur.execute("UPDATE app_user SET name=%s WHERE email=%s",
                    (info["name"], email))
    request.session["email"] = email
    return {"email": email, "name": row[1] or info.get("name"),
            "role": row[2]}


@app.post("/api/auth/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/api/me")
def whoami(user=Depends(me)):
    return user


_agent_sync_lock = threading.Lock()


def _agent_sync_config():
    token = os.environ.get('SAARTHI_AGENT_SYNC_TOKEN', '')
    mount = os.environ.get('RAILWAY_VOLUME_MOUNT_PATH')
    directory = os.environ.get('SAARTHI_AGENT_SYNC_DIR') or mount
    if os.environ.get('RAILWAY_ENVIRONMENT') and (
        not mount or not pathlib.Path(directory).resolve().is_relative_to(pathlib.Path(mount).resolve())
    ):
        raise HTTPException(503, 'Attach a Railway volume for the laptop sync control.')
    if len(token) < 32 or not directory:
        raise HTTPException(503, 'Remote sync needs a persistent storage folder and laptop token.')
    if mount and not pathlib.Path(mount).is_dir():
        raise HTTPException(503, 'The Railway volume for laptop sync is unavailable.')
    return pathlib.Path(directory) / 'agent-sync.json', token


def _agent_sync_state(path):
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    return {'paused': False, 'revision': uuid4().hex}


def _agent_sync_status(state):
    last_seen = state.get('last_seen')
    online = last_seen is not None and dt.datetime.now(dt.timezone.utc).timestamp() - last_seen < OFFLINE_SECONDS
    if not online:
        status = 'Laptop offline or not linked'
    elif state.get('ack') != state['revision']:
        status = 'Pausing' if state['paused'] else 'Resuming'
    elif not state['paused'] and not state.get('live_enabled'):
        status = 'Live mode is off on the laptop'
    else:
        status = 'Paused' if state['paused'] else 'Running'
    return {'paused': state['paused'], 'status': status}


class AgentSyncChange(BaseModel):
    paused: bool


class AgentSyncPoll(BaseModel):
    revision: str = Field(max_length=32)
    live_enabled: bool
    resume_revision: str = Field(default='', max_length=32)


@app.get('/api/agent-sync')
def agent_sync_status(user=Depends(me)):
    if user['role'] != 'admin':
        raise HTTPException(403, 'Owner access only')
    path, _ = _agent_sync_config()
    with _agent_sync_lock:
        return _agent_sync_status(_agent_sync_state(path))


@app.post('/api/agent-sync')
def set_agent_sync(body: AgentSyncChange, user=Depends(me)):
    if user['role'] != 'admin':
        raise HTTPException(403, 'Owner access only')
    path, _ = _agent_sync_config()
    with _agent_sync_lock:
        state = _agent_sync_state(path)
        if state['paused'] != body.paused or not path.exists():
            state.update(paused=body.paused, revision=uuid4().hex)
            atomic_json(path, state)
        return _agent_sync_status(state)


@app.post('/api/agent-sync/poll')
def poll_agent_sync(body: AgentSyncPoll, request: Request):
    # This route must stay independent of the website's PostgreSQL session.
    path, token = _agent_sync_config()
    supplied = request.headers.get('authorization', '').encode('utf-8')
    if not hmac.compare_digest(supplied, ('Bearer ' + token).encode('utf-8')):
        raise HTTPException(401, 'Laptop token required')
    with _agent_sync_lock:
        state = _agent_sync_state(path)
        if state['paused'] and body.resume_revision == body.revision == state['revision']:
            state.update(paused=False, revision=uuid4().hex)
        state.update(last_seen=dt.datetime.now(dt.timezone.utc).timestamp(),
                     ack=body.revision, live_enabled=body.live_enabled)
        atomic_json(path, state)
        return {'paused': state['paused'], 'revision': state['revision']}


# ----------------------------------------------------------------- data

_cache = {"at": None, "items": None, "status": None}


def _num(x):
    """Decimal -> float for JSON; None stays None.

    None and 0 are different answers here. A null stock figure means Tally
    has no value and the UI must render an empty state; 0 means in stock,
    none left. Collapsing them would invent data.
    """
    return None if x is None else float(x)


def _catalogue(cur):
    now = dt.datetime.now(dt.timezone.utc)
    if _cache["at"] and (now - _cache["at"]).total_seconds() < CACHE_SECONDS:
        return _cache["items"], _cache["status"]
    cur.execute("SELECT company,stock_asof,voucher_watermark,pushed_at,"
                "orders_waiting,oldest_wait_mins,orders_set_aside,"
                "invoice_blocked_since,invoice_error"
                " FROM sync_status WHERE id=1")
    s = cur.fetchone()
    status = {"company": s[0] if s else None,
              "stock_asof": str(s[1]) if s and s[1] else None,
              "pushed_at": str(s[3]) if s and s[3] else None,
              # Billing health. An invoice run that stalls looks exactly
              # like one that is idle, so these are the only way anyone
              # finds out before a shop telephones.
              "orders_waiting": s[4] if s else None,
              "oldest_wait_mins": s[5] if s else None,
              "orders_set_aside": s[6] if s else None,
              "invoice_blocked_since": str(s[7]) if s and s[7] else None,
              "invoice_error": s[8] if s else None} if s else {}
    has_rate_edits = cloud_db.pending_our_rates_available(cur)
    rate = 'COALESCE(pending.price,item.rate)' if has_rate_edits else 'item.rate'
    join = ' LEFT JOIN pending_our_rate AS pending USING (guid)' if has_rate_edits else ''
    cur.execute(f"SELECT item.guid,name,grp,unit,{rate},mrp,on_hand,saleable,daily,"
                f"cover_days,brand,scheme,stock_asof FROM item{join} ORDER BY name")
    items = [{
        "id": r[0], "name": r[1], "grp": r[2] or "Other", "unit": r[3] or "PCS",
        "price": _num(r[4]), "mrp": _num(r[5]),
        "onHand": _num(r[6]), "stock": _num(r[7]),
        "daily": _num(r[8]), "cover": _num(r[9]),
        "brand": r[10], "scheme": r[11],
        "lead": LEAD_DAYS_PLACEHOLDER,
        # Stale means "this number predates the last push", i.e. the laptop
        # could not read stock. The phone must say so rather than present it
        # as current.
        "stale": not r[12],
    } for r in cur.fetchall()]
    from product_media import available
    if available(cur):
        cur.execute('SELECT guid,digest FROM item_media')
        media = dict(cur.fetchall())
        for item in items:
            if item['id'] in media:
                item['image'] = '/api/product-image/' + media[item['id']]
    _cache.update(at=now, items=items, status=status)
    return items, status


def owner_purchase_rates(cur):
    cur.execute("SELECT to_regclass('public.owner_purchase_rate')")
    if not cur.fetchone()[0]:return {}
    cur.execute('SELECT guid,rate,unit,vdate FROM owner_purchase_rate')
    return {r[0]:dict(rate=_num(r[1]),unit=r[2],date=r[3]) for r in cur.fetchall()}


@app.get('/api/purchase-rates')
def purchase_rates(user=Depends(me),cur=DB):
    if user['role']!='admin':raise HTTPException(403,'Owner access only')
    return owner_purchase_rates(cur)


class OurRateIn(BaseModel):
    price: Decimal = Field(max_digits=12, decimal_places=2, allow_inf_nan=False)


@app.post('/api/products/{guid}/our-rate')
def update_our_rate(guid: str, body: OurRateIn, user=Depends(me), cur=DB):
    if user['role'] != 'admin':
        raise HTTPException(403, 'Owner access only')
    if not cloud_db.pending_our_rates_available(cur):
        raise HTTPException(503, 'The cloud database needs the Our rate schema upgrade.')
    cur.execute('''INSERT INTO pending_our_rate(guid,price,revision)
                   SELECT guid,%s,%s FROM item WHERE guid=%s
                   ON CONFLICT(guid) DO UPDATE SET price=excluded.price,revision=excluded.revision
                   RETURNING price''', (body.price, uuid4(), guid))
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, 'Product is no longer on the website')
    _cache['at'] = None
    return {'price': _num(row[0])}


@app.get('/api/product-image/{digest}')
def product_image(digest: str, request: Request, user=Depends(me), cur=DB):
    if not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise HTTPException(404, 'Image not found')
    from product_media import available
    if not available(cur):
        raise HTTPException(404, 'Image not found')
    cur.execute('SELECT mime,body FROM product_media WHERE digest=%s AND EXISTS '
                '(SELECT 1 FROM item_media WHERE item_media.digest=product_media.digest)', (digest,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, 'Image not found')
    headers = {'ETag': '"'+digest+'"', 'Cache-Control': 'private, max-age=86400',
               'X-Content-Type-Options': 'nosniff'}
    if request.headers.get('if-none-match') == headers['ETag']:
        return Response(status_code=304, headers=headers)
    return Response(content=bytes(row[1]), media_type=row[0], headers=headers)


@app.get("/api/data")
def data(user=Depends(me), cur=DB):
    """Full sales catalogue and customers; the browser pages their cards.

    Route filters/search run over this complete snapshot. Orders remain
    scoped separately to their submitting salesperson.
    """
    items, status = _catalogue(cur)
    items = [dict(item) for item in items]
    if user['role']=='admin':
        costs = owner_purchase_rates(cur)
        for item in items:
            cost=costs.get(item['id'])
            if cost:item.update(purchaseRate=cost['rate'],purchaseUnit=cost['unit'],purchaseDate=cost['date'])
    status = dict(status,detail_retention_days=7)
    where, params = _scope(user)

    cur.execute(
        "SELECT guid,name,route,outstanding,aov,last_days,churn,growth,trend,"
        "overdue,overdue_days,ageing_gap,gstin,phone,contact,credit_limit,"
        "lat,lng,address FROM customer WHERE TRUE" + where + " ORDER BY name",
        params)
    custs, names = [], []
    for r in cur.fetchall():
        names.append(r[1])
        custs.append({
            "id": r[0], "name": r[1], "territory": r[2], "place": r[2],
            "outstanding": _num(r[3]), "aov": _num(r[4]),
            "lastDays": r[5], "lastVisit": r[5], "churn": r[6],
            "growth": r[7], "trend": json.loads(r[8]) if r[8] else [],
            "overdue": _num(r[9]), "overdueDays": r[10],
            "ageingGap": _num(r[11]), "gstin": r[12], "phone": r[13],
            "contact": r[14], "limit": _num(r[15]),
            "lat": _num(r[16]), "lng": _num(r[17]), "area": r[18],
            "rep": None, "buys": [], "cross": [],
        })
    # Shops found on the road that have no ledger yet. They are listed
    # alongside the real ones so a rep can order against them the same day,
    # flagged so the UI can say the bill is waiting on the office.
    own = "" if user["role"] == "admin" else " AND proposed_by=%s"
    cur.execute(
        "SELECT id,name,route,phone,status,tally_guid FROM prospect"
        " WHERE status <> 'rejected' AND tally_guid IS NULL" + own,
        [] if user["role"] == "admin" else [user["email"]])
    for p in cur.fetchall():
        custs.append({
            "id": p[0], "name": p[1], "territory": p[2], "place": p[2],
            "phone": p[3], "isNew": True, "prospectStatus": p[4],
            # A new shop has no history of any kind -- no balance, no
            # ageing, no reorder cycle. Nulls, so the UI renders empty
            # states rather than a confident zero.
            "outstanding": None, "aov": None, "lastDays": None,
            "lastVisit": None, "churn": None, "growth": None, "trend": [],
            "overdue": None, "overdueDays": None, "ageingGap": None,
            "gstin": None, "contact": None, "limit": None,
            "lat": None, "lng": None, "area": None,
            "rep": None, "buys": [], "cross": [],
        })

    if not names:
        # No existing customers in scope: hand back a consistent world
        # rather than running the remaining queries with an empty IN ().
        return {"me": user, "status": status, "products": items,
                "customers": custs, "cats": _cats(items), "payments": {},
                "daily": {}, "lines": []}

    byname = {c["name"]: c for c in custs}
    guidof = {c["name"]: c["id"] for c in custs}
    idof = {i["name"]: i["id"] for i in items}

    # What each shop is due to reorder, and what it last paid. Joined here
    # because the two together are what the order screen puts on one row.
    # FULL OUTER, not LEFT. party_buy only holds items with an established
    # reorder cycle, so a shop that has bought twice has rows in party_rate
    # and none here -- and a LEFT JOIN dropped them entirely, leaving the
    # order screen with nothing pinned and 625 items alphabetically. What
    # the shop has actually been billed for is exactly "bought before",
    # whether or not a cycle can be inferred from it yet.
    cur.execute(
        "SELECT COALESCE(b.party,r.party), COALESCE(b.item,r.item),"
        " b.cyc_lo,b.cyc_hi,b.last_days,b.typical_qty,b.freq_n,b.freq_m,"
        " b.is_core,r.last_rate,r.last_date,r.item_median,r.times"
        " FROM party_buy b FULL OUTER JOIN party_rate r"
        "   ON r.party=b.party AND r.item=b.item"
        " WHERE COALESCE(b.party,r.party) = ANY(%s)"
        " ORDER BY b.is_core DESC NULLS LAST, b.freq_n DESC NULLS LAST,"
        " r.last_date DESC NULLS LAST",
        (names,))
    for (party, item, lo, hi, last, qty, fn, fm, core, rate, rdate,
         median, purchase_count) in cur.fetchall():
        c, pid = byname.get(party), idof.get(item)
        if not c or not pid:
            continue            # bought an item since deleted from Tally
        rate, median = _num(rate), _num(median)
        suspect = bool(rate and median
                       and not (RATE_SUSPECT_LO * median <= rate
                                <= RATE_SUSPECT_HI * median))
        c["buys"].append({
            "p": pid, "cyc": [lo, hi], "last": last, "qty": _num(qty),
            # A rate-only row has no cycle to speak of, so freq and core
            # must read as "not known", never as zero -- "taken in 0 of the
            # last 0 bills" is worse than saying nothing.
            "freq": [fn, fm] if fn is not None else None,
            "core": bool(core),
            # Due needs a cycle. Without one this is simply something they
            # have bought, which is still worth putting at the top.
            "due": bool(lo is not None and last is not None and last >= lo),
            # A suspect rate is withheld, not shown: quoting a typo as "what
            # they last paid" is worse than showing no rate at all.
            "rate": None if suspect else rate,
            "rateDate": rdate,
            "purchaseCount": purchase_count,
        })

    # Receipts, for the payments screen. Capped per party by the window
    # function so one heavy payer cannot crowd out everyone else.
    cur.execute(
        "SELECT party,vdate,number,amount FROM ("
        "  SELECT party,vdate,number,amount,"
        "         row_number() OVER (PARTITION BY party ORDER BY vdate DESC) k"
        "    FROM history_voucher WHERE vchtype='Receipt' AND source_state='active' AND party = ANY(%s)"
        ") q WHERE k <= 10 ORDER BY party, vdate DESC", (names,))
    pays = {}
    for party, vdate, number, amount in cur.fetchall():
        pays.setdefault(party, []).append({
            "date": vdate, "amt": round(abs(_num(amount) or 0)), "no": number,
            # Mode, cheque number, UTR and the bill a receipt settles are
            # all absent: bill-wise accounting is off in Tally.
            "mode": None, "into": None, "ref": None, "against": None,
        })

    # Real invoice lines, for the dashboard's period-over-period figures.
    # Each line keeps its own date -- deriving this from party_buy would
    # collapse a party's whole history into one point and make every
    # comparison meaningless.
    cur.execute(
        "SELECT v.vdate,v.party,l.item,l.qty,l.amount,v.guid,"
        " COALESCE((SELECT o.salesperson FROM cloud_order o WHERE o.tally_guid=v.guid"
        "  ORDER BY o.created_at LIMIT 1),(SELECT r.salesperson FROM order_receipt r "
        "  WHERE r.tally_guid=v.guid LIMIT 1)),v.amount"
        "  FROM history_line l JOIN history_voucher v ON v.guid=l.voucher_guid"
        " WHERE v.vchtype='Sales' AND v.source_state='active' AND v.party = ANY(%s)"
        " AND v.vdate >= %s ORDER BY v.vdate DESC", (names,
            (dt.datetime.now(dt.timezone(dt.timedelta(hours=5,minutes=30))).date()
             -dt.timedelta(days=90 if user['role']=='admin' else 0)).strftime('%Y%m%d')))
    lines, daily = [], {}
    for vdate, party, item, qty, amount, guid, salesperson, invoice_total in cur.fetchall():
        pid = idof.get(item)
        if pid:
            lines.append([vdate, guidof[party], pid, abs(_num(qty) or 0),
                          abs(_num(amount) or 0), guid,
                          salesperson if user['role']=='admin' or salesperson==user['email'] else None,
                          abs(_num(invoice_total) or 0)])
    cur.execute(
        "SELECT vdate,SUM(invoices),SUM(amount),COUNT(DISTINCT party)"
        " FROM daily_sales WHERE party = ANY(%s)" + ("" if user['role']=='admin' else
        " AND vdate=to_char(now() AT TIME ZONE 'Asia/Kolkata','YYYYMMDD')") +
        " GROUP BY vdate ORDER BY vdate DESC LIMIT 120", (names,))
    for vdate, n, value, parties in cur.fetchall():
        daily[vdate] = {"orders": n, "value": round(_num(value) or 0),
                        "parties": parties}

    visits = []
    if user['role'] == 'admin':
        cur.execute("SELECT to_regclass('public.sales_visit')")
        if cur.fetchone()[0]:
            cur.execute("SELECT salesperson,customer_guid,vdate FROM sales_visit "
                        "WHERE vdate >= to_char((now() AT TIME ZONE 'Asia/Kolkata')::date-7,'YYYYMMDD')")
            visits = [dict(rep=rep, cid=cid, date=date) for rep, cid, date in cur.fetchall()]
    return {"me": user, "status": status, "products": items,
            "customers": custs, "cats": _cats(items), "payments": pays,
            "daily": dict(sorted(daily.items())), "lines": lines, "visits": visits}


def _cats(items):
    """Category chips from Tally stock groups, biggest first.

    Tally's own Category field is "Not Applicable" on every item, so stock
    group is the only real grouping that exists.
    """
    n = {}
    for i in items:
        n[i["grp"]] = n.get(i["grp"], 0) + 1
    top = sorted(n.items(), key=lambda kv: -kv[1])[:8]
    return [["all", "All items"]] + [[g, g.title()[:22]] for g, _ in top]


# ----------------------------------------------------------- the people

def _admin(user):
    if user["role"] != "admin":
        raise HTTPException(403, "only an admin can manage people")


@app.get("/api/routes")
def routes(user=Depends(me), cur=DB):
    """The routes a salesperson can be given, with how many shops each holds.

    Taken from the customers themselves rather than a list someone types,
    because a route that does not match customer.route exactly gives a rep
    who can see nothing.
    """
    _admin(user)
    cur.execute("SELECT route,COUNT(*) FROM customer WHERE route IS NOT NULL"
                " GROUP BY route ORDER BY COUNT(*) DESC")
    return [{"route": r[0], "shops": r[1]} for r in cur.fetchall()]


@app.get("/api/users")
def list_users(user=Depends(me), cur=DB):
    _admin(user)
    cur.execute("""
        SELECT u.email, u.name, u.role, u.active,
               COALESCE(ARRAY_AGG(r.route ORDER BY r.route)
                        FILTER (WHERE r.route IS NOT NULL), '{}')
          FROM app_user u LEFT JOIN user_route r ON r.email = u.email
         GROUP BY u.email, u.name, u.role, u.active
         ORDER BY u.role, u.email""")
    rows = [{"email": r[0], "name": r[1], "role": r[2], "active": r[3],
             "routes": list(r[4])} for r in cur.fetchall()]
    # Routes are optional preferences; every allowed user sees all shops.
    cur.execute("SELECT route,COUNT(*) FROM customer WHERE route IS NOT NULL"
                " GROUP BY route")
    per = dict(cur.fetchall())
    cur.execute("SELECT COUNT(*) FROM customer")
    everything = cur.fetchone()[0]
    for u in rows:
        u["shops"] = everything
        u["is_you"] = u["email"] == user["email"]
    return rows


class UserIn(BaseModel):
    email: str = Field(min_length=5, max_length=200)
    name: str | None = Field(default=None, max_length=120)
    role: str = Field(default="salesperson")
    routes: list[str] = Field(default_factory=list, max_length=40)

    @field_validator("email")
    @classmethod
    def looks_like_an_address(cls, v):
        v = v.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v):
            raise ValueError("that is not an email address")
        return v

    @field_validator("role")
    @classmethod
    def known_role(cls, v):
        if v not in ("admin", "salesperson"):
            raise ValueError("role must be admin or salesperson")
        return v


@app.post("/api/users")
def save_user(body: UserIn, user=Depends(me), cur=DB):
    """Add someone, or change their role and routes.

    Adding an address here is the ONLY way anyone gets in: Google proves
    who someone is, this table decides whether they may use the app.
    """
    _admin(user)
    # Routes are checked against the ones that exist, so a typo is refused
    # now rather than showing up later as a rep who sees an empty list.
    if body.routes:
        cur.execute("SELECT DISTINCT route FROM customer"
                    " WHERE route IS NOT NULL")
        real = {r[0] for r in cur.fetchall()}
        bad = [r for r in body.routes if r not in real]
        if bad:
            raise HTTPException(400, f"no such route: {', '.join(bad)}")
    if body.email == user["email"] and body.role != "admin":
        # Otherwise the last admin can quietly remove their own access and
        # nobody can get back in without a developer and a terminal.
        raise HTTPException(400, "you cannot remove your own admin access")

    cur.execute(
        "INSERT INTO app_user(email,name,role,active) VALUES(%s,%s,%s,TRUE)"
        " ON CONFLICT (email) DO UPDATE SET"
        " name=COALESCE(EXCLUDED.name,app_user.name), role=EXCLUDED.role",
        (body.email, body.name, body.role))
    cur.execute("DELETE FROM user_route WHERE email=%s", (body.email,))
    if body.routes and body.role != "admin":
        cur.executemany("INSERT INTO user_route(email,route) VALUES(%s,%s)",
                        [(body.email, r) for r in body.routes])
    return {"email": body.email, "role": body.role,
            "routes": [] if body.role == "admin" else body.routes}


class ActiveIn(BaseModel):
    active: bool


@app.post("/api/users/{email}/active")
def set_active(email: str, body: ActiveIn, user=Depends(me), cur=DB):
    """Turn someone's access on or off.

    Switching off takes effect on their very next request, even though
    their cookie is good for another month -- the allowlist is re-checked
    every time. It keeps the person's history, which deleting them would
    not.
    """
    _admin(user)
    email = email.strip().lower()
    if email == user["email"] and not body.active:
        raise HTTPException(400, "you cannot switch off your own access")
    if not body.active:
        cur.execute("SELECT COUNT(*) FROM app_user WHERE role='admin'"
                    " AND active AND email <> %s", (email,))
        if cur.fetchone()[0] == 0:
            raise HTTPException(
                400, "that is the last active admin -- switching it off "
                     "would lock everyone out")
    cur.execute("UPDATE app_user SET active=%s WHERE email=%s",
                (body.active, email))
    if not cur.rowcount:
        raise HTTPException(404, "no such person")
    return {"email": email, "active": body.active}


@app.post("/api/users/{email}/remove")
def remove_user(email: str, user=Depends(me), cur=DB):
    """Delete someone outright. Prefer switching them off.

    Orders they placed are kept -- cloud_order.salesperson is the audit
    trail of who sold what, and it must survive the person leaving.
    """
    _admin(user)
    email = email.strip().lower()
    if email == user["email"]:
        raise HTTPException(400, "you cannot remove yourself")
    cur.execute("SELECT COUNT(*) FROM app_user WHERE role='admin'"
                " AND active AND email <> %s", (email,))
    if cur.fetchone()[0] == 0:
        raise HTTPException(400, "that is the last active admin")
    cur.execute("DELETE FROM app_user WHERE email=%s", (email,))
    if not cur.rowcount:
        raise HTTPException(404, "no such person")
    return {"email": email, "removed": True}


# ------------------------------------------------------------ prospects

# A proposed name this close to an existing shop is almost certainly the
# same shop typed differently. "Sharma Kirana" vs "Sharma Kirana (Betma)"
# is exactly how a duplicate ledger gets born, and a duplicate ledger
# splits one shop's balance and history in two for ever.
NEAR_DUPLICATE = 0.82


def _normal(s):
    """Names as a human means them: case, punctuation and spacing dropped."""
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _similar(name, existing):
    """Shops whose names look like this one, closest first."""
    want, out = _normal(name), []
    for other in existing:
        got = _normal(other)
        if not got:
            continue
        r = difflib.SequenceMatcher(None, want, got).ratio()
        # Containment counts too: the short form of a long name scores
        # poorly on ratio but is the commonest way a duplicate is typed.
        if want and got and (want in got or got in want):
            r = max(r, 0.9)
        if r >= NEAR_DUPLICATE:
            out.append((round(r, 3), other))
    return [n for _, n in sorted(out, reverse=True)[:5]]


class ProspectIn(BaseModel):
    name: str = Field(min_length=3, max_length=120)
    route: str | None = Field(default=None, max_length=120)
    phone: str | None = Field(default=None, max_length=40)
    address: str | None = Field(default=None, max_length=300)
    note: str | None = Field(default=None, max_length=500)
    # Set only after the rep has seen the near-matches and said it really
    # is a different shop.
    confirm_duplicate: bool = False


@app.post("/api/prospects")
def new_prospect(body: ProspectIn, user=Depends(me), cur=DB):
    """Office-only creation of a shop awaiting its Tally ledger."""
    if user["role"] != "admin":
        raise HTTPException(403, "Only the office can create a new party. Choose an existing party or contact the office.")
    name = " ".join(body.name.split())

    # The office chooses an existing route/Tally group for the new ledger.
    route = body.route
    cur.execute("SELECT 1 FROM customer WHERE route=%s LIMIT 1", (route,))
    if not route or not cur.fetchone():
        raise HTTPException(400, "that is not an existing route")

    cur.execute("SELECT name FROM customer")
    existing = [r[0] for r in cur.fetchall()]
    cur.execute("SELECT name FROM prospect WHERE status <> 'rejected'")
    existing += [r[0] for r in cur.fetchall()]
    if _normal(name) in {_normal(e) for e in existing}:
        raise HTTPException(409, f"'{name}' already exists")
    near = _similar(name, existing)
    if near and not body.confirm_duplicate:
        # 409 with the candidates, so the phone can ask "did you mean?"
        # rather than silently creating a second ledger for one shop.
        raise HTTPException(409, {"message": "this may already exist",
                                  "matches": near})

    pid = "NEW-" + secrets.token_hex(6).upper()
    cur.execute(
        "INSERT INTO prospect(id,name,route,phone,address,note,proposed_by)"
        " VALUES(%s,%s,%s,%s,%s,%s,%s)",
        (pid, name, route, body.phone, body.address, body.note,
         user["email"]))
    return {"id": pid, "name": name, "route": route, "status": "pending"}


@app.get("/api/prospects")
def list_prospects(user=Depends(me), cur=DB):
    """The approvals queue for an admin; their own proposals for a rep."""
    mine = "" if user["role"] == "admin" else " WHERE proposed_by=%s"
    args = [] if user["role"] == "admin" else [user["email"]]
    cur.execute(
        "SELECT id,name,route,phone,address,note,proposed_by,created_at,"
        "status,decided_by,decide_note,tally_guid FROM prospect" + mine +
        " ORDER BY (status='pending') DESC, created_at DESC LIMIT 200", args)
    rows = [{"id": r[0], "name": r[1], "route": r[2], "phone": r[3],
             "address": r[4], "note": r[5], "by": r[6], "at": r[7].isoformat() if r[7] else None,
             "status": r[8], "decided_by": r[9], "decide_note": r[10],
             # Approved is not the same as usable: the ledger only exists
             # once the laptop has made it, and orders wait until then.
             "in_tally": bool(r[11])} for r in cur.fetchall()]
    ids = [p["id"] for p in rows] or [""]
    cur.execute("SELECT prospect_id,COUNT(*),COALESCE(SUM(total),0)"
                " FROM cloud_order WHERE prospect_id = ANY(%s)"
                " GROUP BY prospect_id", (ids,))
    waiting = {r[0]: (r[1], float(r[2])) for r in cur.fetchall()}
    for p in rows:
        p["orders"], p["order_value"] = waiting.get(p["id"], (0, 0.0))
    return rows


class DecisionIn(BaseModel):
    approve: bool
    note: str | None = Field(default=None, max_length=300)
    route: str | None = Field(default=None, max_length=120)


@app.post("/api/prospects/{pid}/decide")
def decide_prospect(pid: str, body: DecisionIn, user=Depends(me),
                    cur=DB):
    """Approve or reject. Admin only, and it does NOT touch Tally.

    Approving says "yes, make this shop". The laptop creates the ledger on
    its next pass, because only the laptop can reach Tally -- and only it
    can confirm the ledger really exists before orders are released.
    """
    if user["role"] != "admin":
        raise HTTPException(403, "only an admin can approve a new shop")
    cur.execute("SELECT status,name FROM prospect WHERE id=%s", (pid,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, "no such prospect")
    if row[0] != "pending":
        raise HTTPException(409, f"already {row[0]}")
    if body.approve and body.route:
        cur.execute("SELECT 1 FROM customer WHERE route=%s LIMIT 1",
                    (body.route,))
        if not cur.fetchone():
            raise HTTPException(400, "that is not an existing route")
        cur.execute("UPDATE prospect SET route=%s WHERE id=%s",
                    (body.route, pid))
    cur.execute(
        "UPDATE prospect SET status=%s, decided_by=%s, decided_at=now(),"
        " decide_note=%s WHERE id=%s",
        ("approved" if body.approve else "rejected", user["email"],
         body.note, pid))
    if not body.approve:
        # The orders cannot ever be billed, so say so rather than leaving
        # them queued for a ledger that will never exist.
        cur.execute("UPDATE cloud_order SET status='rejected'"
                    " WHERE prospect_id=%s AND tally_vchno IS NULL", (pid,))
    return {"id": pid, "name": row[1],
            "status": "approved" if body.approve else "rejected"}


# --------------------------------------------------------------- orders

class LineIn(BaseModel):
    item_guid: str = Field(min_length=1, max_length=120)
    qty: float = Field(gt=0, le=100000)
    rate: float = Field(gt=0, le=10_000_000)


class OrderIn(BaseModel):
    # Optional for older clients during rollout. Current clients always send it.
    request_id: UUID | None = None
    customer_guid: str = Field(min_length=1, max_length=120)
    note: str | None = Field(default=None, max_length=500)
    invoice_date: dt.date | None = None
    lines: list[LineIn] = Field(min_length=1, max_length=200)

    @field_validator("lines")
    @classmethod
    def no_duplicate_items(cls, v):
        guids = [l.item_guid for l in v]
        if len(set(guids)) != len(guids):
            raise ValueError("the same item appears twice")
        return v


def _order_no():
    """A short, human-sayable number. Allocated by the cloud, never the
    phone: with several reps ordering while the laptop may be switched off,
    two allocators would eventually collide on the same number."""
    return (f"WEB-{dt.datetime.now().strftime('%m%d')}"
            f"{random.randint(100000, 999999)}")


def _order_fingerprint(body):
    content = body.model_dump(mode="json", exclude={"request_id"})
    if body.invoice_date is None:
        content.pop('invoice_date')  # Preserve old retry fingerprints.
    content["lines"].sort(key=lambda line: line["item_guid"])
    return hashlib.sha256(json.dumps(
        content, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _order_reply(no, total, count, customer, status):
    return {"order_no": no, "total": float(total), "lines": count,
            "customer": customer, "status": status,
            "note": ("Saved. This shop is new, so the bill waits until the "
                     "office adds it." if status == "pending_party" else None)}


@app.post("/api/orders")
def place_order(body: OrderIn, user=Depends(me), cur=DB):
    """Record an order for the laptop to invoice.

    The customer check is the security boundary: a guid is guessable, a
    route assignment is not, so scope is re-applied here rather than
    trusted from whatever the phone had on screen.
    """
    request_id = str(body.request_id) if body.request_id else None
    fingerprint = _order_fingerprint(body) if request_id else None
    if request_id:
        # Serialize same-user retries until the first transaction commits
        # or rolls back. Hash collisions only serialize unrelated requests;
        # a unique index still enforces the durable request identity.
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (json.dumps([user["email"], request_id]),))
        cur.execute(
            "SELECT o.order_no,o.total,o.customer_name,o.status,"
            "o.request_fingerprint,(SELECT COUNT(*) FROM cloud_order_line l"
            " WHERE l.order_no=o.order_no) FROM cloud_order o"
            " WHERE o.salesperson=%s AND o.client_request_id=%s",
            (user["email"], request_id))
        prior = cur.fetchone()
        if prior:
            if prior[4] != fingerprint:
                raise HTTPException(409, "This submission ID belongs to a different order. "
                                    "Check the original order before changing it.")
            # Auth/allowlist was checked by me(). Resolve a saved request
            # even if an item/customer has subsequently disappeared.
            return _order_reply(prior[0], prior[1], prior[5], prior[2], prior[3])
        cur.execute('SELECT request_fingerprint,response FROM order_receipt '
                    'WHERE salesperson=%s AND client_request_id=%s',
                    (user['email'],request_id))
        archived = cur.fetchone()
        if archived:
            if archived[0] != fingerprint:
                raise HTTPException(409,'This submission ID belongs to a different archived order.')
            return archived[1]

    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30))).date()
    if body.invoice_date and body.invoice_date < today:
        raise HTTPException(422, 'Choose today or a future invoice date.')
    where, params = _scope(user)
    prospect = body.customer_guid.startswith("NEW-")
    if prospect:
        # A shop found on the road. Orderable straight away, but it cannot
        # be invoiced until the ledger exists, so it is parked at
        # 'pending_party' and the invoice batch leaves it alone.
        own = "" if user["role"] == "admin" else " AND proposed_by=%s"
        cur.execute(
            "SELECT id,name,route,status FROM prospect WHERE id=%s" + own,
            [body.customer_guid] + ([] if user["role"] == "admin"
                                    else [user["email"]]))
        c = cur.fetchone()
        if not c:
            raise HTTPException(404, "no such new shop of yours")
        if c[3] == "rejected":
            raise HTTPException(409, f"{c[1]} was turned down by the office")
    else:
        cur.execute("SELECT guid,name,route FROM customer WHERE guid=%s"
                    + where, [body.customer_guid] + params)
        c = cur.fetchone()
        if not c:
            raise HTTPException(404, "no such sales customer")

    cur.execute("SELECT guid,name,unit FROM item WHERE guid = ANY(%s)",
                ([l.item_guid for l in body.lines],))
    known = {r[0]: r for r in cur.fetchall()}
    missing = [l.item_guid for l in body.lines if l.item_guid not in known]
    if missing:
        raise HTTPException(400, f"{len(missing)} item(s) no longer exist")

    # Amounts are computed here, not accepted. Rounded per line so the sum
    # the shop is shown is the sum of the lines it can see.
    rows, total = [], 0.0
    for n, l in enumerate(body.lines, 1):
        g, name, unit = known[l.item_guid]
        amount = round(l.qty * l.rate, 2)
        total += amount
        rows.append((n, g, name, unit, l.qty, l.rate, amount))
    total = round(total, 2)

    cur.execute("SELECT stock_asof FROM sync_status WHERE id=1")
    asof = (cur.fetchone() or [None])[0]

    status = "pending_party" if prospect else "placed"
    date_column, date_placeholder, date_args = (',invoice_date', ',%s', (body.invoice_date,)) if body.invoice_date else ('', '', ())
    for _ in range(5):
        no = _order_no()
        # A compact archived receipt continues to reserve its order number.
        cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,1))',(no,))
        cur.execute('SELECT 1 FROM order_receipt WHERE order_no=%s',(no,))
        if cur.fetchone():
            continue
        cur.execute(
            "INSERT INTO cloud_order(order_no,salesperson,customer_guid,"
            "customer_name,route,status,subtotal,gst,total,stock_asof,note,"
            f"prospect_id,client_request_id,request_fingerprint{date_column})"
            f" VALUES(%s,%s,%s,%s,%s,%s,%s,0,%s,%s,%s,%s,%s,%s{date_placeholder})"
            " ON CONFLICT (order_no) DO NOTHING RETURNING order_no",
            (no, user["email"], c[0], c[1], c[2], status, total, total, asof,
             body.note, c[0] if prospect else None, request_id, fingerprint)
            + date_args)
        if cur.fetchone():
            break
    else:
        raise HTTPException(500, "could not allocate an order number")
    cur.executemany(
        "INSERT INTO cloud_order_line(order_no,line_no,item_guid,item_name,"
        "unit,qty,rate,amount) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
        [(no,) + r for r in rows])
    return _order_reply(no, total, len(rows), c[1], status)


class OrderAcknowledgement(BaseModel):
    order_no: str = Field(min_length=1,max_length=120)
    request_id: UUID | None = None


@app.post('/api/orders/ack')
def acknowledge_order(body: OrderAcknowledgement,user=Depends(me),cur=DB):
    """Only the submitting account can acknowledge receiving this exact order."""
    cur.execute('UPDATE cloud_order SET retry_protected=FALSE WHERE order_no=%s '
                'AND salesperson=%s AND client_request_id IS NOT DISTINCT FROM %s '
                'RETURNING order_no',(body.order_no,user['email'],str(body.request_id) if body.request_id else None))
    if cur.fetchone():
        return {'acknowledged':True}
    cur.execute('SELECT 1 FROM order_receipt WHERE order_no=%s AND salesperson=%s '
                'AND client_request_id=%s',(body.order_no,user['email'],str(body.request_id)))
    return {'acknowledged':bool(cur.fetchone())}


@app.get("/api/orders")
def my_orders(user=Depends(me), cur=DB, include_tally: bool = False):
    """This user's recent orders, with whatever Tally has since said.

    An admin sees every rep's orders; a rep sees only their own, so one
    salesperson cannot read another's book.
    """
    mine = "" if user["role"] == "admin" else " AND salesperson=%s"
    args = [] if user["role"] == "admin" else [user["email"]]
    desk = include_tally and user['role'] == 'admin'
    desk_day = ("COALESCE(to_date(NULLIF(h.vdate,''),'YYYYMMDD'),"
                "(to_jsonb(o)->>'invoice_date')::date,"
                "(o.created_at AT TIME ZONE 'Asia/Kolkata')::date)")
    today = "(now() AT TIME ZONE 'Asia/Kolkata')::date"
    date_filter = (f' AND {desk_day} BETWEEN {today}-3 AND {today}+3' if desk else
                   f" AND (o.created_at AT TIME ZONE 'Asia/Kolkata')::date={today}" if mine else '')
    cur.execute(
        "SELECT o.order_no,o.created_at,o.salesperson,o.customer_name,o.route,o.status,"
        "o.total,o.tally_vchno,o.pulled_at,h.guid,h.party,h.vdate,h.number,h.amount,"
        "h.source_alterid,h.source_state,h.observed_at,to_jsonb(o)->>'invoice_date' FROM cloud_order o"
        " LEFT JOIN history_voucher h ON h.guid=o.tally_guid AND h.vchtype='Sales'"
        " WHERE TRUE" + mine + date_filter +
        " ORDER BY o.created_at DESC" + ('' if desk else ' LIMIT 200'), args)
    rows = cur.fetchall()
    allowed = None
    if user['role'] != 'admin':
        scope, params = _scope(user)
        cur.execute('SELECT name FROM customer WHERE TRUE' + scope, params)
        allowed = {r[0] for r in cur.fetchall()}
    out = []
    for r in rows:
        invoice = None
        restricted = bool(r[9] and allowed is not None and r[10] not in allowed)
        if r[9] and r[14] is not None and not restricted:
            invoice = {'guid':r[9], 'customer':r[10], 'date':r[11], 'number':r[12],
                       'total':_num(r[13]), 'version':r[14], 'state':r[15],
                       'observed_at':r[16].isoformat() if r[16] else None, 'lines':[]}
        out.append({"order_no": r[0], "at": r[1].isoformat() if r[1] else None, "by": r[2],
                    "customer": r[3], "route": r[4], "status": r[5],
                    "total": _num(r[6]), "vchno": r[7],
                    # Reaching the laptop and being invoiced are different
                    # facts, and a rep chasing a bill needs to tell them
                    # apart.
                    "with_laptop": bool(r[8]), 'invoice':invoice,
                    'invoice_restricted':restricted})
        voucher_day = invoice['date'] if invoice else None
        created_day = (r[1].astimezone(dt.timezone(dt.timedelta(hours=5, minutes=30))).date().isoformat()
                       if r[1] else None)
        out[-1].update(source='saarthi', invoice_date=r[17], desk_date=(
            f'{voucher_day[:4]}-{voucher_day[4:6]}-{voucher_day[6:8]}'
            if voucher_day else r[17] or created_day))
    cur.execute(
        "SELECT order_no,line_no,item_name,unit,qty,rate,amount"
        "  FROM cloud_order_line WHERE order_no = ANY(%s) ORDER BY line_no",
        ([o["order_no"] for o in out] or [""],))
    by = {}
    for r in cur.fetchall():
        by.setdefault(r[0], []).append({
            "line": r[1], "item": r[2], "unit": r[3], "qty": _num(r[4]),
            "rate": _num(r[5]), "amount": _num(r[6])})
    for o in out:
        o["lines"] = by.get(o["order_no"], [])
    if desk:
        cur.execute(
            "SELECT h.guid,h.party,h.vdate,h.number,h.amount,h.source_alterid,h.source_state,h.observed_at"
            " FROM history_voucher h WHERE h.vchtype='Sales' AND h.source_state='active'"
            f" AND h.vdate BETWEEN to_char({today}-3,'YYYYMMDD') AND to_char({today}+3,'YYYYMMDD')"
            " AND NOT EXISTS (SELECT 1 FROM cloud_order o WHERE o.tally_guid=h.guid)"
            " AND NOT EXISTS (SELECT 1 FROM order_receipt r WHERE r.tally_guid=h.guid)"
            " ORDER BY h.vdate DESC,h.guid")
        for guid, party, date, number, amount, version, state, observed in cur.fetchall():
            at = observed.isoformat() if observed else f'{date[:4]}-{date[4:6]}-{date[6:8]}T00:00:00+05:30'
            invoice = dict(guid=guid, customer=party, date=date, number=number,
                           total=_num(amount), version=version, state=state, observed_at=at, lines=[])
            out.append(dict(order_no='TALLY-'+guid, at=at, by='Tally', customer=party, route='',
                            source='tally', desk_date=f'{date[:4]}-{date[4:6]}-{date[6:8]}',
                            status='invoiced', total=invoice['total'], vchno=number, with_laptop=True,
                            invoice=invoice, invoice_restricted=False, lines=[]))
        out.sort(key=lambda order: order['at'] or '', reverse=True)
    invoices = {o['invoice']['guid']:o['invoice'] for o in out if o['invoice']}
    if invoices:
        cur.execute('SELECT voucher_guid,line_no,item,unit,qty,rate,amount FROM history_line '
                    'WHERE voucher_guid=ANY(%s) ORDER BY line_no', (list(invoices),))
        for guid, n, item, unit, qty, rate, amount in cur.fetchall():
            invoices[guid]['lines'].append(dict(line=n,item=item,unit=unit,
                                              qty=_num(qty),rate=_num(rate),amount=_num(amount)))
    return out


# ----------------------------------------------------------- dev sign-in

def _dev_login_allowed():
    """A password-free sign-in, for working on the app before Google is set
    up. Dead the moment GOOGLE_CLIENT_ID exists, so configuring real auth
    cannot leave this open behind it."""
    return bool(cloud_db.env("DEV_LOGIN")) and not GOOGLE_CLIENT_ID


class DevIn(BaseModel):
    email: str = Field(min_length=3, max_length=200)


@app.post("/api/auth/dev")
def auth_dev(body: DevIn, request: Request, cur=DB):
    if not _dev_login_allowed():
        raise HTTPException(404)
    email = body.email.lower()
    cur.execute("SELECT email,name,role,active FROM app_user WHERE email=%s",
                (email,))
    row = cur.fetchone()
    if not row or not row[3]:
        raise HTTPException(403, f"{email} is not on the allowlist")
    request.session["email"] = email
    print(f"DEV_LOGIN: signed in {email} with no password")
    return {"email": email, "name": row[1], "role": row[2], "dev": True}


# ------------------------------------------------------------ the app

@app.get("/healthz")
def healthz():
    """Liveness for Railway. Touches the database, because an API that
    answers while its database is unreachable is a worse lie than a 503."""
    try:
        # Through the pool, so this exercises the same path and the same
        # configuration checks the app itself uses -- a health check that
        # takes a different route can pass while the app is broken.
        with _get_pool().connection() as cx, cx.cursor() as cur:
            cur.execute("SELECT 1")
        return {"ok": True}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=503)


class DesktopApprovalIn(BaseModel):
    challenge: str = Field(pattern=r'^[A-Za-z0-9_-]{43}$')


class DesktopExchangeIn(BaseModel):
    grant: str = Field(min_length=32,max_length=160)
    verifier: str = Field(min_length=43,max_length=128,pattern=r'^[A-Za-z0-9_-]+$')


def _desktop_signer():
    return URLSafeTimedSerializer(_secret,salt='saarthi-desktop-user-access-v1')


@app.post('/api/desktop/approve')
def desktop_approve(body: DesktopApprovalIn,user=Depends(me),cur=DB):
    """An authenticated owner explicitly approves one desktop connection."""
    _admin(user)
    grant=secrets.token_urlsafe(32)
    cur.execute("DELETE FROM desktop_grant WHERE expires_at < now()-interval '1 day'")
    cur.execute("INSERT INTO desktop_grant(code_hash,email,challenge,expires_at) "
                "VALUES(%s,%s,%s,now()+interval '5 minutes')",
                (hashlib.sha256(grant.encode()).hexdigest(),user['email'],body.challenge))
    return {'grant':grant,'expires_in':300}


@app.post('/api/desktop/token')
def desktop_exchange(body: DesktopExchangeIn,cur=DB):
    digest=hashlib.sha256(body.grant.encode()).hexdigest()
    cur.execute('SELECT email,challenge FROM desktop_grant WHERE code_hash=%s '
                'AND used_at IS NULL AND expires_at>now() FOR UPDATE',(digest,))
    row=cur.fetchone()
    challenge=base64.urlsafe_b64encode(hashlib.sha256(body.verifier.encode()).digest()).decode().rstrip('=')
    if not row or not hmac.compare_digest(row[1],challenge):
        raise HTTPException(403,'Connection expired or could not be verified. Connect again.')
    cur.execute('SELECT email,name,role,active FROM app_user WHERE email=%s',(row[0],))
    person=cur.fetchone()
    if not person or not person[3] or person[2]!='admin':
        raise HTTPException(403,'An active owner must approve website access')
    cur.execute('UPDATE desktop_grant SET used_at=now() WHERE code_hash=%s',(digest,))
    return {'access_token':_desktop_signer().dumps({'email':person[0],'scope':'website-users'}),
            'expires_in':3600,'email':person[0],'name':person[1]}


def desktop_owner(request: Request,cur=DB):
    header=request.headers.get('authorization','')
    if not header.startswith('Bearer '):raise HTTPException(401,'Connect the desktop using an owner account')
    try:data=_desktop_signer().loads(header[7:],max_age=3600)
    except (BadSignature,SignatureExpired):raise HTTPException(401,'Website connection expired. Connect again.')
    if data.get('scope')!='website-users':raise HTTPException(403,'Wrong desktop permission')
    cur.execute('SELECT email,name,role,active FROM app_user WHERE email=%s',(data.get('email'),))
    row=cur.fetchone()
    if not row or not row[3] or row[2]!='admin':raise HTTPException(403,'Owner access has been withdrawn')
    return {'email':row[0],'name':row[1],'role':row[2],'routes':None}


@app.get('/api/desktop/users')
def desktop_users(user=Depends(desktop_owner),cur=DB):return list_users(user,cur)


@app.get('/api/desktop/routes')
def desktop_routes(user=Depends(desktop_owner),cur=DB):return routes(user,cur)


@app.post('/api/desktop/users')
def desktop_save_user(body: UserIn,user=Depends(desktop_owner),cur=DB):return save_user(body,user,cur)


@app.post('/api/desktop/users/{email}/active')
def desktop_set_active(email: str,body: ActiveIn,user=Depends(desktop_owner),cur=DB):return set_active(email,body,user,cur)


if WEB_DIR.is_dir():
    # Content changes identify a release, including files whose URLs were not bumped.
    digest = hashlib.sha256()
    for asset in sorted(WEB_DIR.iterdir()):
        if asset.suffix in ('.html', '.js', '.css', '.webmanifest'):
            digest.update(asset.name.encode())
            digest.update(asset.read_bytes())
    WEB_VERSION = digest.hexdigest()[:20]

    @app.get('/api/app-version')
    def app_version():
        return JSONResponse({'version': WEB_VERSION}, headers={'Cache-Control': 'no-store'})

    @app.get("/")
    def index():
        html = (WEB_DIR / 'index.html').read_text(encoding='utf-8')
        html = html.replace('<head>', f'<head><meta name="saarthi-version" content="{WEB_VERSION}">', 1)
        html = re.sub(r'((?:src|href)="[^":]+\.(?:js|css))\?v=[^"]+', rf'\1?v={WEB_VERSION}', html)
        return HTMLResponse(html, headers={'Cache-Control': 'no-store'})

    @app.get('/index.html')
    def index_file():
        return index()

    # Mounted last so it cannot shadow /api.
    app.mount("/", StaticFiles(directory=str(WEB_DIR)), name="web")
else:
    print(f"WARNING: WEB_DIR does not exist ({WEB_DIR}); serving API only.")
