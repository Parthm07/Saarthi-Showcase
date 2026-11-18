"""Push the website's slice of the local archive to Postgres, pull orders back.

Only a subset goes up. The local archive is ~108 MB; the website needs about
2 MB of it. What is deliberately withheld:

  * purchase vouchers and supplier ledgers -- 604 purchases, Rs 2.56 crore,
    59 suppliers. The site is sales-only, so pushing them would put buying
    details in the cloud for no functional gain. Only the latest purchase-rate
    fact per product is published separately for authenticated Owner access.
  * the 783-field raw voucher archive.
  * the 524-field ledger records; the site needs five columns.

The cloud is the part we do not control, so it holds the minimum needed to
take an order and nothing else.

    python tally_cloud.py --schema       # create/verify tables
    python tally_cloud.py --push         # one full push
    python tally_cloud.py --self-check

Needs CLOUD_DB_URL in .env (see .env.example). Uses psycopg (v3).
"""
import argparse
import datetime as dt
import json
import os
import pathlib
import sys
import time
from xml.sax.saxutils import escape

import psycopg

import tally_connector as t
import tally_insights as ins
import tally_shop as shop
import tally_store as s

from saarthi_runtime import data_directory
HERE = data_directory()

# How far back the website can see. Matches the local backfill cutoff.
HISTORY_MONTHS = 6

# Only the sales side of a party's history leaves the laptop.
#
# 12 parties are BOTH customer and supplier, so an unfiltered history carries
# their Purchase vouchers -- i.e. what we pay them. A whitelist, not a
# blacklist: a voucher type nobody has thought about yet must default to
# staying local. Payment is excluded for the same reason (it settles the
# purchase side for those 12); a salesperson needs what the shop bought and
# what it paid, which is Sales plus Receipt plus returns.
HISTORY_TYPES = ("Sales", "Receipt", "Credit Note")

# Schema and connection live in cloud_db, shared with the website API.
from cloud_db import (SCHEMA, MIGRATE, env, connect,  # noqa: F401
                      create_schema)


# ------------------------------------------------------------------ push

def _cutoff():
    return (dt.date.today()
            - dt.timedelta(days=int(HISTORY_MONTHS * 30.44))).strftime("%Y%m%d")


# A push whose local snapshot is this much smaller than what the cloud
# already holds is treated as a degraded source, not as deletions.
MIN_PUSH_RATIO = 0.5


class RefusedPush(Exception):
    """The local snapshot looked too empty to trust. Nothing was changed."""


def _guard(cur, table, incoming, log):
    """Refuse to let a thin snapshot delete a healthy cloud table.

    push_fast deletes rows that are no longer present locally, which is
    correct for a real deletion and catastrophic for a local database that
    is empty because Tally had no company loaded. Observed: a self-check run
    against a fresh temp DB pushed 0 items and 0 customers and wiped 625 and
    605 live rows. Same principle as reconcile's MIN_LIVE_RATIO.
    """
    cur.execute(f"SELECT COUNT(*) FROM {table}")
    have = cur.fetchone()[0]
    if have and incoming < MIN_PUSH_RATIO * have:
        raise RefusedPush(
            f"refusing to push {incoming} rows to {table} when it holds "
            f"{have} -- the local archive looks degraded, not pruned. "
            f"Nothing was changed.")
    return have


def apply_website_rates(local, cur, database):
    """Persist website edits before publishing and acknowledge only those revisions."""
    from cloud_db import pending_our_rates_available
    from desktop_archive import Archive
    from desktop_workflows import Workflows

    if not database or not pending_our_rates_available(cur):
        return []
    cur.execute('SELECT guid,price,revision FROM pending_our_rate')
    pending = cur.fetchall()
    if not pending:
        return []
    cur.execute('SELECT company FROM sync_status WHERE id=1')
    if cur.fetchone() != (s._get(local, 'company'),):
        raise RefusedPush('Cannot import website rates into a different Tally company')
    products = {row[0] for row in local.execute("SELECT DISTINCT guid FROM master WHERE objtype='StockItem'")}
    confirmed = []
    with Workflows(Archive(database)).db() as conn:
        for guid, price, revision in pending:
            if guid not in products:
                continue
            # A cloud rollback may replay a revision. Leave any newer local edit intact.
            saved = conn.execute('INSERT INTO website_rate_receipt VALUES(?,?) '
                                 'ON CONFLICT(guid) DO UPDATE SET revision=excluded.revision '
                                 'WHERE website_rate_receipt.revision<>excluded.revision',
                                 (guid, str(revision)))
            if saved.rowcount:
                conn.execute('INSERT INTO product_our_rate VALUES(?,?) '
                             'ON CONFLICT(guid) DO UPDATE SET price=excluded.price', (guid, float(price)))
            confirmed.append((guid, revision))
    return confirmed


