"""Unattended invoicing: the two refusals, told apart.

Running the invoice batch by hand, a person reads the error and decides.
Running it unattended, the code has to decide, and getting it backwards is
expensive in opposite directions:

  * Treat a stuck Tally as the order's fault -> every order in the queue
    burns its attempts and gets set aside. One bad afternoon quarantines
    the whole day's billing.
  * Treat a bad order as a stuck Tally -> one unbillable order sits at the
    head of the queue refusing for ever and nothing behind it is billed.

So this checks, on a scratch database, that an order Tally refuses for its
own reasons is counted and set aside while the queue keeps moving, and
that a Tally refusing everything stops the run and counts against nobody.

    python test_invoice_auto.py

Touches no real order and never contacts Tally -- the refusals are
injected.
"""
import datetime as dt
import pathlib
import tempfile

import tally_invoice as inv
import tally_shop as shop

ok = fail = 0


def check(label, got, want):
    global ok, fail
    good = got == want
    ok, fail = ok + good, fail + (not good)
    print(f"  {'PASS' if good else 'FAIL'}  {label}"
          + ("" if good else f"\n          got {got!r}, want {want!r}"))


def scratch():
    """A throwaway archive with three queued orders."""
    path = pathlib.Path(tempfile.mkdtemp()) / "t.db"
    c = shop.open_db(str(path))
    for n, (no, party) in enumerate([("T-1", "Bad Party"),
                                     ("T-2", "Good Party A"),
                                     ("T-3", "Good Party B")]):
        c.execute(
            "INSERT INTO shop_order(order_no,created_at,party_guid,party_name,"
            "route,status,subtotal,gst,total) VALUES(?,?,?,?,?,'placed',?,0,?)",
            (no, f"2026-10-15 0{n}:00:00", f"g{n}", party, "r", 100, 100))
        c.execute("INSERT INTO shop_order_line(order_no,line_no,item_guid,"
                  "item_name,unit,qty,rate,amount) VALUES(?,1,'g','Item',"
                  "'PCS',2,50,100)", (no,))
    c.commit()
    return c


print("1. an order Tally refuses for its OWN reasons")
c = scratch()
# Tally names a problem with this one order; the other two are fine.
real_send = inv._send
def send_one_bad(order, company):
    if order["order_no"] == "T-1":
        raise inv.InvoiceError(
            "Tally did not create the voucher: {'created': 0, 'errors': 0, "
            "'exceptions': 1, 'messages': [\"Ledger 'Bad Party' does not "
            "exist!\"]}",
            {"created": 0, "errors": 0, "exceptions": 1,
             "messages": ["Ledger 'Bad Party' does not exist!"]})
    return {"created": 1}
inv._send = send_one_bad
inv.t.company = lambda *a, **k: "Demo Co"
inv.find_references = lambda refs, timeout=300: {}
# _verify reads the new vouchers back from Tally and RECORDS them. The
# recording is the part that matters here -- without it an order stays
# queued -- so the stub does the same write the real one does.
def fake_verify(conn, created, log):
    out = []
    for i, no in enumerate(created):
        out.append((no, inv._record(conn, no, {"GUID": f"g{i}",
                                               "VOUCHERNUMBER": str(4000 + i)})))
    return out
inv._verify = fake_verify

res = inv.post_all(c, commit=True, log=lambda *a: None)
check("the run does NOT stop at the bad order", res["failed"], None)
check("the other two were billed", len(res["done"]), 2)
check("the bad one is counted once",
      c.execute("SELECT invoice_tries FROM shop_order WHERE order_no='T-1'")
       .fetchone()["invoice_tries"], 1)
check("and its reason is recorded",
      "does not exist" in (c.execute(
          "SELECT invoice_error FROM shop_order WHERE order_no='T-1'")
          .fetchone()["invoice_error"] or ""), True)
check("it is still queued while attempts remain", inv.pending(c), ["T-1"])

print("\n2. it is set aside after repeated refusals, not retried for ever")
for _ in range(inv.INVOICE_MAX_TRIES):
    inv.post_all(c, commit=True, log=lambda *a: None)
tries = c.execute("SELECT invoice_tries FROM shop_order WHERE order_no='T-1'"
                  ).fetchone()["invoice_tries"]
check(f"attempts stop climbing past {inv.INVOICE_MAX_TRIES}",
      tries >= inv.INVOICE_MAX_TRIES, True)
check("it leaves the queue", inv.pending(c), [])
check("and shows up as needing a person",
      [q["order_no"] for q in inv.quarantined(c)], ["T-1"])
check("the stall counter sees nothing billable",
      inv.waiting(c)["waiting"], 0)
check("but does report the set-aside one", inv.waiting(c)["quarantined"], 1)
c.close()

print("\n3. a Tally refusing EVERYTHING")
c = scratch()
def send_all_stuck(order, company):
    # The real signature of the failure: exceptions, and no message at all.
    raise inv.InvoiceError(
        "Tally did not create the voucher: {'created': 0, 'errors': 0, "
        "'exceptions': 1, 'messages': []}",
        {"created": 0, "errors": 0, "exceptions": 1, "messages": []})
inv._send = send_all_stuck

res = inv.post_all(c, commit=True, log=lambda *a: None)
check("the run stops at the first order", res["failed"], "T-1")
check("and says Tally is stuck", res.get("tally_stuck"), True)
check("two orders are still waiting behind it", res["left"], 3)
check("NOTHING is counted against the orders",
      [r["invoice_tries"] for r in c.execute(
          "SELECT invoice_tries FROM shop_order ORDER BY order_no")],
      [0, 0, 0])
check("so none of them are set aside", inv.quarantined(c), [])
check("all three stay queued for when Tally is back",
      inv.pending(c), ["T-1", "T-2", "T-3"])

print("\n4. the stall counter, which is what makes this visible")
w = inv.waiting(c)
check("counts every waiting order", w["waiting"], 3)
check("and how long the oldest has waited", w["oldest_mins"] is not None, True)
print(f"        oldest has waited {w['oldest_mins']} min")

print("\n5. the same refusal is classified consistently")
stuck = lambda msgs: inv.InvoiceError("x", {"exceptions": 1,
                                            "messages": msgs}).is_tally_stuck
check("silent refusal -> Tally's fault", stuck([]), True)
check("'date is missing' -> Tally's fault",
      stuck(["Voucher date is missing for: 'Sales' voucher ."]), True)
check("a named ledger problem -> the order's fault",
      stuck(["Ledger 'X' does not exist!"]), False)

inv._send = real_send
c.close()
print(f"\n{ok} passed, {fail} failed")
raise SystemExit(1 if fail else 0)
