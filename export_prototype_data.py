"""Export the real Tally archive into the Saarthi prototype's data shape.

Writes data-tally.js, which the prototype loads AFTER data.js and which
replaces PRODUCTS and CUSTOMERS with the real catalogue and customer base.
The demo's other scaffolding (REPS, ORDERS, ZONES) is left alone so the
remaining modules keep working.

    python export_prototype_data.py

Everything here is real except where marked PLACEHOLDER. Nothing is
invented to fill a gap: a field with no Tally source comes out null, and the
prototype renders an empty state.
"""
import datetime as dt
import gzip
import json
import pathlib

import tally_connector as t
import tally_insights as ins
import tally_shop as shop

OUT = pathlib.Path(r"C:\Users\PARTH MUNDRA\Desktop\Saarthi Sales Copilot"
                   r"\data-tally.js")

# The prototype flags critical stock as `cover(p) < p.lead`. Tally stores no
# supplier lead time, so one constant stands in until it does.
LEAD_DAYS_PLACEHOLDER = 7

# c.rep is read 13 times and drives navigation, but Tally records no
# salesperson per customer. Routes are dealt round-robin across the demo
# reps purely so the app stays navigable -- this is NOT a real assignment.
DEMO_REPS = ["s1", "s2", "s3", "s4"]