def push_fast(local, cur, log=print):
    """Stock, balances and freshness. Small enough to send whole, often.

    No diffing: the payload is ~30 KB, so a full snapshot every cycle is
    simpler than incremental and cannot drift.
    """
    database = local.execute('PRAGMA database_list').fetchone()[2]
    # Serialize catalogue snapshots and their website-rate acknowledgements.
    cur.execute("SELECT pg_advisory_xact_lock(hashtext('saarthi:website-our-rates'))")
    items = shop.catalogue(local, limit=10 ** 6)
    custs = shop.customers(local, limit=10 ** 6)
    # Checked BEFORE anything is written, so a refusal leaves the cloud
    # exactly as it was.
    _guard(cur, "item", len(items), log)
    _guard(cur, "customer", len(custs), log)
    confirmed_rates = apply_website_rates(local, cur, database)
    if confirmed_rates:
        items = shop.catalogue(local, limit=10 ** 6)
    from desktop_workflows import website_hidden_products
    hidden = website_hidden_products(database) if database else set()
    items = [item for item in items if item['guid'] not in hidden]
    met = ins.metrics(local)
    # PostgreSQL item.rate is numeric(12,2). One oversized rate must not
    # interrupt the entire stock, order and image sync transaction.
    def website_rate(item):
        rate=item["rate"]
        if rate is not None and abs(rate)>=9_999_999_999.995:
            log(f'catalogue rate for {item["guid"]} exceeds cloud column precision; publishing without rate')
            return None
        return rate

    cur.execute("CREATE TEMP TABLE t_item (LIKE item) ON COMMIT DROP")
    cur.executemany(
        "INSERT INTO t_item(guid,name,grp,unit,rate,mrp,on_hand,saleable,"
        "stock_asof,daily,cover_days) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        [(i["guid"], i["name"], i["group"], i["unit"], website_rate(i),
          i["mrp"], i["on_hand"], i["saleable"], i["stock_asof"],
          (met.get(i["name"]) or {}).get("daily"),
          (met.get(i["name"]) or {}).get("cover_days"))
         for i in items])
    cur.execute("""
        INSERT INTO item SELECT * FROM t_item
        ON CONFLICT (guid) DO UPDATE SET
          name=EXCLUDED.name, grp=EXCLUDED.grp, unit=EXCLUDED.unit,
          rate=EXCLUDED.rate, mrp=EXCLUDED.mrp, on_hand=EXCLUDED.on_hand,
          saleable=EXCLUDED.saleable, stock_asof=EXCLUDED.stock_asof,
          daily=EXCLUDED.daily, cover_days=EXCLUDED.cover_days""")
    cur.execute("DELETE FROM item WHERE guid NOT IN (SELECT guid FROM t_item)")
    if confirmed_rates:
        cur.executemany('DELETE FROM pending_our_rate WHERE guid=%s AND revision=%s', confirmed_rates)

    cur.execute("CREATE TEMP TABLE t_cust (LIKE customer) ON COMMIT DROP")
    rowsc = []
    for c in custs:
        pr = ins.profile(local, c["name"]) or {}
        ag = ins.ageing(local, c["name"]) or {}
        rowsc.append((
            c["guid"], c["name"], c["route"], c["outstanding"], c["state"],
            c["balance_asof"], c["gstin"], pr.get("aov"), pr.get("last_days"),
            pr.get("churn"), pr.get("growth"),
            json.dumps(pr.get("trend")) if pr.get("trend") else None,
            # overdue = anything past 30 days in the reconstruction
            (ag.get("d31_60", 0) or 0) + (ag.get("d61_90", 0) or 0)
            + (ag.get("d90_plus", 0) or 0) if ag else None,
            ag.get("oldest_days"), ag.get("d0_30"), ag.get("d31_60"),
            ag.get("d61_90"), ag.get("d90_plus"), ag.get("gap"),
            ag.get("last_receipt")))
    cur.executemany(
        "INSERT INTO t_cust(guid,name,route,outstanding,state,balance_asof,"
        "gstin,aov,last_days,churn,growth,trend,overdue,overdue_days,"
        "d0_30,d31_60,d61_90,d90_plus,ageing_gap,last_receipt)"
        " VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        rowsc)
    cur.execute("""
        INSERT INTO customer SELECT * FROM t_cust
        ON CONFLICT (guid) DO UPDATE SET
          name=EXCLUDED.name, route=EXCLUDED.route,
          outstanding=EXCLUDED.outstanding, state=EXCLUDED.state,
          balance_asof=EXCLUDED.balance_asof, gstin=EXCLUDED.gstin,
          aov=EXCLUDED.aov, last_days=EXCLUDED.last_days,
          churn=EXCLUDED.churn, growth=EXCLUDED.growth,
          trend=EXCLUDED.trend,
          overdue=EXCLUDED.overdue, overdue_days=EXCLUDED.overdue_days,
          d0_30=EXCLUDED.d0_30, d31_60=EXCLUDED.d31_60,
          d61_90=EXCLUDED.d61_90, d90_plus=EXCLUDED.d90_plus,
          ageing_gap=EXCLUDED.ageing_gap,
          last_receipt=EXCLUDED.last_receipt""")
    cur.execute("DELETE FROM customer WHERE guid NOT IN"
                " (SELECT guid FROM t_cust)")

    from product_media import push as push_images
    push_images(local, cur, log=log)
    from purchase_costs import push as push_purchase_costs
    push_purchase_costs(local,cur,log=log)
    push_party_rates(local, cur, log=log)
    push_recommendations(local, cur, log=log)
    push_daily_sales(local, cur, log=log)
    f = shop.freshness(local)
    b = billing_state(local)
    cur.execute("""
        INSERT INTO sync_status(id,company,stock_asof,voucher_watermark,
                                pushed_at,orders_waiting,oldest_wait_mins,
                                orders_set_aside,invoice_blocked_since,
                                invoice_error)
        VALUES (1,%s,%s,%s,now(),%s,%s,%s,%s,%s)
        ON CONFLICT (id) DO UPDATE SET
          company=EXCLUDED.company, stock_asof=EXCLUDED.stock_asof,
          voucher_watermark=EXCLUDED.voucher_watermark,
          pushed_at=EXCLUDED.pushed_at,
          orders_waiting=EXCLUDED.orders_waiting,
          oldest_wait_mins=EXCLUDED.oldest_wait_mins,
          orders_set_aside=EXCLUDED.orders_set_aside,
          invoice_blocked_since=EXCLUDED.invoice_blocked_since,
          invoice_error=EXCLUDED.invoice_error""",
        (f["company"], f["stock_asof"], f["voucher_watermark"],
         b["waiting"], b["oldest_mins"], b["set_aside"],
         b["blocked_since"] or None, b["error"] or None))
    log(f"push fast: {len(items)} items, {len(custs)} customers"
        + (f", {b['waiting']} order(s) awaiting billing" if b["waiting"] else ""))
    return len(items), len(custs)


def billing_state(local):
    """How billing is going, for the owner's screen.

    A stalled invoice run has no symptom of its own -- orders just stay
    "at the office", which is what they say when everything is fine. This
    is the difference between noticing at 9am and hearing it from a shop
    at 4pm.
    """
    import tally_invoice as inv
    import tally_store as st
    w = inv.waiting(local)
    return {"waiting": w["waiting"], "oldest_mins": w["oldest_mins"],
            "set_aside": w["quarantined"],
            "blocked_since": st._get(local, "invoice_blocked_since") or "",
            "error": st._get(local, "invoice_error") or ""}


def _guard_fact(local,cur,table,incoming,log):
    try:
        return _guard(cur,table,incoming,log)
    except RefusedPush:
        # All valid purchases can legitimately disappear after cancellation.
        # Permit a verified rebuild to shrink, not an empty/unbound install.
        company=s._get(local,'company')
        ready=(company and s._get(local,'facts_built_on') and
            s._get(local,'facts_built_at_alterid')==(s._get(local,'voucher_alterid') or '')
            and local.execute('SELECT COUNT(*) FROM voucher').fetchone()[0]>0)
        if not ready:
            raise
        cur.execute('SELECT company FROM sync_status WHERE id=1')
        if cur.fetchone()!=(company,):
            raise RefusedPush('Fact rebuild belongs to a different cloud company')
        log(f'{table}: accepting reduced fact set after validated local rebuild')


def push_party_rates(local, cur, log=print):
    """Publish changed customer prices on the regular cycle, not hourly.

    Standard item prices are independent. The caller commits customer rates
    with the rest of its snapshot so readers cannot see a partial rate table.
    """
    company = s._get(local, 'company')
    if not company:
        raise RefusedPush('Cannot publish customer prices from an unbound archive')
    cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                ('saarthi-party-rates:' + company,))
    rates = local.execute(
        'SELECT party,item,last_rate,last_date,times,item_median FROM party_item_rate').fetchall()
    _guard_fact(local,cur,'party_rate',len(rates),log)
    cur.execute('SELECT party,item,last_rate,last_date,times,item_median FROM party_rate')
    def normalized(row):
        return (float(row[2]) if row[2] is not None else None, row[3], row[4],
                float(row[5]) if row[5] is not None else None)
    known = {(r[0],r[1]):normalized(r) for r in cur.fetchall()}
    incoming = {(r[0],r[1]):normalized(r) for r in rates}
    changed = [tuple(r) for r in rates if known.get((r[0],r[1])) != incoming[(r[0],r[1])]]
    removed = list(known.keys() - incoming.keys())
    if changed:
        cur.executemany(
            'INSERT INTO party_rate(party,item,last_rate,last_date,times,item_median) '
            'VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(party,item) DO UPDATE SET '
            'last_rate=EXCLUDED.last_rate,last_date=EXCLUDED.last_date,'
            'times=EXCLUDED.times,item_median=EXCLUDED.item_median', changed)
    if removed:
        cur.executemany('DELETE FROM party_rate WHERE party=%s AND item=%s', removed)
    if changed or removed:
        log(f'customer prices: {len(changed)} updated, {len(removed)} removed')
    return len(changed) + len(removed)


