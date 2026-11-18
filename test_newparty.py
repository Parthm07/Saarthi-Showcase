"""The new-shop journey, end to end, against the live cloud and Tally.

A salesperson finds a shop that has no ledger in Tally, takes an order
there the same minute, the office approves it, the laptop creates the
ledger, and the order becomes billable. Every step is checked, including
the ones that exist to stop something bad:

  * a rep cannot invent a shop on a route that is not theirs
  * a near-duplicate name is caught before a second ledger is created
  * orders for a shop with no ledger are kept OUT of the invoice batch
  * the ledger is read back from Tally before any order is released

Needs the API running, `test.rep@example.com` scoped to Depalpur and Nagda
Route, and Tally open. It creates a real ledger in Tally, prints its name
at the end, and removes everything else it made.

    python test_newparty.py
"""
import base64
import http.cookiejar
import json
import urllib.error
import urllib.request

import itsdangerous

import cloud_db
import tally_cloud as cl
import tally_connector as t
import tally_invoice as inv
import tally_shop as shop

BASE = "http://127.0.0.1:8000"
REP = "test.rep@example.com"
ADMIN = cloud_db.env("TEST_OWNER_EMAIL", "test.owner@example.com")
SHOP = "ZZ Claude Test Kirana (Depalpur)"

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


def drop_ledger():
    """Remove the ledger this test creates, so it can be run again.

    Safe because the test never bills against it -- a ledger with vouchers
    cannot be deleted, and Tally would refuse rather than orphan them. The
    name is read back afterwards rather than trusting the import counters.
    """
    company = t.company()
    if not company:
        return
    cl_xml = f"""<ENVELOPE>
<HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>
<BODY><IMPORTDATA>
<REQUESTDESC><REPORTNAME>All Masters</REPORTNAME>
<STATICVARIABLES><SVCURRENTCOMPANY>{company}</SVCURRENTCOMPANY>
</STATICVARIABLES></REQUESTDESC>
<REQUESTDATA><TALLYMESSAGE xmlns:UDF="TallyUDF">
<LEDGER NAME="{SHOP}" ACTION="Delete"><NAME>{SHOP}</NAME></LEDGER>
</TALLYMESSAGE></REQUESTDATA>
</IMPORTDATA></BODY></ENVELOPE>"""
    t.tally(cl_xml, timeout=120)
    return not t.collection("Ledger", "Name", name="DropChk",
                            where=f'$Name = "{SHOP}"', timeout=120)


def cleanup():
    """Remove everything this test made, in both databases."""
    with cloud_db.connect() as cx, cx.cursor() as cur:
        cur.execute("SELECT id FROM prospect WHERE name LIKE 'ZZ Claude%'")
        ids = [r[0] for r in cur.fetchall()]
        if ids:
            cur.execute("DELETE FROM cloud_order WHERE prospect_id = ANY(%s)",
                        (ids,))
            cur.execute("DELETE FROM prospect WHERE id = ANY(%s)", (ids,))
        cx.commit()
    c = shop.open_db()
    c.execute("DELETE FROM shop_order_line WHERE order_no IN"
              " (SELECT order_no FROM shop_order WHERE party_name LIKE"
              " 'ZZ Claude%')")
    c.execute("DELETE FROM shop_order WHERE party_name LIKE 'ZZ Claude%'")
    c.commit()
    c.close()


cleanup()
drop_ledger()   # so the run is repeatable
rep, admin = client(REP), client(ADMIN)

print("1. a rep proposes a shop")
s, r = call(rep, "/api/prospects", {"name": SHOP, "route": "Depalpur",
                                    "phone": "98260 00000"})
check("accepted", s, 200)
pid = r.get("id")
check("id marks it as not-yet-a-ledger", str(pid).startswith("NEW-"), True)
check("starts pending", r.get("status"), "pending")

print("\n2. guards on proposing")
s, _ = call(rep, "/api/prospects", {"name": SHOP, "route": "Depalpur"})
check("the same name twice is refused", s, 409)
s, r = call(rep, "/api/prospects", {"name": "ZZ Claude Test Kirana",
                                    "route": "Depalpur"})
check("a near-duplicate name is caught", s, 409)
near = (r.get("detail") or {})
check("and it says which shop it looks like",
      SHOP in str(near.get("matches")), True)
s, _ = call(rep, "/api/prospects", {"name": "ZZ Claude Other Shop",
                                    "route": "Dewas Debtors"})
check("a route that is not the rep's is refused", s, 400)

