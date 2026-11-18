"""Managing who may sign in, from the owner's screen rather than a terminal.

Adding an address to the allowlist is the ONLY way anyone gets access, so
these checks are about who may change it and what cannot be done by
accident -- above all, the owner locking themselves out of their own
system with nobody able to let them back in.

Needs the API running. Removes everyone it adds.

    python test_people.py
"""
import base64
import http.cookiejar
import json
import urllib.error
import urllib.request

import itsdangerous

import cloud_db

BASE = "http://127.0.0.1:8000"
ADMIN = cloud_db.env("TEST_OWNER_EMAIL", "test.owner@example.com")
REP = "test.rep@example.com"
NEW = "zz.claude.newrep@example.com"

ok = fail = 0


def check(label, got, want):
    global ok, fail
    good = got == want
    ok, fail = ok + good, fail + (not good)
    print(f"  {'PASS' if good else 'FAIL'}  {label}"
          + ("" if good else f"\n          got {got!r}, want {want!r}"))


def client(email):
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    data = base64.b64encode(json.dumps({"email": email}).encode())
    val = itsdangerous.TimestampSigner(
        str(cloud_db.env("SESSION_SECRET"))).sign(data).decode()
    cj.set_cookie(http.cookiejar.Cookie(
        0, "saarthi", val, None, False, "127.0.0.1", False, False, "/",
        True, False, None, True, None, None, {}))
    return op


def call(op, path, body=None):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode() if body is not None
        else None, headers={"Content-Type": "application/json"})
    try:
        with op.open(req, timeout=180) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw or b"null")
        except ValueError:
            return e.code, {"detail": raw[:200].decode("utf-8", "replace")}


def cleanup():
    with cloud_db.connect() as cx, cx.cursor() as cur:
        cur.execute("DELETE FROM app_user WHERE email LIKE 'zz.claude.%'")
        cx.commit()


cleanup()
admin, rep = client(ADMIN), client(REP)

print("1. only an owner may manage people")
check("a salesperson cannot list them", call(rep, "/api/users")[0], 403)
check("a salesperson cannot add one",
      call(rep, "/api/users", {"email": NEW, "routes": ["Depalpur"]})[0], 403)
check("a salesperson cannot see the routes", call(rep, "/api/routes")[0], 403)
check("the owner can list them", call(admin, "/api/users")[0], 200)

print("\n2. the routes the owner picks from are real")
s, routes = call(admin, "/api/routes")
check("routes come back with shop counts", s, 200)
print(f"        {len(routes)} routes, e.g. "
      f"{routes[0]['route']} ({routes[0]['shops']} shops)")
check("every route has at least one shop",
      all(r["shops"] > 0 for r in routes), True)

print("\n3. adding a salesperson")
s, r = call(admin, "/api/users", {
    "email": "  ZZ.Claude.NewRep@Example.com  ", "name": "ZZ New Rep",
    "role": "salesperson", "routes": ["Depalpur"]})
check("accepted", s, 200)
check("the address is normalised, so sign-in matches", r["email"], NEW)
s, people = call(admin, "/api/users")
mine = next((p for p in people if p["email"] == NEW), None)
check("appears in the list", bool(mine), True)
check("with the route given", mine["routes"], ["Depalpur"])
check("and the shops that implies", mine["shops"] > 0, True)
check("active straight away", mine["active"], True)

print("\n4. they can now actually sign in, and see only their route")
newrep = client(NEW)
s, who = call(newrep, "/api/me")
check("the new address is accepted", s, 200)
check("as a salesperson", who["role"], "salesperson")
s, d = call(newrep, "/api/data")
check("and sees only Depalpur",
      sorted({c["territory"] for c in d["customers"] if not c.get("isNew")}),
      ["Depalpur"])

print("\n5. typos are refused rather than silently creating a dead account")
check("a bad route",
      call(admin, "/api/users", {"email": "zz.claude.x@example.com",
                                 "routes": ["Depalpurr"]})[0], 400)
check("a non-address",
      call(admin, "/api/users", {"email": "not-an-email",
                                 "routes": ["Depalpur"]})[0], 422)
check("an unknown role",
      call(admin, "/api/users", {"email": "zz.claude.y@example.com",
                                 "role": "manager"})[0], 422)

print("\n6. the owner cannot lock themselves out")
check("cannot switch off their own access",
      call(admin, f"/api/users/{ADMIN}/active", {"active": False})[0], 400)
check("cannot remove themselves",
      call(admin, f"/api/users/{ADMIN}/remove", {})[0], 400)
check("cannot demote themselves to salesperson",
      call(admin, "/api/users", {"email": ADMIN, "role": "salesperson"})[0],
      400)
# And the rule that matters when there are two owners: the last ACTIVE one
# cannot be switched off either, by anyone.
call(admin, "/api/users", {"email": "zz.claude.owner2@example.com",
                           "role": "admin"})
other = client("zz.claude.owner2@example.com")
check("a second owner can be added", call(other, "/api/me")[1]["role"], "admin")
check("who also cannot switch off the first",
      call(other, f"/api/users/{ADMIN}/active", {"active": False})[0], 200)
check("but the LAST active owner is protected",
      call(other, "/api/users/zz.claude.owner2@example.com/active",
           {"active": False})[0], 400)
# put the first owner back
call(other, f"/api/users/{ADMIN}/active", {"active": True})
check("the first owner is restored", call(admin, "/api/users")[0], 200)

print("\n7. switching access off takes effect at once")
check("the rep works before",
      call(newrep, "/api/data")[0], 200)
call(admin, f"/api/users/{NEW}/active", {"active": False})
check("and is cut off immediately after, cookie still valid",
      call(newrep, "/api/data")[0], 403)

print("\n8. cleanup")
check("removing them works",
      call(admin, f"/api/users/{NEW}/remove", {})[0], 200)
call(admin, "/api/users/zz.claude.owner2@example.com/remove", {})
s, people = call(admin, "/api/users")
check("nobody test-made is left",
      [p for p in people if p["email"].startswith("zz.claude.")], [])
cleanup()

print(f"\n{ok} passed, {fail} failed")
raise SystemExit(1 if fail else 0)