def push_recommendations(local, cur, log=print):
    """Publish fresh reorder/frequency facts without waiting for hourly history."""
    if not s._get(local, 'company'):
        raise RefusedPush('Cannot publish recommendations from an unbound archive')
    cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                ('saarthi-party-buy:' + s._get(local, 'company'),))
    columns = 'party,item,cyc_lo,cyc_hi,last_days,typical_qty,freq_n,freq_m,is_core'
    rows = [tuple(r[:-1]) + (bool(r[-1]),) for r in local.execute(
        'SELECT ' + columns + ' FROM party_buy').fetchall()]
    _guard_fact(local,cur,'party_buy',len(rows),log)
    cur.execute('SELECT ' + columns + ' FROM party_buy')
    def normalized(row):
        return tuple(float(v) if v is not None else None for v in row[2:])
    known = {(r[0],r[1]):normalized(r) for r in cur.fetchall()}
    incoming = {(r[0],r[1]):normalized(r) for r in rows}
    changed = [r for r in rows if known.get(r[:2]) != incoming[r[:2]]]
    removed = list(known.keys() - incoming.keys())
    if changed:
        cur.executemany('INSERT INTO party_buy(' + columns + ') '
            'VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(party,item) DO UPDATE SET '
            'cyc_lo=EXCLUDED.cyc_lo,cyc_hi=EXCLUDED.cyc_hi,last_days=EXCLUDED.last_days,'
            'typical_qty=EXCLUDED.typical_qty,freq_n=EXCLUDED.freq_n,'
            'freq_m=EXCLUDED.freq_m,is_core=EXCLUDED.is_core', changed)
    if removed:
        cur.executemany('DELETE FROM party_buy WHERE party=%s AND item=%s', removed)
    if changed or removed:
        log(f'recommendations: {len(changed)} updated, {len(removed)} removed')
    return len(changed) + len(removed)