print("\n3. the shop is orderable immediately")
s, d = call(rep, "/api/data")
new = [c for c in d["customers"] if c["id"] == pid]
check("it appears in the rep's own list", len(new), 1)
check("flagged as new", new[0]["isNew"], True)
check("with no invented history", new[0]["outstanding"], None)
item = next(p for p in d["products"] if p["price"])
s, o = call(rep, "/api/orders", {
    "customer_guid": pid, "note": "found on the road",
    "lines": [{"item_guid": item["id"], "qty": 4, "rate": 25}]})
check("an order against it is accepted", s, 200)
check("the total is computed server-side (4 x 25)", o.get("total"), 100.0)
check("but parked until the ledger exists", o.get("status"), "pending_party")
order_no = o.get("order_no")

print("\n4. it must NOT reach the invoice batch")
with cloud_db.connect() as cx, cx.cursor() as cur:
    cl.pull_orders(shop.open_db(), cur, log=lambda *a: None)
    cx.commit()
c = shop.open_db()
row = c.execute("SELECT status,prospect_id FROM shop_order WHERE order_no=?",
                (order_no,)).fetchone()
check("it reached the laptop", bool(row), True)
check("still pending_party there", row["status"], "pending_party")
check("carrying the prospect it came from", row["prospect_id"], pid)
check("the invoice batch does not see it", order_no in inv.pending(c), False)

print("\n5. only an admin decides")
s, _ = call(rep, f"/api/prospects/{pid}/decide", {"approve": True})
check("a rep cannot approve their own shop", s, 403)
s, q = call(admin, "/api/prospects")
mine = [p for p in q if p["id"] == pid]
check("it is in the admin's queue", len(mine), 1)
check("showing the order waiting on it", mine[0]["orders"], 1)
check("and its value", mine[0]["order_value"], 100.0)
check("not in Tally yet", mine[0]["in_tally"], False)
s, _ = call(admin, f"/api/prospects/{pid}/decide",
            {"approve": True, "note": "ok, known shop"})
check("the admin approves", s, 200)
s, _ = call(admin, f"/api/prospects/{pid}/decide", {"approve": True})
check("approving twice is refused", s, 409)

print("\n6. approval alone does not create anything in Tally")
check("no ledger yet",
      len(t.collection("Ledger", "Name", name="Chk1",
                       where=f'$Name = "{SHOP}"', timeout=120)), 0)
check("the order is still parked",
      c.execute("SELECT status FROM shop_order WHERE order_no=?",
                (order_no,)).fetchone()["status"], "pending_party")

print("\n7. the laptop creates the ledger and releases the order")
with cloud_db.connect() as cx, cx.cursor() as cur:
    made = cl.create_approved_parties(c, cur, log=print)
    cx.commit()
check("one ledger created", made, 1)
led = t.collection("Ledger", "Name", "Parent", "GUID", name="Chk2",
                   where=f'$Name = "{SHOP}"', timeout=120)
check("it is really in Tally", len(led), 1)
check("under the approved route, so the rep can still see it",
      led[0].get("PARENT"), "Depalpur")
check("the order is released for billing",
      c.execute("SELECT status FROM shop_order WHERE order_no=?",
                (order_no,)).fetchone()["status"], "placed")
check("and the batch now sees it", order_no in inv.pending(c), True)
check("pointing at the real ledger, not the prospect",
      c.execute("SELECT party_guid FROM shop_order WHERE order_no=?",
                (order_no,)).fetchone()["party_guid"], led[0].get("GUID"))

print("\n8. the rep sees it as a normal shop now")
s, d2 = call(rep, "/api/data")
check("no longer listed as a prospect",
      [x for x in d2["customers"] if x["id"] == pid], [])
check("appears as a real customer once masters sync brings it down",
      any(x["name"] == SHOP for x in d2["customers"]), False)

c.close()
cleanup()

print("\n9. the test leaves nothing behind")
# A test that creates a real ledger in the real books and walks away is
# not a test you can run twice, and it quietly pollutes the chart of
# accounts. Deleting it here is also a check that the delete path works.
check("its ledger is removed from Tally", drop_ledger(), True)
with cloud_db.connect() as cx, cx.cursor() as cur:
    cur.execute("SELECT COUNT(*) FROM prospect WHERE name LIKE 'ZZ Claude%'")
    check("no prospect rows left", cur.fetchone()[0], 0)

print(f"\n{ok} passed, {fail} failed")
raise SystemExit(1 if fail else 0)
