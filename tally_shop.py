"""Phase 3: catalogue, cart and orders over the local Tally archive.

Reads the archive built by tally_store; never talks to Tally directly. So it
keeps serving while Tally is closed, which is the normal state outside
11:00-17:30 -- but every stock figure it returns carries the time it was
observed, because serving a stale quantity as if it were current is how a
website oversells.

Pure logic: no HTTP. `tally_desk.py` is the server and UI over this, so
these rules stay testable on their own and the web layer can be swapped for
Next.js/FastAPI later without touching them.

    python tally_shop.py --self-check

Design decisions forced by the data (see ARCHITECTURE.md section 8):

  * GST is ASSUMED ZERO on every line, by instruction. The company has no
    GST configured: no GSTIN on any party, GSTHSNCode = "Not Found" on all
    625 items, and 4,402 sales vouchers with no tax line. Totals here are
    therefore ex-GST. GST_RATE is the single place to change that.
  * Product reference rate: latest confirmed Sales invoice, falling back
    to Tally StandardPrice if never billed. Customer quotes use their own
    last invoice rate where available; submitted draft prices stay frozen.
  * One unit per item: PCS. AdditionalUnits is "Not Applicable" and
    Conversion is 0 on all 625 items, so there is no carton/box pack
    structure to offer.
  * Location comes from the customer's route group, not an address: 489 of
    606 names carry a "(place)" suffix but in inconsistent casing, while
    the 18 route groups are structured and complete.

Stdlib only.
"""
import datetime as dt
import gzip
import json
import re
import sqlite3

import tally_connector as t
import tally_store as s

# By instruction: no GST. Change here if the company is ever configured for
# it -- every total routes through _money().
GST_RATE = 0.0

# A quantity observed longer ago than this is not offered as committable.
STALE_MINUTES = 30

SCHEMA = """
-- Latest confirmed sales invoice price for each product, across customers.
-- Rebuilt from the full local Tally archive; never from a submitted order.
CREATE TABLE IF NOT EXISTS item_invoice_rate (
    item TEXT PRIMARY KEY,
    last_date TEXT,
    last_rate REAL,
    voucher_guid TEXT,
    source_alterid INTEGER
);
CREATE TABLE IF NOT EXISTS shop_order (
    order_no   TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    party_guid TEXT NOT NULL,
    party_name TEXT NOT NULL,
    route      TEXT,
    status     TEXT NOT NULL,     -- see STATUSES
    subtotal   REAL NOT NULL,
    gst        REAL NOT NULL,
    total      REAL NOT NULL,
    stock_asof TEXT,              -- staleness of the stock it was priced on
    note       TEXT,
    tally_guid TEXT,              -- set only once Tally confirms an invoice
    tally_vchno TEXT
);
CREATE INDEX IF NOT EXISTS shop_order_party ON shop_order(party_guid);
CREATE INDEX IF NOT EXISTS shop_order_status ON shop_order(status);

-- Ledgers inside the Sundry Debtors tree that are not shops anyone can
-- order for. There is NO data signal to derive this: 'Unknown Sale' sits
-- directly under Sundry Debtors, has sales history, and ISBILLWISEON is 'No'
-- on all 606 debtors so it cannot discriminate. So the list is explicit and
-- per-installation, seeded with what this company has.
CREATE TABLE IF NOT EXISTS shop_excluded_customer (
    name  TEXT PRIMARY KEY,
    why   TEXT,
    added TEXT NOT NULL
);

-- What each party was LAST actually charged for each item.
--
-- StandardPrice is not the price. Measured across the archive: of 399 items
-- sold to 3+ parties, 396 are billed at DIFFERENT rates to different shops
-- (HIT RED 320ML 155-188, Taj Mahal 1kg 480-610, Moms Magic 70-110). And
-- 75% of 14,431 (party, item) pairs differ from StandardPrice -- median
-- 2.7%, 90th percentile 11.7%. Rates are negotiated per shop and recorded
-- only in the invoices; the PriceLevel field is empty on all 734 ledgers,
-- which is what made this easy to miss.
--
-- So the quote is: last rate to THIS party for THIS item, falling back to
-- StandardPrice when the pair has no history.
CREATE TABLE IF NOT EXISTS party_item_rate (
    party      TEXT NOT NULL,      -- PartyLedgerName, as the voucher carries
    item       TEXT NOT NULL,      -- StockItemName, likewise
    last_date  TEXT,
    last_rate  REAL,
    times      INTEGER NOT NULL,
    item_median REAL,              -- across all parties, for the outlier guard
    PRIMARY KEY (party, item)
);
CREATE INDEX IF NOT EXISTS party_item_rate_party ON party_item_rate(party);

CREATE TABLE IF NOT EXISTS shop_order_line (
    order_no  TEXT NOT NULL,
    line_no   INTEGER NOT NULL,
    item_guid TEXT NOT NULL,
    item_name TEXT NOT NULL,
    unit      TEXT,
    qty       REAL NOT NULL,
    rate      REAL NOT NULL,
    amount    REAL NOT NULL,
    PRIMARY KEY (order_no, line_no)
);
"""