def push_daily_sales(local, cur, log=print):
    """Small corrected daily totals, retained independently of invoice details."""
    if not s._get(local,'facts_built_on'):
        log('daily totals: deferred until the first local fact rebuild')
        return 0
    company = s._get(local,'company')
    if not company:
        raise RefusedPush('Cannot publish daily facts from an unbound archive')
    cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                ('saarthi-daily-sales:' + company,))
    rows = [tuple(r) for r in local.execute(
        'SELECT vdate,party,salesperson,invoices,amount FROM daily_sales')]
    cur.execute('SELECT vdate,party,salesperson,invoices,amount FROM daily_sales')
    known = {r[:3]:(r[3],round(float(r[4]),2)) for r in cur.fetchall()}
    incoming = {r[:3]:(r[3],round(float(r[4]),2)) for r in rows}
    changed = [r for r in rows if known.get(r[:3]) != incoming[r[:3]]]
    if changed:
        cur.executemany('INSERT INTO daily_sales(vdate,party,salesperson,invoices,amount) '
            'VALUES(%s,%s,%s,%s,%s) ON CONFLICT(vdate,party,salesperson) DO UPDATE SET '
            'invoices=EXCLUDED.invoices,amount=EXCLUDED.amount',changed)
    removed = list(known.keys()-incoming.keys())
    if removed:
        cur.executemany('DELETE FROM daily_sales WHERE vdate=%s AND party=%s AND salesperson=%s',removed)
    return len(changed)+len(removed)


def push_slow(local, cur, log=print, *, include_history=True):
    """Per-party rates and 6-month history with line items.

    Heavier (~2 MB), changes slowly, so this runs on its own cadence rather
    than every minute.
    """
    rates = local.execute(
        "SELECT party,item,last_rate,last_date,times,item_median"
        " FROM party_item_rate").fetchall()
    from product_media import push as push_images
    push_images(local, cur, log=log)
    from purchase_costs import push as push_purchase_costs
    push_purchase_costs(local,cur,log=log)
    push_party_rates(local, cur, log=log)

    buys = local.execute(
        "SELECT party,item,cyc_lo,cyc_hi,last_days,typical_qty,freq_n,"
        "freq_m,is_core FROM party_buy").fetchall()
    push_recommendations(local, cur, log=log)
    push_daily_sales(local, cur, log=log)

    # History is versioned and published on the fast cycle too. Never
    # truncate it here: a slow run must not discard newer invoice metadata.
    import tally_updates
    changed, line_count = tally_updates.publish(local, cur, log=log) if include_history else (0, 0)
    log(f"push slow: {len(rates)} rates, {len(buys)} reorder lines, "
        f"{changed} changed vouchers, {line_count} changed lines")
    return len(rates), changed, line_count