def build():
    c = shop.open_db()
    items = shop.catalogue(c, limit=10 ** 6)
    custs = shop.customers(c)
    met = ins.metrics(c)

    # ---- products -------------------------------------------------------
    products, groups = [], {}
    for i in items:
        m = met.get(i["name"]) or {}
        grp = i["group"] or "Other"
        groups.setdefault(grp, 0)
        groups[grp] += 1
        products.append({
            "id": i["guid"],
            "name": i["name"],
            "unit": i["unit"] or "PCS",          # PCS: no case structure
            "price": i["standard_rate"],         # fallback; party rate wins
            "mrp": i["mrp"],                     # parsed from the name
            "stock": i["saleable"],              # may be NEGATIVE
            "onHand": i["on_hand"],
            "stale": i["stale"],
            "daily": round(m.get("daily") or 0, 3),
            "cover": m.get("cover_days"),
            "lead": LEAD_DAYS_PLACEHOLDER,
            "grp": grp,
            "brand": None,                       # no Tally field
            "scheme": None,                      # no schemes in this business
        })

    # Category chips come from Tally stock groups -- the demo's hand-made
    # categories do not exist. Biggest groups first, rest folded into "Other".
    top = sorted(groups.items(), key=lambda kv: -kv[1])[:8]
    cats = [["all", "All items"]] + [[g, g.title()[:22]] for g, _ in top]
    keep = {g for g, _ in top}
    pcat = {p["id"]: (p["grp"] if p["grp"] in keep else "other")
            for p in products}

    # ---- customers ------------------------------------------------------
    byname = {p["name"]: p["id"] for p in products}
    customers = []
    for n, cu in enumerate(custs):
        prof = ins.profile(c, cu["name"]) or {}
        age = ins.ageing(c, cu["name"]) or {}
        rates = shop.party_rates(c, cu["name"])
        buys = []
        for b in ins.due_lines(c, cu["name"]):
            pid = byname.get(b["item"])
            if not pid:
                continue                      # bought an item since deleted
            r = rates.get(b["item"]) or {}
            buys.append({
                "p": pid, "cyc": b["cycle"], "last": b["last_days"],
                "qty": b["qty"], "freq": b["freq"], "core": b["core"],
                "due": b["due"],
                # what THIS shop last paid, and when
                "rate": None if r.get("suspect") else r.get("rate"),
                "rateDate": r.get("date"),
            })
        trend = [round(x["value"]) for x in (prof.get("trend") or [])]
        overdue = None
        if age:
            overdue = round((age.get("d31_60") or 0) + (age.get("d61_90") or 0)
                            + (age.get("d90_plus") or 0))
        customers.append({
            "id": cu["guid"],
            "name": cu["name"],
            "area": None,                      # 0 of 734 have an address
            "territory": cu["route"],          # the geography we DO have
            "place": cu["route"],
            "rep": DEMO_REPS[n % len(DEMO_REPS)],   # PLACEHOLDER
            "contact": None,                   # 0 of 734
            "phone": None,                     # 0 of 734
            "lastDays": prof.get("last_days"),
            "lastVisit": prof.get("last_days"),
            "aov": round(prof.get("aov") or 0),
            "outstanding": round(cu["outstanding"] or 0),
            "limit": None,                     # 0 of 734 -- credit flags dark
            "overdue": overdue,
            "overdueDays": age.get("oldest_days"),
            "ageingGap": age.get("gap"),
            "trend": trend or [0] * 6,
            "churn": prof.get("churn"),
            "growth": prof.get("growth"),
            "lat": None, "lng": None,          # 0 of 734 -- no route planner
            "buys": buys,
            "cross": [],                       # derive from peers later
            "gstin": cu["gstin"],              # 0 of 734 registered
        })
    # ---- real receipts, for the Payments module ------------------------
    # Mode comes from the ledger the money landed in: Cash In Hand vs a bank
    # account. Cheque numbers, UTRs and bill references do NOT exist -- the
    # demo invented those, and bill-wise accounting is off, so there is no
    # "against invoice" either.
    def mode_of(n):
        u = (n or "").upper()
        if "CASH" in u:
            return "Cash"
        if any(k in u for k in ("BANK", "ICICI", "HDFC", "SBI", "AXIS", "BOI")):
            return "Bank"
        return None

    pays, daily = {}, {}
    for r in c.execute(
            "SELECT v.vdate,v.party,v.vchtype,v.amount,v.blob FROM voucher v"
            " JOIN (SELECT guid,MAX(alterid) a FROM voucher GROUP BY guid) x"
            " ON v.guid=x.guid AND v.alterid=x.a"
            " LEFT JOIN deleted_voucher d ON d.guid=v.guid"
            " WHERE d.guid IS NULL AND v.party IS NOT NULL"
            " AND v.vchtype IN ('Receipt','Sales') ORDER BY v.vdate DESC"):
        rec = json.loads(gzip.decompress(r["blob"]))
        if (rec.get("ISCANCELLED") or "No").strip() == "Yes":
            continue
        if r["vchtype"] == "Receipt":
            into = next((le.get("LEDGERNAME")
                         for le in rec.get("ALLLEDGERENTRIES.LIST", [])
                         if le.get("LEDGERNAME") and le["LEDGERNAME"] != r["party"]),
                        None)
            pays.setdefault(r["party"], [])
            if len(pays[r["party"]]) < 10:
                pays[r["party"]].append({
                    "date": r["vdate"], "amt": round(abs(r["amount"] or 0)),
                    "mode": mode_of(into), "into": into,
                    "no": rec.get("VOUCHERNUMBER"),
                    "ref": None, "against": None,   # not recorded in Tally
                })
        else:
            d = daily.setdefault(r["vdate"], {"orders": 0, "value": 0,
                                              "parties": []})
            d["orders"] += 1
            d["value"] += round(abs(r["amount"] or 0))
            if r["party"] not in d["parties"]:
                d["parties"].append(r["party"])
    daily = {k: {"orders": v["orders"], "value": v["value"],
                 "parties": len(v["parties"])}
             for k, v in sorted(daily.items())[-120:]}

    # ---- real sales LINES, for the dashboard -----------------------------
    # The dashboard compares a period against the one before it, so it needs
    # a genuine time series: one row per invoice line, with its own date.
    # Deriving it from party_buy instead would collapse a party's whole
    # history for an item into a single point at its most recent purchase --
    # the totals would look plausible and every period comparison would be
    # meaningless.
    gcut = (dt.date.today() - dt.timedelta(days=90)).strftime("%Y%m%d")
    byname = {p["name"]: p["id"] for p in products}
    guidof = {x["name"]: x["guid"] for x in custs}
    lines = []
    for r in c.execute(
            "SELECT v.vdate,v.party,v.blob FROM voucher v JOIN (SELECT guid,"
            "MAX(alterid) a FROM voucher GROUP BY guid) x ON v.guid=x.guid"
            " AND v.alterid=x.a LEFT JOIN deleted_voucher d ON d.guid=v.guid"
            " WHERE d.guid IS NULL AND v.vchtype='Sales' AND v.vdate >= ?",
            (gcut,)):
        if r["party"] not in guidof:
            continue
        rec = json.loads(gzip.decompress(r["blob"]))
        if (rec.get("ISCANCELLED") or "No").strip() == "Yes":
            continue
        for ln in rec.get("ALLINVENTORYENTRIES.LIST", []):
            pid = byname.get(ln.get("STOCKITEMNAME"))
            if not pid:
                continue
            lines.append([r["vdate"], guidof[r["party"]], pid,
                          abs(t.qty(ln.get("ACTUALQTY"))[0]),
                          round(abs(t.amount(ln.get("AMOUNT"))))])
    c.close()

    stamp = dt.datetime.now().isoformat(timespec="seconds")
    js = f"""/* Generated from the live Tally archive -- do not edit by hand.
   {stamp} · {len(products)} items · {len(customers)} customers
   Regenerate: python "D:\\\\Tally Project\\\\export_prototype_data.py"

   Quantities are in Tally's base unit (PCS). There is no case/pack
   structure: AdditionalUnits is "Not Applicable" and Conversion is 0 on
   every item, so cases would have to be invented.

   null means Tally has no value -- render an empty state, never a zero. */
PRODUCTS.length = 0; PRODUCTS.push(...{json.dumps(products, ensure_ascii=False)});
CUSTOMERS.length = 0; CUSTOMERS.push(...{json.dumps(customers, ensure_ascii=False)});
CATS.length = 0; CATS.push(...{json.dumps(cats, ensure_ascii=False)});
Object.keys(PCAT).forEach(k => delete PCAT[k]);
Object.assign(PCAT, {json.dumps(pcat)});
CUSTOMERS.forEach(c => {{ c._inv = null; c._pay = null; }});
/* Real receipts, newest first, max 10 per party. mode is Cash or Bank from
   the ledger the money landed in; ref and against are null because Tally
   records neither (bill-wise accounting is off). */
const TALLY_PAYMENTS = {json.dumps(pays, ensure_ascii=False)};
/* Real order activity per day: orders placed, value, distinct parties.
   This is NOT visit coverage -- Tally only knows the visits that became a
   sale, never the ones that did not. */
const TALLY_DAILY = {json.dumps(daily)};
/* Real invoice lines, last 90 days: [date, customerGuid, itemGuid, qty, value].
   qty is in Tally's base unit (PCS) -- never cases. */
const TALLY_LINES = {json.dumps(lines)};
/* The demo order queue points at product ids that no longer exist, so it is
   cleared rather than left to throw. Real orders arrive from the cloud. */
ORDERS.length = 0;
"""
    OUT.write_text(js, encoding="utf-8")
    kb = len(js.encode()) / 1024
    print(f"wrote {OUT.name}: {kb:,.0f} KB")
    print(f"  {len(products)} items, {len(customers)} customers")
    print(f"  {sum(len(x['buys']) for x in customers)} purchase lines")
    priced = sum(1 for p in products if p["price"])
    withmrp = sum(1 for p in products if p["mrp"])
    print(f"  {priced} priced, {withmrp} with MRP, "
          f"{sum(1 for p in products if (p['stock'] or 0) < 0)} oversold")
    print(f"  categories from Tally groups: {[c[1] for c in cats[1:]]}")
    nobuys = sum(1 for x in customers if not x["buys"])
    print(f"  {len(pays)} parties with receipts, {len(daily)} days of order activity")
    print(f"  {len(lines)} real sales lines over 90 days")
    print(f"  {nobuys} customers with no purchase history (list shows all "
          f"items, none pinned)")
    return products, customers


if __name__ == "__main__":
    build()
