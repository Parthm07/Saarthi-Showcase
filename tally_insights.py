"""Derived facts the UI needs that Tally does not store: run rates, reorder
cycles, purchase trends, churn.

Everything here is computed from the voucher archive. None of it needs a
field anyone has to type into Tally, which is the point -- these are the
inputs the order-taking UI's recommendation engine runs on, and they were
simulated in the prototype.

Quantities are in Tally's BASE UNIT (PCS for all 625 items). There is no
case/pack conversion anywhere: AdditionalUnits is "Not Applicable" and
Conversion is 0 on every item, so inventing cases would mean inventing
numbers.

    python tally_insights.py        # rebuild + self-check

Maps onto the prototype's data model:

    PRODUCTS[].daily      -> item_metric.daily
    PRODUCTS[].cover      -> item_metric.cover_days
    CUSTOMERS[].aov       -> party_profile.aov
    CUSTOMERS[].lastDays  -> party_profile.last_days
    CUSTOMERS[].trend[6]  -> party_profile.trend
    CUSTOMERS[].churn     -> party_profile.churn
    CUSTOMERS[].buys[]    -> party_buy rows (cyc, last, qty, freq, core)
"""
import datetime as dt
import gzip
import json
import statistics

import tally_connector as t
import tally_shop as shop

# Run rate is measured over this window. Long enough to survive a slow week,
# short enough that a discontinued line stops looking live.
RATE_WINDOW_DAYS = 90

# "Core" is RELATIVE to the party's own basket -- their most-taken lines --
# not an absolute share of invoices.
#
# Measured: of 3,763 repeat lines, 6 appear in >=80% of a party's recent
# invoices and only 277 in >=40%; the median is about 20%. These shops order
# a varying basket each visit rather than a fixed staple list, so an
# absolute threshold flags almost nothing (6 of 236 parties at 0.8).
#
# freq_n/freq_m stays the honest raw count. UI copy must read from it
# ("taken in 4 of the last 10 bills") and must NOT say "usually in every
# order", which this data does not support.
CORE_TOP_N = 3          # the party's N most-frequently-taken lines
CORE_MIN_TIMES = 3      # ...and seen at least this many times

# Trend and churn look at this many calendar months.
TREND_MONTHS = 6

SCHEMA = """
CREATE TABLE IF NOT EXISTS item_metric (
    guid        TEXT PRIMARY KEY,
    name        TEXT,
    daily       REAL,      -- avg base units/day over RATE_WINDOW_DAYS
    cover_days  REAL,      -- on_hand / daily; NULL when daily is 0
    last_sold   TEXT,
    times_sold  INTEGER,
    computed_at TEXT NOT NULL
);

-- Ageing, RECONSTRUCTED. Bill-wise accounting is off in this company
-- (ISBILLWISEON='No' on all 734 ledgers), so Tally holds no bill
-- references, no due dates and no per-bill balances -- the
-- BILLALLOCATIONS.LIST containers in the export are empty placeholders.
--
-- So receipts are applied to invoices oldest-first (FIFO), which is the
-- usual settlement convention, and what is left unpaid is aged by invoice
-- date. This is DERIVED, not authoritative: if a shop paid a later bill
-- first, the buckets shift. rebuild() checks the reconstruction against
-- each party's actual closing balance and records the gap, so the error is
-- visible rather than assumed away.
CREATE TABLE IF NOT EXISTS party_ageing (
    party        TEXT PRIMARY KEY,
    outstanding  REAL,      -- from the FIFO reconstruction
    actual       REAL,      -- the polled closing balance, for comparison
    gap          REAL,      -- reconstruction minus actual
    oldest_days  INTEGER,   -- age of the oldest unpaid invoice
    d0_30        REAL,
    d31_60       REAL,
    d61_90       REAL,
    d90_plus     REAL,
    last_receipt TEXT,      -- date of their last payment
    computed_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS party_profile (
    party       TEXT PRIMARY KEY,
    aov         REAL,      -- average sales invoice value
    invoices    INTEGER,
    last_days   INTEGER,   -- days since the last sale to them
    trend       TEXT,      -- JSON [{month, value}] oldest first
    churn       INTEGER,   -- % BELOW their own baseline, else NULL
    growth      INTEGER,   -- % above, else NULL
    computed_at TEXT NOT NULL
);

-- One row per (party, item) they buy repeatedly. This is the reorder engine.
CREATE TABLE IF NOT EXISTS party_buy (
    party      TEXT NOT NULL,
    item       TEXT NOT NULL,
    cyc_lo     INTEGER,    -- usual reorder window, in days
    cyc_hi     INTEGER,
    last_days  INTEGER,    -- since they last took this line
    typical_qty REAL,      -- median of recent quantities
    freq_n     INTEGER,    -- bought in n ...
    freq_m     INTEGER,    -- ... of their last m invoices
    is_core    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (party, item)
);
CREATE INDEX IF NOT EXISTS party_buy_party ON party_buy(party);

CREATE TABLE IF NOT EXISTS daily_sales (
    vdate TEXT NOT NULL,
    party TEXT NOT NULL,
    salesperson TEXT NOT NULL DEFAULT '',
    invoices INTEGER NOT NULL,
    amount REAL NOT NULL,
    PRIMARY KEY(vdate,party,salesperson)
);
"""


