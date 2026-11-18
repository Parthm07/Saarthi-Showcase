"""Exercise the API's access-control boundaries against the live cloud DB.

Needs the API running and a `test.rep@example.com` salesperson scoped to
Depalpur and Nagda Route:

    python tally_cloud.py --add test.rep@example.com --route Depalpur
    python tally_cloud.py --add test.rep@example.com --route "Nagda Route"
    python -m uvicorn api:app --port 8000     # in another window
    python test_api.py

These checks are the reason route scoping can be trusted. Each one asserts
something that, if it broke, would let a salesperson read or bill against
another rep's shops -- which no amount of correct-looking UI would reveal.
It writes real rows to cloud_order; the order number is printed at the end.
"""
import base64
import http.cookiejar
import json
import sys

import itsdangerous

import cloud_db
import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"
ADMIN = cloud_db.env("TEST_OWNER_EMAIL", "test.owner@example.com")

# Tags every row this test writes, so cleanup can find them all
# without guessing from order numbers.
TEST_NOTE = "api self-test"


def client():
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.jar = cj
    return op


def signin(op, email):
    """Put a signed session cookie in the jar, the way Google sign-in would.

    The tests cannot complete a real Google sign-in -- that needs a human
    and a password -- and they must not depend on DEV_LOGIN, which is dead
    the moment GOOGLE_CLIENT_ID is set. So the cookie is minted here
    exactly as Starlette's SessionMiddleware mints it. That leaves only
    Google's own token verification untested, which is Google's code; every
    rule this file exists to check -- the allowlist, route scoping, order
    validation -- lives after that step.

    It goes into the CookieJar rather than a fixed header so the server can
    still clear it: a logout that the client ignores proves nothing.
    """
    # Standard base64 WITH padding, which is what SessionMiddleware emits
    # and what its b64decode requires. Stripping the "=" produces a cookie
    # whose signature verifies and whose payload then fails to decode.
    data = base64.b64encode(json.dumps({"email": email}).encode())
    value = itsdangerous.TimestampSigner(
        str(cloud_db.env("SESSION_SECRET"))).sign(data).decode()
    op.jar.set_cookie(http.cookiejar.Cookie(
        0, "saarthi", value, None, False, "127.0.0.1", False, False,
        "/", True, False, None, True, None, None, {}))
    return call(op, "/api/me")


def call(op, path, body=None, method=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method or ("POST" if data else "GET"),
        headers={"Content-Type": "application/json"})
    try:
        with op.open(req, timeout=120) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw or b"null")
        except ValueError:
            # Not every error comes from our own handlers; a middleware or
            # the server itself can answer in plain text or HTML.
            return e.code, {"detail": raw[:200].decode("utf-8", "replace")}


ok = fail = 0


def check(label, got, want):
    global ok, fail
    good = got == want
    ok, fail = ok + good, fail + (not good)
    print(f"  {'PASS' if good else 'FAIL'}  {label}"
          + ("" if good else f"\n          got {got!r}, want {want!r}"))


print("1. unauthenticated access")
anon = client()
check("/api/me rejects anonymous", call(anon, "/api/me")[0], 401)
check("/api/data rejects anonymous", call(anon, "/api/data")[0], 401)
check("/api/orders rejects anonymous", call(anon, "/api/orders")[0], 401)
check("placing an order rejects anonymous",
      call(anon, "/api/orders", {"customer_guid": "x",
                                 "lines": [{"item_guid": "y", "qty": 1,
                                            "rate": 1}]})[0], 401)
s, cfg = call(anon, "/api/config")
check("/api/config is public", s, 200)
print(f"        google configured: {bool(cfg['google_client_id'])}, "
      f"dev login: {cfg['dev_login']}")

print("\n2. the allowlist")
# A correctly signed cookie for someone not on the allowlist. This is
# the case that matters: Google will happily vouch for any Gmail address.
check("a signed-in stranger is still refused",
      signin(client(), "stranger@gmail.com")[0], 403)
check("the dev bypass is gone now that Google is configured",
      call(anon, "/api/auth/dev", {"email": "test.owner@example.com"})[0], 404)

print("\n3. a salesperson's scope")
rep = client()
s, who = signin(rep, "test.rep@example.com")
check("the test rep signs in", s, 200)
s, me = call(rep, "/api/me")
check("role is salesperson", me["role"], "salesperson")
check("two routes", sorted(me["routes"]), ["Depalpur", "Nagda Route"])

s, d = call(rep, "/api/data")
check("/api/data succeeds", s, 200)
print(f"        {len(d['products'])} products, {len(d['customers'])} customers,"
      f" {len(d['lines'])} sales lines, {len(d['payments'])} payers")
# Counted from the database, not hardcoded: shops get added, and a
# prospect legitimately appears in this list before it has a ledger.
with cloud_db.connect() as cx, cx.cursor() as cur:
    cur.execute("SELECT COUNT(*) FROM customer WHERE route IN"
                " ('Depalpur','Nagda Route')")
    want_rep = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM customer")
    want_all = cur.fetchone()[0]
real = [c for c in d["customers"] if not c.get("isNew")]
check(f"sees only its own {want_rep} routes' shops, not all {want_all}",
      len(real), want_rep)
check("every customer is on one of the rep's routes",
      sorted({c["territory"] for c in d["customers"]}),
      ["Depalpur", "Nagda Route"])