def pull_orders(local, cur, log=print, *, include_visits=True, batch_size=None):
    """Bring new website orders down into the local shop_order tables.

    Commit locally before acknowledging receipt. A failed cloud
    acknowledgement can replay the same IDs without duplicating orders.
    """
    from sales_visits import pull as pull_visits
    if include_visits:
        pull_visits(local,cur)
    cur.execute(
        "SELECT order_no,created_at,salesperson,customer_guid,customer_name,"
        "route,status,subtotal,gst,total,stock_asof,note,prospect_id,to_jsonb(o)->>'invoice_date'"
        " FROM cloud_order o WHERE pulled_at IS NULL ORDER BY created_at,order_no LIMIT %s",
        (batch_size,))
    orders = cur.fetchall()
    if not orders:
        return 0
    order_numbers = [row[0] for row in orders]
    cur.execute("SELECT order_no,line_no,item_guid,item_name,unit,qty,rate,amount"
                " FROM cloud_order_line WHERE order_no=ANY(%s) ORDER BY order_no,line_no",
                (order_numbers,))
    lines = {}
    for no, line, guid, name, unit, qty, rate, amount in cur.fetchall():
        lines.setdefault(no, []).append((no, line, guid, name, unit, float(qty), float(rate), float(amount)))
    with local:
        _store_pulled_orders(local, orders, lines)
    cur.execute("UPDATE cloud_order SET pulled_at=now() WHERE order_no=ANY(%s) AND pulled_at IS NULL",
                (order_numbers,))
    log(f"pull: {len(orders)} new order(s)")
    return len(orders)


def _store_pulled_orders(local, orders, lines):
    for o in orders:
        (no, created, who, cguid, cname, route, status, sub, gst, tot,
         asof, note, prospect, *dates) = o
        # Older orders use the order's creation day in India as the voucher date.
        when = created.astimezone(dt.timezone(dt.timedelta(hours=5, minutes=30))).replace(tzinfo=None) if (
            created and created.tzinfo) else created
        local.execute(
            "INSERT OR IGNORE INTO shop_order(order_no,created_at,party_guid,"
            "party_name,route,status,subtotal,gst,total,stock_asof,note,"
            "prospect_id,invoice_date) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (no, str(when), cguid, cname, route, status, float(sub),
             float(gst), float(tot), str(asof) if asof else None,
             f"[{who}] {note or ''}".strip(), prospect, dates[0] if dates else None))
        local.executemany(
            "INSERT OR IGNORE INTO shop_order_line(order_no,line_no,item_guid,"
            "item_name,unit,qty,rate,amount) VALUES(?,?,?,?,?,?,?,?)",
            lines.get(no, []))


def _ledger_xml(name, parent, company, phone=None, address=None):
    """One Sundry Debtor, under the route group the office approved.

    Routes ARE Tally ledger groups -- customer.route is the ledger's parent
    -- so putting the right group here is what makes the shop visible to
    the rep who found it. Get it wrong and nobody can see their own shop.
    """
    extra = ""
    if address:
        extra += (f"<ADDRESS.LIST TYPE=\"String\"><ADDRESS>"
                  f"{escape(address)}</ADDRESS></ADDRESS.LIST>")
    if phone:
        extra += f"<LEDGERPHONE>{escape(phone)}</LEDGERPHONE>"
    return f"""<ENVELOPE>
<HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>
<BODY><IMPORTDATA>
<REQUESTDESC><REPORTNAME>All Masters</REPORTNAME>
<STATICVARIABLES><SVCURRENTCOMPANY>{escape(company)}</SVCURRENTCOMPANY>
</STATICVARIABLES></REQUESTDESC>
<REQUESTDATA>
<TALLYMESSAGE xmlns:UDF="TallyUDF">
  <LEDGER NAME="{escape(name)}" ACTION="Create">
    <NAME>{escape(name)}</NAME>
    <PARENT>{escape(parent)}</PARENT>
    <ISBILLWISEON>No</ISBILLWISEON>
    <AFFECTSSTOCK>No</AFFECTSSTOCK>
    <OPENINGBALANCE>0</OPENINGBALANCE>{extra}
  </LEDGER>
</TALLYMESSAGE>
</REQUESTDATA>
</IMPORTDATA></BODY>
</ENVELOPE>"""