def _today():
    return dt.date.today()


def _d(yyyymmdd):
    return dt.date(int(yyyymmdd[:4]), int(yyyymmdd[4:6]), int(yyyymmdd[6:8]))


def _scan(conn):
    """One pass over sales vouchers -> (invoices, lines).

    invoices: [(party, date, amount, guid)]
    lines:    {(party, item): [(date, qty, guid)]} and {item: [(date, qty)]}
    """
    rows = conn.execute(
        "SELECT v.vdate, v.party, v.amount, v.blob, v.guid FROM voucher v JOIN ("
        "  SELECT guid, MAX(alterid) a FROM voucher GROUP BY guid"
        ") x ON v.guid=x.guid AND v.alterid=x.a"
        " LEFT JOIN deleted_voucher d ON d.guid=v.guid"
        " WHERE d.guid IS NULL AND v.vchtype='Sales' AND v.party IS NOT NULL"
        " ORDER BY v.vdate,v.alterid,v.guid").fetchall()
    # Party-level facts are only meaningful for real shops. 'Unknown Sale'
    # and 'Cash In Hand' carry real sales but are posting ledgers, not
    # customers -- left in, 'Unknown Sale' is the single busiest "party" in
    # the books and has no core line, because it is a bucket.
    #
    # ITEM run rates deliberately keep every sale: stock leaves the godown
    # whoever it was billed to.
    shops = {c["name"] for c in shop.customers(conn)}
    invoices, by_pair, by_item = [], {}, {}
    for r in rows:
        rec = json.loads(gzip.decompress(r["blob"]))
        # A cancelled voucher is still in Tally, so reconcile never flags it;
        # counting it would inflate every run rate and trend.
        if (rec.get("ISCANCELLED") or "No").strip() == "Yes":
            continue
        if (rec.get("ISOPTIONAL") or "No").strip() == "Yes":
            continue
        is_shop = r["party"] in shops
        if is_shop:
            invoices.append((r["party"], r["vdate"], abs(r["amount"] or 0),r['guid']))
        for ln in rec.get("ALLINVENTORYENTRIES.LIST", []):
            item = ln.get("STOCKITEMNAME")
            if not item:
                continue
            qty = abs(t.qty(ln.get("BILLEDQTY") or ln.get("ACTUALQTY"))[0])
            if not qty:
                continue
            if is_shop:
                by_pair.setdefault((r["party"], item), []).append(
                    (r["vdate"], qty,r['guid']))
            by_item.setdefault(item, []).append((r["vdate"], qty))
    return invoices, by_pair, by_item


