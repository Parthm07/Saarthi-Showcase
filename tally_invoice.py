"""Create a sales voucher in Tally from a website order.

This is the website-order sales writer. Windows admin entries use tally_admin,
and approved party creation uses tally_cloud. All execute in the serial agent.
The order sales path is deliberately narrow and noisy:

  * The payload is copied from a voucher Tally itself produced, not invented.
  * The order number goes into REFERENCE, and Tally is searched for it
    BEFORE creating anything -- if the import succeeds but the reply is
    lost, a retry finds the existing voucher instead of making a second one.
  * VOUCHERNUMBER is never supplied. Tally owns its own numbering series.
  * Nothing is created unless commit=True is passed explicitly.

No GST lines. This company charges none -- all 4,402 existing sales
vouchers post only to the party ledger and SALES -- so a tax line here would
be the thing that was inconsistent, not its absence.

    python tally_invoice.py                 # dry run against a pending order
    python tally_invoice.py --commit ORD-X  # actually create it
"""
import argparse
import datetime as dt
import sys
from xml.sax.saxutils import escape

import tally_connector as t
import tally_shop as shop

# The ledger every sales line is credited to, read off the existing
# vouchers rather than assumed. Changing this posts revenue somewhere else.
SALES_LEDGER = "SALES"
BATCH = "Primary Batch"
GODOWN = "Main Location"


class InvoiceError(Exception):
    """Refused to create, or Tally rejected it. Nothing was written.

    `result` carries Tally's own counters when it was Tally that refused,
    so callers can tell the two kinds of refusal apart: a refusal naming a
    problem ("Ledger X does not exist") is this order's fault, while a
    refusal with no message at all means Tally has stopped accepting
    vouchers entirely and every other order will fail the same way.
    """

    def __init__(self, msg, result=None):
        super().__init__(msg)
        self.result = result

    @property
    def is_tally_stuck(self):
        r = self.result
        if not r:
            return False
        # Silent refusal, or the misleading date complaint Tally produces
        # once the voucher import path closes. See ARCHITECTURE section 13.
        return bool(r.get("exceptions")) and (
            not r.get("messages")
            or any("date is missing" in (m or "") for m in r["messages"]))


def find_references(refs, timeout=300):
    """{reference: voucher row} for many order numbers in ONE query.

    A Tally collection round-trip costs ~5 seconds whatever it returns, and
    Tally is single-threaded: while it answers, the billing desk's own
    screen is frozen. Asking once for 30 references instead of 30 times is
    the difference between a batch that interrupts the office for five
    minutes and one that interrupts it for five seconds.
    """
    refs = {r for r in refs if r}
    if not refs:
        return {}
    rows = t.collection(
        "Voucher", "Date", "VoucherNumber", "VoucherTypeName", "Amount",
        "Reference", "GUID", name="RefBatch",
        where=f'$Reference != ""', timeout=timeout)
    out = {}
    for r in rows:
        ref = (r.get("REFERENCE") or "").strip()
        if ref in refs:
            out.setdefault(ref, r)
    return out


def find_by_reference(ref, timeout=120):
    """Vouchers already carrying this order number. The duplicate guard.

    Tally has no 'create if absent', so this is checked first and again is
    the only thing standing between a lost reply and a double-billed shop.
    """
    rows = t.collection(
        "Voucher", "Date", "VoucherNumber", "VoucherTypeName", "Amount",
        "Reference", "GUID", name="RefChk",
        where=f'$Reference = "{ref}"', timeout=timeout)
    return [r for r in rows if (r.get("REFERENCE") or "").strip() == ref]