def create_approved_parties(local, cur, log=print):
    """Make the Tally ledgers the office has approved, then free their orders.

    This is the only place a customer ledger is created, and it runs on the
    laptop because only the laptop can reach Tally -- and only it can prove
    the ledger exists before telling anyone the order can be billed.

    Order of operations matters. The ledger is created, then READ BACK, and
    only then are the orders moved off 'pending_party'. Releasing them on
    the strength of the import reply alone would queue invoices against a
    ledger that might not be there, which is how the batch run stops dead.
    """
    cur.execute("SELECT id,name,route,phone,address FROM prospect"
                " WHERE status='approved' AND tally_guid IS NULL"
                " ORDER BY created_at")
    todo = cur.fetchall()
    if not todo:
        return 0
    company = t.company()
    if not company:
        raise t.TallyOffline("no company loaded")
    made = 0
    for pid, name, route, phone, address in todo:
        if not route:
            log(f"party: {name!r} has no route -- cannot place it in Tally")
            continue
        existing = t.collection("Ledger", "Name", "Parent", "GUID",
                                name="PartyChk",
                                where=f'$Name = "{name}"', timeout=120)
        if not existing:
            res = t.tally(_ledger_xml(name, route, company, phone, address),
                          timeout=120)
            n = res.find(".//CREATED")
            errs = [e.text for e in res.iter("LINEERROR") if e.text]
            if errs or not (n is not None and (n.text or "0").strip() == "1"):
                log(f"party: Tally refused {name!r}: {errs or 'no reason given'}")
                continue
            existing = t.collection("Ledger", "Name", "Parent", "GUID",
                                    name="PartyChk2",
                                    where=f'$Name = "{name}"', timeout=120)
            if not existing:
                log(f"party: Tally said it created {name!r} but it cannot be "
                    f"read back -- left pending, check Tally by hand")
                continue
        guid = existing[0].get("GUID")
        cur.execute("UPDATE prospect SET tally_guid=%s, linked_at=now()"
                    " WHERE id=%s", (guid, pid))
        # Point the orders at the real ledger and let them be billed.
        cur.execute(
            "UPDATE cloud_order SET customer_guid=%s, status='placed'"
            " WHERE prospect_id=%s AND status='pending_party'", (guid, pid))
        freed = cur.rowcount
        local.execute(
            "UPDATE shop_order SET party_guid=?, status='placed'"
            " WHERE prospect_id=? AND status='pending_party'", (guid, pid))
        local.commit()
        log(f"party: created {name!r} under {route!r}"
            + (f", {freed} order(s) released for billing" if freed else ""))
        made += 1
    return made


def push_confirmations(local, cur, log=print):
    """Tell the cloud which orders Tally has actually billed.

    Without this the rep's phone shows an order as 'placed' forever, even
    though it is invoiced and the stock has moved -- so they re-ask the
    office, or worse, place it again. The laptop is the only writer of these
    three columns (the website owns status up to 'placed'), so there is no
    conflict to resolve: last write wins and the last writer is Tally.
    """
    rows = local.execute(
        "SELECT order_no,tally_guid,tally_vchno,status FROM shop_order"
        " WHERE tally_vchno IS NOT NULL").fetchall()
    if not rows:
        log("confirm: nothing invoiced yet")
        return 0
    company=s._get(local,'company')
    if not company:
        raise RefusedPush('Cannot confirm invoices from an unbound archive')
    cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                ('saarthi-history:'+company,))
    cur.executemany(
        "UPDATE cloud_order SET tally_guid=%s, tally_vchno=%s, status=%s,"
        " completed_at=COALESCE(completed_at,now())"
        " WHERE order_no=%s AND (tally_vchno IS DISTINCT FROM %s OR completed_at IS NULL)",
        [(r["tally_guid"], r["tally_vchno"], r["status"] or "invoiced",
          r["order_no"], r["tally_vchno"]) for r in rows])
    n = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    log(f"confirm: {n} order(s) marked invoiced in the cloud")
    return n


def push_all(local=None, log=print):
    """One full cycle. Opens its own connections; safe to call from the agent."""
    own = local is None
    local = local or shop.open_db()
    try:
        with connect() as cx:
            with cx.cursor() as cur:
                create_schema(cur, log=lambda *a: None)
                push_fast(local, cur, log=log)
                push_slow(local, cur, log=log)
                create_approved_parties(local, cur, log=log)
                push_confirmations(local, cur, log=log)
                n = pull_orders(local, cur, log=log)
            cx.commit()
        return n
    finally:
        if own:
            local.close()