def rebuild(conn, log=print):
    """Recompute every derived table. Cheap enough to run with the rates job."""
    conn.executescript(SCHEMA)
    watermark = shop.s._get(conn,'voucher_alterid') or ''
    now = dt.datetime.now().isoformat(timespec="seconds")
    today = _today()
    invoices, by_pair, by_item = _scan(conn)
    # Replace facts from source, never increment counters on each sync.
    # This also corrects old days when a Tally invoice is altered/deleted.
    owners = {}
    for r in conn.execute('SELECT tally_guid,note FROM shop_order WHERE tally_guid IS NOT NULL'):
        note = r['note'] or ''
        owners[r['tally_guid']] = note[1:].split(']',1)[0] if note.startswith('[') else ''
    days = {}
    for party, date, amount, guid in invoices:
        key = (date,party,owners.get(guid,''))
        count,value = days.get(key,(0,0))
        days[key] = (count+1,value+amount)
    conn.execute('DELETE FROM daily_sales')
    conn.executemany('INSERT INTO daily_sales VALUES(?,?,?,?,?)',
                     [(*key,count,round(value,2)) for key,(count,value) in days.items()])

    # ---- per item: run rate and cover -------------------------------------
    stock = {r["name"]: r for r in shop.s.stock_now(conn)}
    cutoff = (today - dt.timedelta(days=RATE_WINDOW_DAYS)).strftime("%Y%m%d")
    rows = []
    for item, hits in by_item.items():
        recent = [q for d, q in hits if d >= cutoff]
        daily = sum(recent) / RATE_WINDOW_DAYS if recent else 0.0
        st = stock.get(item)
        on_hand = st["qty"] if st else None
        # Cover is undefined, not infinite, when nothing is moving -- saying
        # "9999 days of cover" would read as a healthy number.
        #
        # And it is ZERO, not negative, when stock is negative. 54 items are
        # oversold in Tally; -111/2.4 = "-46 days of cover" is arithmetically
        # right and meaningless on screen. The oversold condition stays
        # visible through on_hand itself, which is never clamped.
        if not daily or on_hand is None:
            cover = None
        else:
            cover = max(on_hand, 0) / daily
        rows.append((st["guid"] if st else item, item, round(daily, 4),
                     round(cover, 1) if cover is not None else None,
                     max(d for d, _ in hits), len(hits), now))
    conn.execute("DELETE FROM item_metric")
    conn.executemany(
        "INSERT OR REPLACE INTO item_metric(guid,name,daily,cover_days,"
        "last_sold,times_sold,computed_at) VALUES(?,?,?,?,?,?,?)", rows)

    # ---- per party: aov, recency, trend, churn ----------------------------
    per_party = {}
    for party, d, amt, _guid in invoices:
        per_party.setdefault(party, []).append((d, amt))

    months = []
    cur = today.replace(day=1)
    for _ in range(TREND_MONTHS):
        months.append(cur.strftime("%Y%m"))
        cur = (cur - dt.timedelta(days=1)).replace(day=1)
    months.reverse()

    prof = []
    for party, inv in per_party.items():
        amounts = [a for _, a in inv]
        aov = statistics.mean(amounts) if amounts else 0
        last = max(d for d, _ in inv)
        last_days = (today - _d(last)).days
        bucket = {m: 0.0 for m in months}
        for d, a in inv:
            m = d[:6]
            if m in bucket:
                bucket[m] += a
        trend = [{"month": m, "value": round(bucket[m], 2)} for m in months]
        # Churn against the party's OWN baseline, not a global average: a
        # Rs 20k/month shop dropping to Rs 9k matters as much as a big one.
        prior = [bucket[m] for m in months[:-1]]
        base = statistics.mean(prior) if prior else 0
        latest = bucket[months[-1]]
        churn = growth = None
        if base > 0:
            delta = (latest - base) / base * 100
            # The current month is partial, so a mild dip is not a signal.
            if delta <= -25:
                churn = int(round(-delta))
            elif delta >= 25:
                growth = int(round(delta))
        prof.append((party, round(aov, 2), len(inv), last_days,
                     json.dumps(trend), churn, growth, now))
    conn.execute("DELETE FROM party_profile")
    conn.executemany(
        "INSERT OR REPLACE INTO party_profile(party,aov,invoices,last_days,"
        "trend,churn,growth,computed_at) VALUES(?,?,?,?,?,?,?,?)", prof)

    # ---- ageing, reconstructed FIFO --------------------------------------
    shops = {c["name"] for c in shop.customers(conn)}
    ledger = {}
    for r in conn.execute(
            "SELECT v.vdate, v.party, v.vchtype, v.amount, v.blob FROM voucher v"
            " JOIN (SELECT guid, MAX(alterid) a FROM voucher GROUP BY guid) x"
            " ON v.guid=x.guid AND v.alterid=x.a"
            " LEFT JOIN deleted_voucher d ON d.guid=v.guid"
            " WHERE d.guid IS NULL AND v.party IS NOT NULL"
            " AND v.vchtype IN ('Sales','Receipt','Credit Note')"
            " ORDER BY v.vdate"):
        if r["party"] not in shops:
            continue
        rec = json.loads(gzip.decompress(r["blob"]))
        if (rec.get("ISCANCELLED") or "No").strip() == "Yes":
            continue
        ledger.setdefault(r["party"], []).append(
            (r["vdate"], r["vchtype"], abs(r["amount"] or 0)))

    bal = {r["name"]: r["closing"] for r in conn.execute(
        "SELECT b.name, b.closing FROM party_balance b JOIN ("
        "  SELECT guid, MAX(observed_at) m FROM party_balance GROUP BY guid"
        ") x ON b.guid=x.guid AND b.observed_at=x.m")}

    aged = []
    for party, evts in ledger.items():
        open_inv = []                      # [date, remaining]
        credit = 0.0
        last_receipt = None
        for d, vt, amt in evts:
            if vt == "Sales":
                open_inv.append([d, amt])
            else:                          # Receipt or Credit Note: settles
                if vt == "Receipt":
                    last_receipt = d
                credit += amt
            while credit > 0.005 and open_inv:
                take = min(credit, open_inv[0][1])
                open_inv[0][1] -= take
                credit -= take
                if open_inv[0][1] <= 0.005:
                    open_inv.pop(0)
        buckets = [0.0, 0.0, 0.0, 0.0]
        oldest = None
        for d, rem in open_inv:
            age = (today - _d(d)).days
            oldest = age if oldest is None else max(oldest, age)
            buckets[0 if age <= 30 else 1 if age <= 60 else
                    2 if age <= 90 else 3] += rem
        recon = sum(b for b in buckets)
        # Tally shows a debtor balance negative; what they owe is -closing.
        actual = -(bal.get(party) or 0)
        aged.append((party, round(recon, 2), round(actual, 2),
                     round(recon - actual, 2), oldest,
                     *[round(b, 2) for b in buckets], last_receipt, now))
    conn.execute("DELETE FROM party_ageing")
    conn.executemany(
        "INSERT OR REPLACE INTO party_ageing(party,outstanding,actual,gap,"
        "oldest_days,d0_30,d31_60,d61_90,d90_plus,last_receipt,computed_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?)", aged)

    # ---- per (party, item): reorder cycle, frequency, core ----------------
    inv_guids = {}
    for party, d, _a, guid in invoices:
        inv_guids.setdefault(party, []).append(guid)
    buys = []
    for (party, item), hits in by_pair.items():
        dates = sorted({d for d, _, _guid in hits})
        gaps = [( _d(b) - _d(a)).days for a, b in zip(dates, dates[1:])]
        gaps = [g for g in gaps if g > 0]
        gaps.sort()
        lo = hi = None
        if gaps:
            lo = gaps[int(len(gaps) * 0.25)]
            hi = gaps[min(int(len(gaps) * 0.75), len(gaps) - 1)]
            if hi <= lo:
                hi = lo + max(2, round(lo * 0.4))
        qtys = sorted(q for _, q, _guid in hits[-6:])
        typical = statistics.median(qtys)
        recent_inv = inv_guids.get(party, [])[-10:]
        m = len(recent_inv)
        n = len({guid for _d_, _q, guid in hits if guid in set(recent_inv)})
        buys.append([party, item, lo, hi,
                     (today - _d(dates[-1])).days, round(typical, 3), n, m, 0])

    # Mark each party's top lines as core, ranked within that party.
    byparty = {}
    for b in buys:
        byparty.setdefault(b[0], []).append(b)
    for rows_ in byparty.values():
        rows_.sort(key=lambda b: (-b[6], -b[5]))        # freq_n, then qty
        for b in rows_[:CORE_TOP_N]:
            if b[6] >= CORE_MIN_TIMES:
                b[8] = 1
    conn.execute("DELETE FROM party_buy")
    conn.executemany(
        "INSERT OR REPLACE INTO party_buy(party,item,cyc_lo,cyc_hi,last_days,"
        "typical_qty,freq_n,freq_m,is_core) VALUES(?,?,?,?,?,?,?,?,?)", buys)
    shop.s._set(conn, 'facts_built_at_alterid', watermark)
    shop.s._set(conn, 'facts_built_on', today.isoformat())
    conn.commit()
    log(f"insights: {len(rows)} items, {len(prof)} parties, {len(buys)} "
        f"repeat lines, {len(aged)} aged, from {len(invoices)} sales")
    return {"items": len(rows), "parties": len(prof), "buys": len(buys),
            "aged": len(aged)}