def build_xml(order, company):
    """One <VOUCHER> matching the shape Tally produces for an Item Invoice.

    Signs follow the model voucher exactly: inventory and the SALES
    allocation positive, the party ledger negative.

    The party line is LEDGERENTRIES.LIST and comes AFTER the inventory
    entries. Not ALLLEDGERENTRIES.LIST -- that belongs to Accounting
    Voucher View, and pairing it with OBJVIEW="Invoice Voucher View" makes
    Tally reject the voucher with EXCEPTIONS=1 and no message at all.
    Verified against Tally's own export of voucher 4397 (Sales_4397.xml).
    """
    if not order["lines"]:
        raise InvoiceError("order has no lines")
    day = (order.get('invoice_date') or order['created_at'])[:10].replace('-', '')
    total = 0.0
    items = []
    for l in order["lines"]:
        qty, rate, amt = float(l["qty"]), float(l["rate"]), float(l["amount"])
        if qty <= 0 or rate <= 0:
            raise InvoiceError(f"line {l['line_no']}: qty/rate must be > 0")
        total += amt
        unit = l["unit"] or "PCS"
        items.append(f"""
      <ALLINVENTORYENTRIES.LIST>
        <STOCKITEMNAME>{escape(l['item_name'])}</STOCKITEMNAME>
        <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
        <RATE>{rate:.2f}/{escape(unit)}</RATE>
        <AMOUNT>{amt:.2f}</AMOUNT>
        <ACTUALQTY>{qty:g} {escape(unit)}</ACTUALQTY>
        <BILLEDQTY>{qty:g} {escape(unit)}</BILLEDQTY>
        <BATCHALLOCATIONS.LIST>
          <GODOWNNAME>{escape(GODOWN)}</GODOWNNAME>
          <BATCHNAME>{escape(BATCH)}</BATCHNAME>
          <AMOUNT>{amt:.2f}</AMOUNT>
          <ACTUALQTY>{qty:g} {escape(unit)}</ACTUALQTY>
          <BILLEDQTY>{qty:g} {escape(unit)}</BILLEDQTY>
        </BATCHALLOCATIONS.LIST>
        <ACCOUNTINGALLOCATIONS.LIST>
          <LEDGERNAME>{escape(SALES_LEDGER)}</LEDGERNAME>
          <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
          <AMOUNT>{amt:.2f}</AMOUNT>
        </ACCOUNTINGALLOCATIONS.LIST>
      </ALLINVENTORYENTRIES.LIST>""")
    if abs(total - float(order["total"])) > 0.05:
        raise InvoiceError(
            f"lines total {total:.2f} but the order says {order['total']:.2f}")

    party = escape(order["party_name"])
    return f"""<ENVELOPE>
<HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>
<BODY><IMPORTDATA>
<REQUESTDESC><REPORTNAME>Vouchers</REPORTNAME>
<STATICVARIABLES><SVCURRENTCOMPANY>{escape(company)}</SVCURRENTCOMPANY>
</STATICVARIABLES></REQUESTDESC>
<REQUESTDATA>
<TALLYMESSAGE xmlns:UDF="TallyUDF">
  <VOUCHER VCHTYPE="Sales" ACTION="Create" OBJVIEW="Invoice Voucher View">
    <DATE>{day}</DATE>
    <EFFECTIVEDATE>{day}</EFFECTIVEDATE>
    <VOUCHERTYPENAME>Sales</VOUCHERTYPENAME>
    <PARTYLEDGERNAME>{party}</PARTYLEDGERNAME>
    <PARTYNAME>{party}</PARTYNAME>
    <ISINVOICE>Yes</ISINVOICE>
    <PERSISTEDVIEW>Invoice Voucher View</PERSISTEDVIEW>
    <VCHENTRYMODE>Item Invoice</VCHENTRYMODE>
    <REFERENCE>{escape(order['order_no'])}</REFERENCE>
    <NARRATION>Website order {escape(order['order_no'])}</NARRATION>{''.join(items)}
    <LEDGERENTRIES.LIST>
      <LEDGERNAME>{party}</LEDGERNAME>
      <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
      <ISPARTYLEDGER>Yes</ISPARTYLEDGER>
      <AMOUNT>-{total:.2f}</AMOUNT>
    </LEDGERENTRIES.LIST>
  </VOUCHER>
</TALLYMESSAGE>
</REQUESTDATA>
</IMPORTDATA></BODY>
</ENVELOPE>"""


def _result(root):
    """Tally reports import outcomes as counters, not exceptions."""
    def n(tag):
        e = root.find(f".//{tag}")
        try:
            return int((e.text or "0").strip()) if e is not None else 0
        except ValueError:
            return 0
    errs = [e.text for e in root.iter("LINEERROR") if e.text]
    return {"created": n("CREATED"), "altered": n("ALTERED"),
            "errors": n("ERRORS"), "exceptions": n("EXCEPTIONS"),
            "messages": errs}


def _record(conn, order_no, v):
    """Write Tally's answer against the order. The only place that does."""
    conn.execute("UPDATE shop_order SET tally_guid=?, tally_vchno=?,"
                 " status='invoiced' WHERE order_no=?",
                 (v.get("GUID"), v.get("VOUCHERNUMBER"), order_no))
    conn.commit()
    return v.get("VOUCHERNUMBER")


def _send(order, company):
    """Hand one voucher to Tally. Raises unless Tally says it created it."""
    res = _result(t.tally(build_xml(order, company), timeout=180))
    if res["created"] != 1 or res["errors"] or res["exceptions"]:
        raise InvoiceError(f"Tally did not create the voucher: {res}", res)
    return res