def _self_check():
    url = env("CLOUD_DB_URL")
    if not url:
        print("CLOUD_DB_URL not set -- skipping live cloud checks.")
        print("Copy .env.example to .env and paste the Railway PUBLIC url.")
        return
    masked = url.split("@")[-1] if "@" in url else "?"
    print(f"connecting to ...@{masked}")
    local = shop.open_db()
    t0 = time.time()

    # The guard must refuse a thin snapshot BEFORE testing the happy path,
    # because this check itself once wiped 625 items and 605 customers by
    # pushing from an empty temp database.
    import tempfile
    empty = shop.open_db(os.path.join(tempfile.mkdtemp(), "empty.db"))
    with connect() as cx:
        with cx.cursor() as cur:
            create_schema(cur, log=lambda *a: None)
            cur.execute("SELECT COUNT(*) FROM item")
            if cur.fetchone()[0] >= 2:
                try:
                    push_fast(empty, cur, log=lambda *a: None)
                except RefusedPush:
                    pass
                else:
                    raise AssertionError(
                        "an empty snapshot was pushed -- it would delete the "
                        "live cloud tables")
        cx.rollback()
    empty.close()

    with connect() as cx:
        with cx.cursor() as cur:
            create_schema(cur)
            ni, nc = push_fast(local, cur)
            nr, nv, nl = push_slow(local, cur)
        cx.commit()

        with cx.cursor() as cur:
            # what landed must match what we sent
            cur.execute("SELECT COUNT(*) FROM item")
            assert cur.fetchone()[0] == ni, "item count mismatch"
            cur.execute("SELECT COUNT(*) FROM customer")
            assert cur.fetchone()[0] == nc, "customer count mismatch"
            cur.execute("SELECT COUNT(*) FROM party_rate")
            assert cur.fetchone()[0] == nr, "party_rate count mismatch"
            cur.execute("SELECT COUNT(*) FROM history_line")
            assert cur.fetchone()[0] == nl, "history_line count mismatch"

            # the withheld things must NOT be there
            cur.execute("SELECT table_name FROM information_schema.tables"
                        " WHERE table_schema='public'")
            tables = {r[0] for r in cur.fetchall()}
            for forbidden in ("voucher_raw", "master", "supplier",
                              "party_balance"):
                assert forbidden not in tables, \
                    f"{forbidden} reached the cloud; it must stay local"
            # Whitelist, verified from the other end: anything outside
            # HISTORY_TYPES is a leak of the purchase side.
            cur.execute("SELECT DISTINCT vchtype FROM history_voucher")
            leaked = {r[0] for r in cur.fetchall()} - set(HISTORY_TYPES)
            assert not leaked, f"voucher types reached the cloud: {leaked}"

            cur.execute("SELECT company,stock_asof,voucher_watermark"
                        " FROM sync_status WHERE id=1")
            st = cur.fetchone()
            assert st and st[0], "sync_status not written"
            print(f"sync_status: company={st[0]!r} stock_asof={st[1]} "
                  f"watermark={st[2]}")

            # idempotent: a second push changes the counts not at all
            with cx.cursor() as c2:
                push_fast(local, c2, log=lambda *a: None)
            cx.commit()
            cur.execute("SELECT COUNT(*) FROM item")
            assert cur.fetchone()[0] == ni, "second push duplicated items"

        # an order written by the 'website' must come down exactly once
        with cx.cursor() as cur:
            c0 = shop.customers(local)[0]
            i0 = next(i for i in shop.catalogue(local, limit=10 ** 6)
                      if i["rate"])
            no = f"TEST-{dt.datetime.now():%H%M%S}"
            cur.execute(
                "INSERT INTO cloud_order(order_no,salesperson,customer_guid,"
                "customer_name,route,subtotal,total) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s)",
                (no, "selfcheck@example.com", c0["guid"], c0["name"],
                 c0["route"], 100, 100))
            cur.execute(
                "INSERT INTO cloud_order_line(order_no,line_no,item_guid,"
                "item_name,unit,qty,rate,amount)"
                " VALUES(%s,1,%s,%s,%s,2,50,100)",
                (no, i0["guid"], i0["name"], i0["unit"]))
        cx.commit()
        with cx.cursor() as cur:
            assert pull_orders(local, cur, log=lambda *a: None) == 1
            assert pull_orders(local, cur, log=lambda *a: None) == 0, \
                "an order was pulled twice"
        cx.commit()
        o = shop.get_order(local, no)
        assert o and o["lines"], "pulled order did not land locally"
        assert "selfcheck@example.com" in (o["note"] or ""), \
            "salesperson audit trail lost"
        with cx.cursor() as cur:
            cur.execute("DELETE FROM cloud_order WHERE order_no=%s", (no,))
        cx.commit()
        local.execute("DELETE FROM shop_order_line WHERE order_no=?", (no,))
        local.execute("DELETE FROM shop_order WHERE order_no=?", (no,))
        local.commit()

    print(f"cloud ok in {time.time()-t0:.0f}s: {ni} items, {nc} customers, "
          f"{nr} rates, {nv} vouchers, {nl} lines; "
          f"purchases and suppliers withheld; pull is exactly-once")
    local.close()