def ageing(conn, party):
    r = conn.execute("SELECT * FROM party_ageing WHERE party=?",
                     (party,)).fetchone()
    return dict(r) if r else None


# ------------------------------------------------------------------ reads

def due_lines(conn, party):
    """What this party is due to reorder, and what they usually take.

    The order-taking screen's whole reason-giving layer: due / core-missing,
    each with the evidence behind it.
    """
    out = []
    for r in conn.execute(
            "SELECT * FROM party_buy WHERE party=? ORDER BY is_core DESC,"
            " freq_n DESC", (party,)):
        due = r["last_days"] is not None and r["last_days"] >= (r["cyc_lo"] or 0)
        out.append({
            "item": r["item"], "qty": r["typical_qty"],
            "cycle": [r["cyc_lo"], r["cyc_hi"]], "last_days": r["last_days"],
            "freq": [r["freq_n"], r["freq_m"]], "core": bool(r["is_core"]),
            "due": due,
            "reason": ("reorder" if due else "core" if r["is_core"] else None),
        })
    return out


def profile(conn, party):
    r = conn.execute("SELECT * FROM party_profile WHERE party=?",
                     (party,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["trend"] = json.loads(d["trend"] or "[]")
    return d


def metrics(conn):
    return {r["name"]: dict(r) for r in conn.execute("SELECT * FROM item_metric")}


def _self_check():
    c = shop.open_db()
    r = rebuild(c)
    assert r["items"] > 300, f"only {r['items']} items had any sales"
    assert r["parties"] > 300, f"only {r['parties']} parties"
    assert r["buys"] > 1000, f"only {r['buys']} repeat lines"

    # run rate and cover must be consistent with the stock snapshot
    m = metrics(c)
    moving = [v for v in m.values() if v["daily"]]
    assert moving, "no item has a run rate"
    withcover = [v for v in moving if v["cover_days"] is not None]
    assert withcover, "no item has cover"
    for v in withcover[:50]:
        assert v["cover_days"] >= 0, f"negative cover for {v['name']}"
    top = sorted(moving, key=lambda v: -v["daily"])[:3]
    print(f"insights ok: {len(m)} items, {len(moving)} moving")
    for v in top:
        print(f"   {v['daily']:>7.2f}/day  cover "
              f"{v['cover_days'] if v['cover_days'] is not None else '-':>7}"
              f"  {v['name'][:38]}")

    # a party profile must look like the prototype's customer card
    busiest = c.execute("SELECT party FROM party_profile ORDER BY invoices"
                        " DESC LIMIT 1").fetchone()["party"]
    p = profile(c, busiest)
    assert p and p["aov"] > 0 and len(p["trend"]) == TREND_MONTHS
    assert p["last_days"] is not None and p["last_days"] >= 0
    print(f"   {busiest[:32]}: {p['invoices']} invoices, aov "
          f"{p['aov']:,.0f}, last {p['last_days']}d ago, "
          f"churn={p['churn']} growth={p['growth']}")
    print(f"     trend: {[int(x['value']) for x in p['trend']]}")

    # the reorder engine must produce both kinds of prompt
    lines = due_lines(c, busiest)
    assert lines, "no repeat lines for the busiest party"
    assert all(l["cycle"][0] <= l["cycle"][1] for l in lines), "bad cycle"
    assert all(0 <= l["freq"][0] <= l["freq"][1] for l in lines), "bad freq"
    due = [l for l in lines if l["due"]]
    core = [l for l in lines if l["core"]]
    assert core, "nothing identified as a core line"
    print(f"   {len(lines)} repeat lines, {len(due)} due now, {len(core)} core")
    for l in (due or lines)[:3]:
        print(f"     {l['item'][:32]:<32} every {l['cycle'][0]}-"
              f"{l['cycle'][1]}d, last {l['last_days']}d, "
              f"qty {l['qty']:g}, {l['freq'][0]}/{l['freq'][1]}"
              f"{' CORE' if l['core'] else ''}")

    # --- ageing: the reconstruction has to reproduce the real balances ----
    rows_a = c.execute("SELECT * FROM party_ageing WHERE actual > 100"
                       ).fetchall()
    assert rows_a, "no party has a balance to age"
    close = [r for r in rows_a if abs(r["gap"]) <= max(50, 0.02 * r["actual"])]
    pct = 100 * len(close) / len(rows_a)
    print(f"   ageing: {len(rows_a)} parties with a balance, "
          f"{len(close)} ({pct:.0f}%) reconstruct to within 2%")
    worst = sorted(rows_a, key=lambda r: -abs(r["gap"]))[:3]
    for r in worst:
        print(f"     gap {r['gap']:>12,.0f}  recon {r['outstanding']:>12,.0f}"
              f"  actual {r['actual']:>12,.0f}  {r['party'][:26]}")
    assert pct >= 50, (
        f"only {pct:.0f}% of balances reconstruct -- FIFO settlement is the "
        f"wrong model for this business, do not ship the ageing")
    withold = [r for r in rows_a if r["oldest_days"]]
    assert withold, "nothing has an age"
    o = max(withold, key=lambda r: r["oldest_days"])
    print(f"     oldest unpaid: {o['oldest_days']}d  {o['party'][:28]}  "
          f"0-30 {o['d0_30']:,.0f} / 31-60 {o['d31_60']:,.0f} / "
          f"61-90 {o['d61_90']:,.0f} / 90+ {o['d90_plus']:,.0f}")

    # churn must be measured against the party's own baseline
    ch = c.execute("SELECT party,churn,trend FROM party_profile"
                   " WHERE churn IS NOT NULL ORDER BY churn DESC"
                   " LIMIT 1").fetchone()
    if ch:
        tr = [int(x["value"]) for x in json.loads(ch["trend"])]
        print(f"   churn: {ch['party'][:30]} down {ch['churn']}% -> {tr}")
    c.close()
    print("insights ok")


if __name__ == "__main__":
    _self_check()