def post(conn, order_no, commit=False, log=print):
    """Create the voucher. Dry run unless commit=True.

    Returns the Tally voucher number and guid on success.
    """
    order = shop.get_order(conn, order_no)
    if not order:
        raise InvoiceError(f"no local order {order_no}")
    if order["tally_vchno"]:
        log(f"already invoiced as {order['tally_vchno']}; nothing to do")
        return {"skipped": True, "vchno": order["tally_vchno"]}

    company = t.company()
    if not company:
        raise t.TallyOffline("no company loaded")

    # Guard first: never create what already exists.
    existing = find_by_reference(order_no)
    if existing:
        e = existing[0]
        log(f"REFUSED: Tally already has voucher {e.get('VOUCHERNUMBER')} "
            f"with reference {order_no}. Recording it instead of creating a "
            f"second one.")
        return {"deduped": True, "vchno": _record(conn, order_no, e)}

    xml = build_xml(order, company)
    if not commit:
        log(f"DRY RUN -- nothing sent. {len(order['lines'])} lines, "
            f"Rs {order['total']:,.2f} to {order['party_name']}")
        return {"dry_run": True, "xml": xml}

    log(f"tally import: {_send(order, company)}")

    # Read back: the import result does not carry the number Tally assigned.
    made = find_by_reference(order_no)
    if not made:
        raise InvoiceError(
            "Tally reported CREATED=1 but the voucher cannot be found by "
            "reference -- do NOT retry blindly, check Tally by hand")
    v = made[0]
    _record(conn, order_no, v)
    log(f"created voucher {v.get('VOUCHERNUMBER')} dated {v.get('DATE')} "
        f"Rs {abs(t.amount(v.get('AMOUNT'))):,.2f}")
    return {"created": True, "vchno": v.get("VOUCHERNUMBER"),
            "guid": v.get("GUID")}


# An order Tally refuses for its OWN reasons -- a ledger deleted since, an
# item renamed -- will be refused again every time. Three attempts, then it
# is set aside so the queue keeps moving, and reported rather than hidden.
# A Tally that is refusing EVERYTHING never counts against this: that is
# not the order's fault and must not quarantine the whole day's work.
INVOICE_MAX_TRIES = 3


def pending(conn, include_stuck=False):
    """Orders waiting to be billed, oldest first.

    Only 'placed' and 'approved' -- a draft was never sent and a rejected
    order was turned down, and neither should reach the books because a
    batch run swept them up. Orders set aside after repeated refusals are
    excluded unless asked for.
    """
    q = ",".join("?" * len(shop.OPEN_STATUSES))
    cap = "" if include_stuck else f" AND invoice_tries < {INVOICE_MAX_TRIES}"
    return [r["order_no"] for r in conn.execute(
        f"SELECT order_no FROM shop_order WHERE tally_vchno IS NULL"
        f" AND status IN ({q}){cap} ORDER BY created_at", shop.OPEN_STATUSES)]


def quarantined(conn):
    """Orders set aside: tried too often, still refused. Need a human."""
    q = ",".join("?" * len(shop.OPEN_STATUSES))
    return [dict(r) for r in conn.execute(
        f"SELECT order_no,party_name,total,invoice_error,invoice_tries"
        f" FROM shop_order WHERE tally_vchno IS NULL AND status IN ({q})"
        f" AND invoice_tries >= {INVOICE_MAX_TRIES} ORDER BY created_at",
        shop.OPEN_STATUSES)]