mine = [c for c in real if c["buys"]] or real
check("products are not scoped (whole catalogue)", len(d["products"]), 625)
buys = sum(len(c["buys"]) for c in d["customers"])
print(f"        {buys} reorder lines, "
      f"{sum(1 for c in d['customers'] for b in c['buys'] if b['rate'])}"
      f" with a known rate")
check("payload carries reorder history", buys > 0, True)

print("\n4. ordering inside and outside scope")
mine = real[0]
item = next(p for p in d["products"] if p["price"])
s, r = call(rep, "/api/orders", {
    "customer_guid": mine["id"], "note": TEST_NOTE,
    "lines": [{"item_guid": item["id"], "qty": 3, "rate": 12.5}]})
check("an order for a customer in scope is accepted", s, 200)
check("the server computed the total (3 x 12.50)", r.get("total"), 37.5)
placed = r.get("order_no")
print(f"        {placed} for {r.get('customer')}")

# The security boundary: a guid the rep can obtain but is not allowed to use.
import cloud_db  # noqa: E402

with cloud_db.connect() as cx, cx.cursor() as cur:
    cur.execute("SELECT guid,name,route FROM customer WHERE route NOT IN"
                " ('Depalpur','Nagda Route') LIMIT 1")
    other = cur.fetchone()
s, r = call(rep, "/api/orders", {
    "customer_guid": other[0], "note": TEST_NOTE,
    "lines": [{"item_guid": item["id"], "qty": 1, "rate": 10}]})
check(f"an order for a customer OFF the rep's routes is refused", s, 404)
print(f"        tried {other[1]} ({other[2]}) -> {r.get('detail')}")

print("\n5. input the server must not trust")
s, r = call(rep, "/api/orders", {
    "customer_guid": mine["id"],
    "lines": [{"item_guid": item["id"], "qty": 2, "rate": 100},
              {"item_guid": item["id"], "qty": 2, "rate": 1}]})
check("the same item twice is rejected", s, 422)
s, r = call(rep, "/api/orders", {
    "customer_guid": mine["id"],
    "lines": [{"item_guid": item["id"], "qty": -5, "rate": 10}]})
check("a negative quantity is rejected", s, 422)
s, r = call(rep, "/api/orders", {
    "customer_guid": mine["id"],
    "lines": [{"item_guid": "no-such-guid", "qty": 1, "rate": 10}]})
check("an unknown item is rejected", s, 400)
s, r = call(rep, "/api/orders", {"customer_guid": mine["id"], "lines": []})
check("an empty order is rejected", s, 422)

print("\n6. order visibility")
s, lst = call(rep, "/api/orders")
check("the rep sees their own order", any(o["order_no"] == placed
                                         for o in lst), True)
check("and only their own", {o["by"] for o in lst}, {"test.rep@example.com"})
o = next(o for o in lst if o["order_no"] == placed)
check("not yet invoiced", o["vchno"], None)
check("not yet pulled by the laptop", o["with_laptop"], False)

print("\n7. the admin")
adm = client()
s, _ = signin(adm, ADMIN)
check("the admin signs in", s, 200)
s, me2 = call(adm, "/api/me")
check("routes is null, meaning all", me2["routes"], None)
s, d2 = call(adm, "/api/data")
check("the admin sees every customer",
      len([c for c in d2["customers"] if not c.get("isNew")]), want_all)
s, lst2 = call(adm, "/api/orders")
check("the admin sees the rep's order too",
      any(o["order_no"] == placed for o in lst2), True)

print("\n8. revocation takes effect immediately, cookie still valid")
import tally_cloud as cl  # noqa: E402

cl.users(disable="test.rep@example.com", log=lambda *a: None)
check("a disabled rep is cut off mid-session", call(rep, "/api/data")[0], 403)
cl.users(enable="test.rep@example.com", log=lambda *a: None)
# Revocation destroys the session rather than suspending it, so re-enabling
# does not revive the old cookie -- the rep signs in again. That is the
# behaviour we want: a cookie seen once by a revoked account stays dead.
check("the revoked cookie stays dead after re-enabling",
      call(rep, "/api/data")[0], 401)
rep = client()
signin(rep, "test.rep@example.com")
check("a fresh sign-in works again", call(rep, "/api/data")[0], 200)

print("\n9. sign out")
check("logout succeeds", call(rep, "/api/auth/logout", {})[0], 200)
check("and the session is gone", call(rep, "/api/me")[0], 401)

print("\n10. cleanup")
# The orders above are real rows the laptop would otherwise pull down and
# bill. Removed here so running the tests never puts a fake invoice in
# front of whoever is working the order desk.
with cloud_db.connect() as cx, cx.cursor() as cur:
    cur.execute("DELETE FROM cloud_order WHERE note = %s", (TEST_NOTE,))
    gone = cur.rowcount
    cx.commit()
check("the test's own orders are removed", gone >= 1, True)
with cloud_db.connect() as cx, cx.cursor() as cur:
    cur.execute("SELECT COUNT(*) FROM cloud_order WHERE note = %s", (TEST_NOTE,))
    check("none left behind", cur.fetchone()[0], 0)

print(f"\n{ok} passed, {fail} failed")
raise SystemExit(1 if fail else 0)