def users(add=None, name=None, role="salesperson", routes=None, drop=None,
          disable=None, enable=None, log=print):
    """Manage who may sign in to the website, and what they can see.

    The allowlist is the whole access-control story: Google says who
    someone is, this table says whether they may use the app. Nobody can
    self-register, which is the point -- there are four salespeople, not an
    open sign-up.

    Routes are matched against customer.route exactly, so a typo silently
    yields a rep who can see nothing. Anything passed here is checked
    against the routes that actually exist and refused if it does not match.
    """
    with connect() as cx:
        with cx.cursor() as cur:
            create_schema(cur, log=lambda *a: None)
            if add:
                add = add.lower()
                if role not in ("admin", "salesperson"):
                    raise SystemExit("role must be admin or salesperson")
                # Validated before anything is written, so a refusal says
                # only that -- rather than printing "allowed" for a change
                # that is about to be rolled back.
                if routes:
                    cur.execute("SELECT DISTINCT route FROM customer"
                                " WHERE route IS NOT NULL")
                    real = {r[0] for r in cur.fetchall()}
                    bad = [r for r in routes if r not in real]
                    if bad:
                        raise SystemExit(
                            "refused, nothing changed. These routes do not "
                            "exist, so the rep would see nothing:\n  "
                            + "\n  ".join(bad)
                            + "\n\nroutes that do exist:\n  "
                            + "\n  ".join(sorted(real)))
                cur.execute(
                    "INSERT INTO app_user(email,name,role,active)"
                    " VALUES(%s,%s,%s,TRUE) ON CONFLICT (email) DO UPDATE SET"
                    " name=COALESCE(EXCLUDED.name,app_user.name),"
                    " role=EXCLUDED.role, active=TRUE",
                    (add, name, role))
                log(f"allowed {add} as {role}")
                if routes:
                    cur.executemany(
                        "INSERT INTO user_route(email,route) VALUES(%s,%s)"
                        " ON CONFLICT DO NOTHING", [(add, r) for r in routes])
                    log(f"  + {len(routes)} route(s)")
            for email, active in ((disable, False), (enable, True)):
                if email:
                    cur.execute("UPDATE app_user SET active=%s WHERE email=%s",
                                (active, email.lower()))
                    log(f"{'enabled' if active else 'disabled'} "
                        f"{email.lower()}" if cur.rowcount else "no such user")
            if drop:
                cur.execute("DELETE FROM app_user WHERE email=%s",
                            (drop.lower(),))
                log(f"removed {drop.lower()}" if cur.rowcount
                    else "no such user")
            cur.execute(
                "SELECT u.email,u.name,u.role,u.active,"
                "  (SELECT COUNT(*) FROM user_route r WHERE r.email=u.email),"
                "  (SELECT COUNT(*) FROM customer c WHERE u.role='admin'"
                "     OR c.route IN (SELECT route FROM user_route r"
                "                     WHERE r.email=u.email))"
                " FROM app_user u ORDER BY u.role,u.email")
            rows = cur.fetchall()
        cx.commit()
    log(f"\n{'email':34} {'role':12} {'on':3} {'routes':>6} {'parties':>8}")
    for e, nm, rl, ac, nr, nc in rows:
        log(f"{e:34} {rl:12} {'yes' if ac else 'NO ':3} {nr:>6} {nc:>8}")
    if not rows:
        log("(nobody is allowed to sign in yet)")
    return rows


def list_routes(log=print):
    """The routes a rep can be assigned to -- Tally's own ledger groups."""
    with connect() as cx, cx.cursor() as cur:
        cur.execute("SELECT route,COUNT(*) FROM customer WHERE route IS NOT"
                    " NULL GROUP BY route ORDER BY COUNT(*) DESC")
        rows = cur.fetchall()
    for r, n in rows:
        log(f"{n:>5}  {r}")
    log(f"{len(rows)} routes")
    return rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--schema", action="store_true")
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--self-check", action="store_true")
    ap.add_argument("--users", action="store_true",
                    help="list who may sign in to the website")
    ap.add_argument("--routes", action="store_true",
                    help="list the routes a salesperson can be assigned")
    ap.add_argument("--add", metavar="EMAIL", help="allow this Google address")
    ap.add_argument("--name", help="display name, with --add")
    ap.add_argument("--role", default="salesperson",
                    choices=("admin", "salesperson"))
    ap.add_argument("--route", action="append", default=[], metavar="ROUTE",
                    help="repeatable; the routes this person covers")
    ap.add_argument("--disable", metavar="EMAIL",
                    help="revoke access, keep the audit trail")
    ap.add_argument("--enable", metavar="EMAIL")
    ap.add_argument("--remove", metavar="EMAIL")
    a = ap.parse_args()
    if a.routes:
        list_routes()
    elif a.users or a.add or a.disable or a.enable or a.remove:
        users(add=a.add, name=a.name, role=a.role, routes=a.route,
              drop=a.remove, disable=a.disable, enable=a.enable)
    elif a.self_check:
        _self_check()
    elif a.schema:
        with connect() as cx:
            with cx.cursor() as cur:
                create_schema(cur)
            cx.commit()
    elif a.push:
        print("pulled", push_all())
    else:
        ap.print_help()