# An order is PROVISIONAL until Tally confirms an invoice. Nothing here may
# ever tell a customer a sale is confirmed -- Phase 5 owns that transition.
#
# 'pending_party' is an order for a shop that has no ledger in Tally yet --
# a rep found it on the road. It is deliberately NOT in OPEN_STATUSES, so
# the invoice batch skips it: trying to bill a party that does not exist
# would fail with "Ledger does not exist" and stop the whole run. The
# laptop moves it to 'placed' only after the ledger is confirmed.
STATUSES = ("draft", "pending_party", "placed", "approved", "rejected",
            "invoiced")
OPEN_STATUSES = ("placed", "approved")


DEFAULT_EXCLUDED = (
    ("Unknown Sale", "catch-all posting ledger, not a shop"),
)


# Columns added after the first release. CREATE TABLE IF NOT EXISTS does
# nothing to a table that already exists, so a column added only to the
# SCHEMA above never appears on anybody's existing database -- and the
# failure is a query error at runtime, not at startup. SQLite has no
# "ADD COLUMN IF NOT EXISTS", so each one is checked first.
ADDED_COLUMNS = (
    ("shop_order", "prospect_id", "TEXT"),
    ("shop_order", "invoice_date", "TEXT"),
    # Why an order could not be billed, and how many times that was tried.
    # Run by hand, the operator sees the refusal and fixes it. Run
    # unattended, an order Tally will never accept -- a deleted ledger, an
    # item renamed since -- would sit at the head of the queue refusing
    # forever and block every order behind it. After INVOICE_MAX_TRIES it
    # is set aside instead, loudly, and the rest keep going.
    ("shop_order", "invoice_error", "TEXT"),
    ("shop_order", "invoice_tries", "INTEGER NOT NULL DEFAULT 0"),
    ("shop_order", "invoice_tried_at", "TEXT"),
)


def _migrate(conn):
    for table, col, decl in ADDED_COLUMNS:
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if col not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def open_db(path=None):
    """Open the archive. Safe to call per request -- SQLite open is cheap,
    and a connection cannot be shared across threads.
    """
    conn = sqlite3.connect(path or s.DB)
    conn.row_factory = sqlite3.Row
    conn.executescript(s.SCHEMA)        # archive tables, if a fresh file
    conn.executescript(SCHEMA)
    from sales_visits import LOCAL_SCHEMA
    conn.executescript(LOCAL_SCHEMA)
    _migrate(conn)
    conn.executemany(
        "INSERT OR IGNORE INTO shop_excluded_customer(name,why,added)"
        " VALUES(?,?,datetime('now'))", DEFAULT_EXCLUDED)
    conn.commit()
    return conn


def excluded_customers(conn):
    return {r["name"] for r in conn.execute(
        "SELECT name FROM shop_excluded_customer")}


def exclude_customer(conn, name, why=None):
    conn.execute("INSERT OR IGNORE INTO shop_excluded_customer"
                 "(name,why,added) VALUES(?,?,datetime('now'))", (name, why))
    conn.commit()


def _money(x):
    return round(float(x) + 0.0, 2)


# ------------------------------------------------- per-party rates, history

# A last rate this far from the item's median across all parties is treated
# as a data error rather than a negotiated price. Multani Hand Wash 750ml
# shows 1.00 to 100.00 in the archive -- quoting the 1.00 would be worse
# than quoting nothing.
RATE_SUSPECT_LO, RATE_SUSPECT_HI = 0.5, 2.0