def waiting(conn):
    """How many orders are queued, and how long the oldest has waited.

    The number that makes a stall visible. An invoice run that quietly
    stops is invisible; 'oldest order waiting 240 minutes' is not.
    """
    r = conn.execute(
        "SELECT COUNT(*) n, MIN(created_at) oldest FROM shop_order"
        " WHERE tally_vchno IS NULL AND status IN (?,?)"
        f" AND invoice_tries < {INVOICE_MAX_TRIES}", shop.OPEN_STATUSES
    ).fetchone()
    mins = None
    if r["oldest"]:
        try:
            then = dt.datetime.fromisoformat(r["oldest"][:26])
            mins = max(0, int((dt.datetime.now() - then).total_seconds() // 60))
        except ValueError:
            mins = None
    return {"waiting": r["n"], "oldest_mins": mins,
            "quarantined": len(quarantined(conn))}


def _verify(conn, created, log):
    """Read back everything just created, in one query, and record it.

    The import reply says a voucher was created but not what number Tally
    gave it, so this is both how the number is learned and the proof that
    the voucher is really in the books. Anything sent but not found is
    reported loudly and left un-recorded -- marking an order invoiced when
    the voucher cannot be found is the one outcome worth crashing over.
    """
    if not created:
        return []
    made = find_references(created)
    out = []
    for no in created:
        v = made.get(no)
        if v:
            out.append((no, _record(conn, no, v)))
        else:
            log(f"  !! {no}: Tally said it created this but it cannot be "
                f"found by reference.\n     NOT recorded -- check Tally by "
                f"hand before re-running.")
    nums = [n for _, n in out]
    if nums:
        log(f"  vouchers {', '.join(nums)}")
    return out


def post_all(conn, commit=False, log=print, *, batch_size=None, notify_cloud=True):
    """Invoice everything pending. STOPS at the first refusal.

    Stopping is the point. When Tally enters the state where it declines
    voucher imports (ARCHITECTURE section 13) it declines all of them, so
    continuing would turn one restart-and-retry into sixty identical
    failures with the real message buried in the middle. One failure, one
    clear reason, nothing half-done.

    Orders already in Tally are not re-created: post() checks REFERENCE
    first, so a batch interrupted halfway is safe to simply run again.
    """
    todo = pending(conn)[:batch_size]
    if not todo:
        log("nothing to invoice -- every order is already billed")
        return {"done": [], "failed": None, "left": 0}
    company = t.company()
    if not company:
        raise t.TallyOffline("no company loaded")
    log(f"{len(todo)} order(s) to invoice"
        + ("" if commit else "  [DRY RUN -- nothing will be written]"))

    # One lookup for the whole batch, not two per order. At 27 orders a day
    # that is the difference between freezing the billing desk's Tally for
    # five minutes and for five seconds.
    already = find_references(todo)
    if already:
        log(f"  ({len(already)} already in Tally -- recording, not resending)")

    done, created, bad = [], [], []
    for n, no in enumerate(todo, 1):
        o = shop.get_order(conn, no)
        head = (f"  {n}/{len(todo)}  {no}  {o['party_name'][:34]:36}"
                f" Rs {o['total']:>10,.2f}")
        if no in already:
            # Created by an earlier run whose reply was lost. Record it;
            # never send it twice.
            done.append((no, _record(conn, no, already[no])))
            log(f"{head}  already in Tally as {done[-1][1]}")
            continue
        try:
            if not commit:
                build_xml(o, company)        # validates without sending
                log(f"{head}  ready")
                done.append((no, None))
                continue
            _send(o, company)
        except (InvoiceError, t.TallyError, t.TallyOffline) as e:
            stuck = getattr(e, "is_tally_stuck", False) or isinstance(
                e, (t.TallyOffline, t.TallyBusy))
            log(f"{head}  REFUSED")
            if stuck:
                # Tally is refusing everything. This is NOT the order's
                # fault, so it must not count against its attempts --
                # otherwise one bad afternoon quarantines the whole day.
                log(f"\n  Stopped at {no}. {len(todo) - n + 1} order(s) still "
                    f"waiting, nothing half-written.\n  {e}")
                log("\n  This is Tally refusing ALL voucher imports, not a "
                    "problem with this\n  order -- see ARCHITECTURE section "
                    "13. Restart TallyPrime, reopen\n  the company, and run "
                    "this again. The orders are safe where they\n  are and "
                    "nothing needs re-keying.")
                _verify(conn, created, log)
                return {"done": done, "failed": no, "error": str(e),
                        "left": len(todo) - n + 1, "tally_stuck": True}
            # This order's own problem. Count the attempt and move on, so
            # one unbillable order cannot hold up everything behind it.
            conn.execute(
                "UPDATE shop_order SET invoice_tries=invoice_tries+1,"
                " invoice_error=?, invoice_tried_at=? WHERE order_no=?",
                (str(e)[:500], dt.datetime.now().isoformat(timespec="seconds"),
                 no))
            conn.commit()
            tries = conn.execute(
                "SELECT invoice_tries FROM shop_order WHERE order_no=?",
                (no,)).fetchone()["invoice_tries"]
            bad.append((no, str(e)))
            log(f"    {e}")
            log(f"    attempt {tries} of {INVOICE_MAX_TRIES}"
                + (" -- set aside, needs a person" if tries >= INVOICE_MAX_TRIES
                   else " -- will try again next run") + "; continuing")
            continue
        created.append(no)
        log(f"{head}  sent")

    done += _verify(conn, created, log)
    if bad:
        log(f"\n  {len(bad)} order(s) Tally refused for their own reasons "
            f"(the rest were billed):")
        for no, why in bad:
            log(f"    {no}: {why[:110]}")

    if commit and done and notify_cloud:
        # Tell the reps' phones now rather than waiting up to a minute for
        # the agent's next push. Best effort: a cloud hiccup must not make a
        # run that billed correctly look like it failed.
        try:
            import tally_cloud as cloud
            if cloud.env("CLOUD_DB_URL"):
                with cloud.connect() as cx:
                    with cx.cursor() as cur:
                        cloud.push_confirmations(conn, cur, log=log)
                    cx.commit()
        except Exception as e:
            log(f"  (vouchers are created; telling the cloud failed: {e}. "
                f"The agent's next push will do it.)")
    log(f"\n{len(done)} order(s) {'invoiced' if commit else 'ready to invoice'}"
        + ("" if commit else " -- re-run with --commit to write them"))
    return {"done": done, "failed": None, "left": 0}


def _selfcheck():
    """Pin the payload shape. Tally rejects a wrong shape with EXCEPTIONS=1
    and no message, so nothing here can be caught by reading an error."""
    order = {"order_no": "T-1", "party_name": "P & Sons", "total": 760.0,
             "created_at": "2026-10-13T10:00:00", "lines": [
                 {"line_no": 1, "item_name": "A", "qty": 2, "rate": 75.0,
                  "amount": 150.0, "unit": "PCS"},
                 {"line_no": 2, "item_name": "B", "qty": 2, "rate": 305.0,
                  "amount": 610.0, "unit": None}]}
    x = build_xml(order, "Demo Co")
    # The party line lives in LEDGERENTRIES.LIST, after the inventory.
    assert "<ALLLEDGERENTRIES" not in x
    assert x.index("ALLINVENTORYENTRIES.LIST") < x.index("LEDGERENTRIES.LIST")
    # Party negative, inventory and SALES positive.
    assert "<AMOUNT>-760.00</AMOUNT>" in x and "<AMOUNT>610.00</AMOUNT>" in x
    assert x.count("<LEDGERNAME>SALES</LEDGERNAME>") == 2
    assert "<RATE>305.00/PCS</RATE>" in x       # unit defaults to PCS
    assert "P &amp; Sons" in x and "<REFERENCE>T-1</REFERENCE>" in x
    assert "<VOUCHERNUMBER>" not in x           # Tally owns its numbering
    # Totals must agree, and junk lines must be refused outright.
    for bad in ({**order, "total": 999.0},
                {**order, "lines": [{**order["lines"][0], "qty": 0}]},
                {**order, "lines": []}):
        try:
            build_xml(bad, "Demo Co")
            raise AssertionError(f"accepted a bad order: {bad['total']}")
        except InvoiceError:
            pass
    # Telling the two refusals apart decides what a person does next:
    # restart Tally, or go and fix one order. Getting it backwards sends
    # them down the wrong path at the worst moment.
    stuck = lambda res: InvoiceError("x", res).is_tally_stuck
    assert stuck({"exceptions": 1, "messages": []}), "silent refusal"
    assert stuck({"exceptions": 1,
                  "messages": ["Voucher date is missing for: 'Sales' "]})
    assert not stuck({"exceptions": 1,
                      "messages": ["Ledger 'X' does not exist!"]})
    assert not stuck({"exceptions": 0, "messages": []})
    assert not InvoiceError("no local order").is_tally_stuck
    print("selfcheck ok")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("order_no", nargs="?")
    ap.add_argument("--selfcheck", action="store_true")
    ap.add_argument("--all", action="store_true",
                    help="invoice every pending order, stopping at the first"
                         " refusal")
    ap.add_argument("--commit", action="store_true",
                    help="actually write to Tally")
    a = ap.parse_args()
    if a.selfcheck:
        _selfcheck()
        sys.exit(0)
    c = shop.open_db()
    if a.all:
        res = post_all(c, commit=a.commit)
        c.close()
        # Non-zero on a refusal, so this can be watched by something other
        # than a person reading the output.
        sys.exit(1 if res["failed"] else 0)
    no = a.order_no
    if not no:
        r = c.execute("SELECT order_no FROM shop_order WHERE tally_vchno IS"
                      " NULL ORDER BY order_no DESC LIMIT 1").fetchone()
        if not r:
            print("no un-invoiced order to post")
            sys.exit(0)
        no = r["order_no"]
    out = post(c, no, commit=a.commit)
    if out.get("dry_run"):
        print(out["xml"])
    c.close()