def rebuild_party_rates(conn, log=print):
    """Materialise (party, item) -> last rate from the voucher archive.

    Materialised, not computed per request: it means decompressing ~5,000
    voucher blobs, which is seconds, not milliseconds.
    """
    rows = conn.execute(
        "SELECT v.vdate, v.party, v.blob, v.guid, v.alterid FROM voucher v JOIN ("
        "  SELECT guid, MAX(alterid) a FROM voucher GROUP BY guid"
        ") x ON v.guid=x.guid AND v.alterid=x.a"
        " LEFT JOIN deleted_voucher d ON d.guid=v.guid"
        " WHERE d.guid IS NULL AND v.vchtype='Sales' AND v.party IS NOT NULL"
        " ORDER BY v.vdate,v.alterid,v.guid").fetchall()

    last, counts, by_item, product_last = {}, {}, {}, {}
    for r in rows:
        rec = json.loads(gzip.decompress(r["blob"]))
        if ((rec.get("ISCANCELLED") or "No").strip() == "Yes"
                or (rec.get("ISOPTIONAL") or "No").strip() == "Yes"):
            continue
        for ln in rec.get("ALLINVENTORYENTRIES.LIST", []):
            item, raw = ln.get("STOCKITEMNAME"), ln.get("RATE")
            if not item or not raw:
                continue
            v, _u = t.rate(raw)
            if not v:
                continue
            key = (r["party"], item)
            last[key] = (r["vdate"], v)          # ordered by date, last wins
            product_last[item] = (r['vdate'], v, r['guid'], r['alterid'])
            counts.setdefault(key, set()).add(r['guid'])
            by_item.setdefault(item, []).append(v)

    med = {}
    for item, vals in by_item.items():
        vals.sort()
        n = len(vals)
        med[item] = (vals[n // 2] if n % 2 else
                     (vals[n // 2 - 1] + vals[n // 2]) / 2)

    conn.execute("DELETE FROM party_item_rate")
    conn.executemany(
        "INSERT INTO party_item_rate(party,item,last_date,last_rate,times,"
        "item_median) VALUES(?,?,?,?,?,?)",
        [(p, i, d, v, len(counts[(p, i)]), med.get(i))
         for (p, i), (d, v) in last.items()])
    conn.execute('DELETE FROM item_invoice_rate')
    conn.executemany('INSERT INTO item_invoice_rate VALUES(?,?,?,?,?)',
                     [(item,*values) for item,values in product_last.items()])
    conn.commit()
    log(f"party rates: {len(last)} (party,item) pairs from {len(rows)} sales")
    return len(last)


def party_rates(conn, party):
    """{item name: {rate, date, times, suspect}} for one party."""
    out = {}
    for r in conn.execute(
            "SELECT item,last_date,last_rate,times,item_median"
            " FROM party_item_rate WHERE party=?", (party,)):
        m = r["item_median"] or 0
        suspect = bool(m and not (RATE_SUSPECT_LO * m <= r["last_rate"]
                                  <= RATE_SUSPECT_HI * m))
        out[r["item"]] = {"rate": _money(r["last_rate"]),
                          "date": r["last_date"], "times": r["times"],
                          "suspect": suspect}
    return out


def party_history(conn, party, limit=50):
    """Recent vouchers for one party, with line items."""
    out = []
    for r in conn.execute(
            "SELECT v.guid,v.vdate,v.vchtype,v.amount,v.blob FROM voucher v"
            " JOIN (SELECT guid, MAX(alterid) a FROM voucher GROUP BY guid) x"
            " ON v.guid=x.guid AND v.alterid=x.a"
            " LEFT JOIN deleted_voucher d ON d.guid=v.guid"
            " WHERE d.guid IS NULL AND v.party=?"
            " ORDER BY v.vdate DESC, v.alterid DESC LIMIT ?", (party, limit)):
        rec = json.loads(gzip.decompress(r["blob"]))
        if (rec.get("ISCANCELLED") or "No").strip() == "Yes":
            continue
        out.append({
            "guid": r["guid"], "date": r["vdate"], "type": r["vchtype"],
            "number": rec.get("VOUCHERNUMBER"),
            "amount": _money(abs(r["amount"] or 0)),
            "lines": [{"item": ln.get("STOCKITEMNAME"),
                       "qty": t.qty(ln.get("ACTUALQTY"))[0],
                       "unit": t.qty(ln.get("ACTUALQTY"))[1],
                       "rate": t.rate(ln.get("RATE"))[0],
                       "amount": t.amount(ln.get("AMOUNT"))}
                      for ln in rec.get("ALLINVENTORYENTRIES.LIST", [])],
        })
    return out


def party_top_items(conn, party, n=20):
    """What this shop orders most -- 'their usual order'."""
    return [dict(r) for r in conn.execute(
        "SELECT item, times, last_rate, last_date FROM party_item_rate"
        " WHERE party=? ORDER BY times DESC, last_date DESC LIMIT ?",
        (party, n))]


# ---------------------------------------------------------------- catalogue

_MRP = re.compile(r"MRP\.?\s*([0-9]+)", re.I)


def _mrp_from_name(name):
    """Retail MRP is embedded in the item name, not a field.

    311 items carry both an MRP in the name and a StandardPrice, and the
    price is consistently below it -- StandardPrice is the distributor rate.
    Shown for reference only; never used for pricing.
    """
    m = _MRP.search(name or "")
    return float(m.group(1)) if m else None


def catalogue(conn, q=None, group=None, in_stock_only=False, limit=500,
              party=None):
    """Items with price, latest observed stock, and that stock's timestamp.

    `party` is the customer's ledger name. Given one, `rate` becomes what
    that shop was LAST charged, because StandardPrice is wrong for 75% of
    (party, item) pairs. `rate_source` says which was used, so the UI can
    show it rather than quoting a negotiated price as if it were a list
    price.
    """
    rows = conn.execute(
        "SELECT m.guid, m.name, m.parent, m.blob FROM master m JOIN ("
        "  SELECT guid, MAX(alterid) a FROM master WHERE objtype='StockItem'"
        "  GROUP BY guid"
        ") x ON m.guid=x.guid AND m.alterid=x.a WHERE m.objtype='StockItem'"
    ).fetchall()
    stock = {r["guid"]: r for r in s.stock_now(conn)}
    reserved = reservations(conn)
    prates = party_rates(conn, party) if party else {}
    invoice_rates = {r['item']:r for r in conn.execute('SELECT * FROM item_invoice_rate')}
    from desktop_workflows import our_prices
    database=conn.execute('PRAGMA database_list').fetchone()[2]
    explicit_prices=our_prices(database) if database else {}

    out = []
    for r in rows:
        rec = json.loads(gzip.decompress(r["blob"]))
        std, unit = _rate_of(rec)
        rate, source, last_date, suspect = None, None, None, False
        latest = invoice_rates.get(r['name'])
        if latest:
            rate, source, last_date = latest['last_rate'], 'invoice', latest['last_date']
        explicit=explicit_prices.get(r['guid'])
        if explicit is not None:
            rate,source,last_date=explicit,'saarthi',None
        pr = prates.get(r["name"])
        if pr and not pr["suspect"]:
            rate, source, last_date = pr["rate"], "party", pr["date"]
        elif pr and pr["suspect"]:
            # Preserve the existing guard for customer-specific outliers.
            rate, source, last_date, suspect = std, "standard-outlier", pr["date"], True
        st = stock.get(r["guid"])
        on_hand = (st["qty"] if st else None)
        held = reserved.get(r["guid"], 0.0)
        item = {
            "guid": r["guid"],
            "name": r["name"],
            "group": r["parent"],
            "unit": unit or (rec.get("BASEUNITS") or "").strip() or None,
            "rate": rate,
            "rate_source": source,          # invoice | party | standard | standard-outlier
            "standard_rate": std,
            "last_billed": last_date,
            "rate_suspect": suspect,
            "mrp": _mrp_from_name(r["name"]),
            "on_hand": on_hand,
            "reserved": held,
            # Saleable deliberately subtracts open-order holds from the
            # observed Tally quantity. It can be negative when staff billed
            # directly in Tally after the snapshot -- surfaced, not clamped.
            "saleable": (None if on_hand is None else _money(on_hand - held)),
            "stock_asof": (st["observed_at"] if st else None),
            "stale": _is_stale(st["observed_at"]) if st else True,
            "sellable": rate is not None,
        }
        out.append(item)

    if q:
        toks = [w for w in re.split(r"\s+", q.strip().lower()) if w]
        out = [i for i in out
               if all(w in (i["name"] or "").lower() for w in toks)]
    if group:
        out = [i for i in out if (i["group"] or "") == group]
    if in_stock_only:
        out = [i for i in out if (i["saleable"] or 0) > 0]
    out.sort(key=lambda i: (i["name"] or "").lower())
    return out[:limit]


def _rate_of(rec):
    """StandardPrice, falling back to nothing.

    ClosingRate is a valuation figure, not a selling price, so it is NOT
    used as a fallback -- quoting cost as price would be worse than
    refusing to quote.
    """

    r, unit = t.rate(rec.get("STANDARDPRICE"))
    if r:
        return _money(r), unit
    return None, None


def _is_stale(observed_at, minutes=STALE_MINUTES):
    if not observed_at:
        return True
    try:
        seen = dt.datetime.fromisoformat(observed_at)
    except ValueError:
        return True
    return (dt.datetime.now(seen.tzinfo) - seen).total_seconds() > minutes * 60


def groups(conn):
    return [r["parent"] for r in conn.execute(
        "SELECT DISTINCT parent FROM master WHERE objtype='StockItem'"
        " AND parent IS NOT NULL ORDER BY parent")]


# ---------------------------------------------------------------- customers

def customers(conn, q=None, route=None, limit=1000):
    """Customers from the archive, with outstanding balance and route.

    Only ledgers under the Sundry Debtors tree, and never cash/bank ledgers
    -- 'Cash In Hand' and 'Unknown Sale' appear as PartyLedgerName on cash
    sales but are not shops anyone can order for.
    """
    rows = conn.execute(
        "SELECT m.guid, m.name, m.parent, m.blob FROM master m JOIN ("
        "  SELECT guid, MAX(alterid) a FROM master WHERE objtype='Ledger'"
        "  GROUP BY guid"
        ") x ON m.guid=x.guid AND m.alterid=x.a WHERE m.objtype='Ledger'"
    ).fetchall()
    debtor_groups = _debtor_groups(conn)
    skip = excluded_customers(conn)
    # Balance comes from the polled snapshot, never the master record: it is
    # a derived value that no AlterID tracks, so a master-stored copy goes
    # stale the moment a receipt is entered.
    bals = s.balances_now(conn)

    out = []
    for r in rows:
        if (r["parent"] or "") not in debtor_groups or r["name"] in skip:
            continue
        rec = json.loads(gzip.decompress(r["blob"]))
        b = bals.get(r["guid"])
        bal = b["closing"] if b else t.amount(rec.get("CLOSINGBALANCE"))
        out.append({
            "guid": r["guid"],
            "name": r["name"],
            "route": r["parent"],
            # Tally sends a debtor balance negative; show what they owe.
            "outstanding": _money(-(bal or 0)),
            "balance_asof": b["observed_at"] if b else None,
            "balance_stale": _is_stale(b["observed_at"]) if b else True,
            "state": (rec.get("LEDSTATENAME") or "").strip() or None,
            "gstin": (rec.get("PARTYGSTIN") or "").strip() or None,
        })
    if q:
        out = [c for c in out if q.strip().lower() in (c["name"] or "").lower()]
    if route:
        out = [c for c in out if c["route"] == route]
    out.sort(key=lambda c: (c["name"] or "").lower())
    return out[:limit]


def _debtor_groups(conn):
    """'Sundry Debtors' plus every group beneath it, from the archive."""
    rows = conn.execute(
        "SELECT m.name, m.parent FROM master m JOIN ("
        "  SELECT guid, MAX(alterid) a FROM master WHERE objtype='Group'"
        "  GROUP BY guid"
        ") x ON m.guid=x.guid AND m.alterid=x.a WHERE m.objtype='Group'"
    ).fetchall()
    kids = {}
    for r in rows:
        kids.setdefault(r["parent"], []).append(r["name"])
    seen, stack = set(), ["Sundry Debtors"]
    while stack:
        g = stack.pop()
        if g in seen:
            continue
        seen.add(g)
        stack.extend(kids.get(g, []))
    return seen


def routes(conn):
    return sorted(g for g in _debtor_groups(conn) if g != "Sundry Debtors")


# ------------------------------------------------------------------- orders

def reservations(conn):
    """Quantity held per item by orders that are placed or approved.

    Holds are released only by reaching 'invoiced' or 'rejected'. Releasing
    on 'placed' would oversell; never releasing would suppress stock.
    """
    out = {}
    for r in conn.execute(
            "SELECT l.item_guid g, SUM(l.qty) q FROM shop_order_line l"
            " JOIN shop_order o ON o.order_no = l.order_no"
            f" WHERE o.status IN ({','.join('?' * len(OPEN_STATUSES))})"
            " GROUP BY l.item_guid", OPEN_STATUSES):
        out[r["g"]] = float(r["q"] or 0)
    return out


def next_order_no(conn, today=None):
    """ORD-YYYYMMDD-NNN. Unique per day, allocated under the caller's txn."""
    day = (today or dt.date.today()).strftime("%Y%m%d")
    r = conn.execute("SELECT order_no FROM shop_order WHERE order_no LIKE ?"
                     " ORDER BY order_no DESC LIMIT 1",
                     (f"ORD-{day}-%",)).fetchone()
    n = int(r["order_no"].rsplit("-", 1)[1]) + 1 if r else 1
    return f"ORD-{day}-{n:03d}"


class OrderError(Exception):
    """The order was refused. The message is shown to the operator."""


def place_order(conn, party_guid, lines, note=None, allow_oversell=False):
    """Validate and store an order. Returns the stored order.

    `lines` is [{"item_guid": ..., "qty": ...}]. Rates come from the
    catalogue, never from the caller: a client-supplied price is a client
    that can set its own price.
    """
    cust = next((c for c in customers(conn) if c["guid"] == party_guid), None)
    if not cust:
        raise OrderError("unknown customer")
    if not lines:
        raise OrderError("order has no lines")

    # Priced for THIS party: a shop that negotiated 165 for HIT RED must not
    # be quoted the 188 another shop pays.
    cat = {i["guid"]: i for i in catalogue(conn, limit=10 ** 6,
                                           party=cust["name"])}
    asof, priced = None, []
    for n, ln in enumerate(lines, 1):
        item = cat.get(ln.get("item_guid"))
        if not item:
            raise OrderError(f"line {n}: unknown item")
        try:
            qty = float(ln.get("qty") or 0)
        except (TypeError, ValueError):
            raise OrderError(f"line {n}: qty is not a number")
        if qty <= 0:
            raise OrderError(f"line {n}: qty must be positive")
        if item["rate"] is None:
            raise OrderError(f"line {n}: {item['name']} has no price in Tally")
        if not allow_oversell and (item["saleable"] or 0) < qty:
            raise OrderError(
                f"line {n}: {item['name']} -- {qty:g} requested, "
                f"{item['saleable'] or 0:g} saleable")
        if item["stock_asof"] and (asof is None or item["stock_asof"] < asof):
            asof = item["stock_asof"]
        amt = _money(qty * item["rate"])
        priced.append((n, item, qty, amt))

    subtotal = _money(sum(p[3] for p in priced))
    gst = _money(subtotal * GST_RATE)
    order_no = next_order_no(conn)
    now = dt.datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO shop_order(order_no,created_at,party_guid,party_name,"
        "route,status,subtotal,gst,total,stock_asof,note)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (order_no, now, party_guid, cust["name"], cust["route"], "placed",
         subtotal, gst, _money(subtotal + gst), asof, note))
    conn.executemany(
        "INSERT INTO shop_order_line(order_no,line_no,item_guid,item_name,"
        "unit,qty,rate,amount) VALUES(?,?,?,?,?,?,?,?)",
        [(order_no, n, i["guid"], i["name"], i["unit"], q, i["rate"], a)
         for n, i, q, a in priced])
    conn.commit()
    return get_order(conn, order_no)


def set_status(conn, order_no, status):
    if status not in STATUSES:
        raise OrderError(f"unknown status {status!r}")
    o = conn.execute("SELECT status FROM shop_order WHERE order_no=?",
                     (order_no,)).fetchone()
    if not o:
        raise OrderError("unknown order")
    # 'invoiced' is Phase 5's to set, and only with a Tally voucher in hand.
    if status == "invoiced":
        raise OrderError("only Tally invoice confirmation may set 'invoiced'")
    conn.execute("UPDATE shop_order SET status=? WHERE order_no=?",
                 (status, order_no))
    conn.commit()
    return get_order(conn, order_no)


def get_order(conn, order_no):
    o = conn.execute("SELECT * FROM shop_order WHERE order_no=?",
                     (order_no,)).fetchone()
    if not o:
        return None
    d = dict(o)
    d["lines"] = [dict(r) for r in conn.execute(
        "SELECT * FROM shop_order_line WHERE order_no=? ORDER BY line_no",
        (order_no,))]
    return d


def list_orders(conn, status=None, limit=200):
    q = "SELECT * FROM shop_order"
    a = ()
    if status:
        q += " WHERE status=?"
        a = (status,)
    q += " ORDER BY order_no DESC LIMIT ?"
    return [dict(r) for r in conn.execute(q, a + (limit,))]


def freshness(conn):
    """What the operator needs to judge whether to trust the numbers."""
    r = conn.execute("SELECT MAX(observed_at) m, COUNT(DISTINCT guid) n"
                     " FROM stock_snapshot").fetchone()
    return {
        "stock_asof": r["m"],
        "stock_items": r["n"],
        "stock_stale": _is_stale(r["m"]),
        "company": s._get(conn, "company"),
        "voucher_watermark": s._get(conn, "voucher_alterid"),
        "gst_rate": GST_RATE,
        "open_orders": len(list_orders(conn, "placed")),
    }


def _self_check():
    import os
    import tempfile

    db = os.path.join(tempfile.mkdtemp(), "shop.db")
    # Copy the live archive so the check runs against real masters.
    import shutil
    if os.path.exists(s.DB):
        shutil.copy(s.DB, db)
    c = open_db(db)

    # --- pure helpers -------------------------------------------------
    assert _mrp_from_name("All Out Refill Mrp 85") == 85.0
    assert _mrp_from_name("ARIEL TOP LOAD 4+2KG MRP1449") == 1449.0
    assert _mrp_from_name("No price here") is None
    assert _money(12.345) == 12.35 and _money(0) == 0.0
    assert _is_stale(None)
    assert _is_stale("2020-01-01T00:00:00")
    assert not _is_stale(dt.datetime.now().isoformat(timespec="seconds"))
    assert GST_RATE == 0.0, "GST assumption changed without updating the UI"
    print("helpers ok (MRP from name, money, staleness, GST=0)")

    items = catalogue(c, limit=10 ** 6)
    if not items:
        print("no archive at", s.DB, "-- skipping data checks")
        return
    cust = customers(c)
    assert items and cust, "catalogue or customer list is empty"
    priced = [i for i in items if i["rate"] is not None]
    assert priced, "no item has a price"
    # Cash/bank ledgers must never appear as orderable customers.
    bad = [x["name"] for x in cust
           if x["name"] in ("Cash In Hand", "Unknown Sale")
           or "Bank Acc" in (x["name"] or "")]
    assert not bad, f"non-shop ledgers offered as customers: {bad}"
    assert all(i["unit"] in (None, "PCS") for i in items), \
        "an item has a unit other than PCS -- pack conversions now exist"
    print(f"data ok: {len(items)} items ({len(priced)} priced), "
          f"{len(cust)} customers, {len(routes(c))} routes")

    # --- ordering rules -----------------------------------------------
    # Seed a synthetic snapshot so the ordering rules are testable without a
    # live Tally -- stock_snapshot is empty whenever the agent has not run.
    if not any(i["on_hand"] is not None for i in items):
        seen = dt.datetime.now().isoformat(timespec="seconds")
        c.executemany(
            "INSERT OR IGNORE INTO stock_snapshot"
            "(observed_at,guid,name,qty,unit,rate) VALUES(?,?,?,?,?,?)",
            [(seen, i["guid"], i["name"], 50.0, "PCS", i["rate"] or 0)
             for i in items if i["rate"] is not None])
        c.commit()
        items = catalogue(c, limit=10 ** 6)
        print("   (seeded a synthetic stock snapshot: Tally is unreachable)")

    # --- per-party rates --------------------------------------------------
    n = rebuild_party_rates(c, log=lambda *a: None)
    assert n > 1000, f"only {n} party/item rates built"
    # StandardPrice must actually disagree with billed rates, or the whole
    # per-party mechanism is pointless and something has gone wrong.
    std = {i["name"]: i["rate"] for i in items if i["rate"]}
    rows = c.execute("SELECT party,item,last_rate FROM party_item_rate"
                     " LIMIT 4000").fetchall()
    cmp_ = [(r["last_rate"], std[r["item"]]) for r in rows if r["item"] in std]
    differ = sum(1 for a, b in cmp_ if abs(a - b) > 0.01)
    assert cmp_ and differ > len(cmp_) * 0.3, \
        f"only {differ}/{len(cmp_)} billed rates differ from StandardPrice"
    print(f"party rates ok: {n} pairs, {100*differ//len(cmp_)}% differ from "
          f"StandardPrice")

    # a party with history must be quoted ITS rate, not the list rate
    r = c.execute("SELECT party,item,last_rate,item_median FROM"
                  " party_item_rate WHERE times >= 2 LIMIT 1").fetchone()
    pcat = {i["name"]: i for i in catalogue(c, limit=10**6, party=r["party"])}
    i = pcat.get(r["item"])
    if i and not i["rate_suspect"]:
        assert i["rate"] == _money(r["last_rate"]), \
            f"party rate not applied: {i['rate']} vs {r['last_rate']}"
        assert i["rate_source"] == "party"
        print(f"   {r['party'][:26]} pays {i['rate']} for "
              f"{r['item'][:26]} (list {i['standard_rate']})")

    # the outlier guard must refuse an absurd last rate
    bad = c.execute(
        "SELECT party,item,last_rate,item_median FROM party_item_rate"
        " WHERE item_median > 0 AND (last_rate < 0.5*item_median"
        " OR last_rate > 2*item_median) LIMIT 1").fetchone()
    if bad:
        bcat = {x["name"]: x for x in catalogue(c, limit=10**6,
                                                party=bad["party"])}
        b = bcat.get(bad["item"])
        assert b and b["rate_suspect"] and b["rate_source"] == "standard-outlier", \
            "outlier rate was quoted instead of being flagged"
        assert b["rate"] == b["standard_rate"], "outlier did not fall back"
        print(f"   outlier guarded: {bad['item'][:30]} last {bad['last_rate']}"
              f" vs median {bad['item_median']} -> quoted {b['rate']}")

    # history and 'their usual order'
    busy = c.execute("SELECT party, COUNT(*) n FROM party_item_rate"
                     " GROUP BY party ORDER BY n DESC LIMIT 1").fetchone()
    hist = party_history(c, busy["party"], limit=5)
    assert hist and any(h["lines"] for h in hist), "history has no line items"
    top = party_top_items(c, busy["party"], n=5)
    assert top and top[0]["times"] >= 1
    print(f"   {busy['party'][:28]}: {len(hist)} recent vouchers, "
          f"top item {top[0]['item'][:28]} x{top[0]['times']}")

    who = cust[0]["guid"]
    pricedcat = {i["guid"]: i for i in
                 catalogue(c, limit=10**6, party=cust[0]["name"])}
    target = next((i for i in pricedcat.values()
                   if i["rate"] is not None and (i["saleable"] or 0) >= 4),
                  None)
    assert target, "no item with price and stock to test against"

    for bad_lines, why in (
            ([], "no lines"),
            ([{"item_guid": "nope", "qty": 1}], "unknown item"),
            ([{"item_guid": target["guid"], "qty": 0}], "zero qty"),
            ([{"item_guid": target["guid"], "qty": -2}], "negative qty"),
            ([{"item_guid": target["guid"], "qty": "abc"}], "non-numeric")):
        try:
            place_order(c, who, bad_lines)
        except OrderError:
            pass
        else:
            raise AssertionError(f"accepted a bad order: {why}")
    try:
        place_order(c, "not-a-customer",
                    [{"item_guid": target["guid"], "qty": 1}])
    except OrderError:
        pass
    else:
        raise AssertionError("accepted an unknown customer")

    # oversell refused, and the message names the shortfall
    try:
        place_order(c, who,
                    [{"item_guid": target["guid"], "qty": 10 ** 9}])
    except OrderError as e:
        assert "saleable" in str(e), e
    else:
        raise AssertionError("oversell was accepted")

    # a good order: priced from the catalogue, not from the caller
    before = target["saleable"]
    o = place_order(c, who, [{"item_guid": target["guid"], "qty": 3}],
                    note="self-check")
    assert o["order_no"].startswith("ORD-")
    assert o["status"] == "placed", "a new order must be provisional"
    assert o["gst"] == 0.0 and o["total"] == o["subtotal"], "GST leaked in"
    assert o["lines"][0]["rate"] == target["rate"], "rate was not re-derived"
    assert o["subtotal"] == _money(3 * target["rate"])
    assert o["stock_asof"], "order did not record stock staleness"

    # the hold must reduce saleable, without touching Tally's own quantity
    after = next(i for i in catalogue(c, limit=10 ** 6)
                 if i["guid"] == target["guid"])
    assert after["reserved"] >= 3, "no reservation recorded"
    assert after["saleable"] == _money(before - 3), \
        f"saleable did not drop by the hold: {before} -> {after['saleable']}"
    assert after["on_hand"] == target["on_hand"], \
        "a website order changed the Tally quantity"

    # order numbers increment within the day
    o2 = place_order(c, who, [{"item_guid": target["guid"], "qty": 1}])
    assert o2["order_no"] != o["order_no"]
    assert int(o2["order_no"].rsplit("-", 1)[1]) == \
        int(o["order_no"].rsplit("-", 1)[1]) + 1

    # rejecting releases the hold; nothing but Tally may set 'invoiced'
    set_status(c, o2["order_no"], "rejected")
    rel = next(i for i in catalogue(c, limit=10 ** 6)
               if i["guid"] == target["guid"])
    assert rel["reserved"] == 3, "rejecting did not release the hold"
    try:
        set_status(c, o["order_no"], "invoiced")
    except OrderError as e:
        assert "Tally" in str(e), e
    else:
        raise AssertionError("'invoiced' was settable without Tally")

    f = freshness(c)
    assert f["gst_rate"] == 0.0 and f["open_orders"] >= 1
    print(f"orders ok: {o['order_no']} {o['total']:.2f}, holds reduce "
          f"saleable, reject releases, 'invoiced' refused")
    c.close()
    print("shop ok")


if __name__ == "__main__":
    _self_check()
