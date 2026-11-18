# Saarthi UI ↔ Tally cloud: field-by-field map

Maps the prototype's in-memory demo objects (`data.js`) onto the Postgres
tables the laptop pushes. Written against the live schema on 2026-10-13 and
against every field the prototype actually reads (counted with grep, not
guessed).

**Status key**

| | |
|---|---|
| ✅ | direct — one column, same meaning |
| ➗ | derived — computed from invoices, available now |
| ⚠️ | **semantics differ** — data exists but the UI must change |
| ⬜ | **blank** — column exists, always NULL today. Feature ships dark. |
| ✂️ | delete — no equivalent and none coming |

**Derived fields are NULL for inactive shops.** Of 605 customers, 469 have a
`trend`, 381 an ageing and 372 a churn figure — the rest have too little
recent history to compute one. The UI must render a blank, not a zero: a
shop with no data is not a shop with no sales.

Counts in brackets are how many times the prototype reads that field, so the
cost of each gap is visible.

---

## 1. `PRODUCTS[]` → `item`

| Prototype | Reads | Cloud | | Notes |
|---|---|---|---|---|
| `p.id` | 22 | `item.guid` | ✅ | GUID, not `P1`. Stable across renames. |
| `p.name` | 16 | `item.name` | ✅ | Carries the MRP as text, e.g. `HARPIC 1LTR MRP 235`. |
| `p.price` | 13 | `item.rate` | ⚠️ | StandardPrice. **This is the fallback, not the quote** — see §3. |
| `p.stock` | 12 | `item.saleable` | ⚠️ | `saleable = on_hand − reserved`. **Can be negative** (54 items oversold); the UI assumes ≥ 0. |
| `p.mrp` | 1 | `item.mrp` | ➗ | Parsed out of the name. Present on **609 of 625**; NULL on the 16 whose name carries no MRP. |
| `p.unit` | 1 | `item.unit` | ⚠️ | Always `PCS`. The prototype shows `48 pcs/case`; **there is no case structure** — see §7. |
| `p.daily` | 1 | `item.daily` | ➗ | Units/day over 90 days. |
| `cover(p)` | — | `item.cover_days` | ➗ | Floors at 0 when stock is negative, so it never reads as a healthy number. |
| `p.lead` | 5 | `item.pack_size`… none | ⬜ | Supplier lead time. **No Tally field.** Used only as `cover(p) < p.lead` to flag critical stock — substitute a constant (say 7 days) until it exists. |
| `p.brand` | 3 | `item.brand` | ⬜ | 0 of 625. Brand filters render empty. |
| `p.scheme` | **20** | `item.scheme` | ⬜ | 0 of 625, and you confirmed there are no schemes. **Second most-read product field** — see §7. |
| `p.slow` | — | — | ➗ | Derive as `cover_days > 60 AND daily > 0`. |
| — | — | `item.on_hand` | ➗ | **New.** Raw Tally quantity, never clamped. Show this when negative. |
| — | — | `item.stock_asof` | ➗ | **New, and required.** When the quantity was observed. Nothing may present stock as current without it. |
| — | — | `item.grp` | ✅ | Tally stock group — the only real category you have. |
| `p.category` | — | `item.category` | ⬜ | Tally's `Category` is `"Not Applicable"` on all 625. Use `grp` instead. |

---

## 2. `CUSTOMERS[]` → `customer`

| Prototype | Reads | Cloud | | Notes |
|---|---|---|---|---|
| `c.id` | 66 | `customer.guid` | ✅ | |
| `c.name` | 33 | `customer.name` | ✅ | **Needs escaping** — real names contain `&apos;` and the `&#4;` control char. |
| `c.outstanding` | 23 | `customer.outstanding` | ✅ | Polled, with `balance_asof`. ₹58.1 lakh across 413 shops. |
| `c.overdue` | 17 | `customer.overdue` | ➗ | Reconstructed FIFO: everything past 30 days. **Approximate** — see `ageing_gap`. |
| `c.overdueDays` | 5 | `customer.overdue_days` | ➗ | Age of the oldest unpaid invoice. Max observed: 195 days. |
| `c.buys[]` | 17 | `party_buy` | ➗ | The reorder engine — §4. |
| `c.limit` | **14** | `customer.credit_limit` | ⬜ | 0 of 734. Every credit warning and the order desk's `issue:'credit'` flag goes dark. |
| `c.rep` | 13 | `user_route` + `route` | ⚠️ | No rep is stored per customer. Ownership comes from **route → salesperson**, which is the model your route groups already imply. |
| `c.lastDays` | 9 | `customer.last_days` | ➗ | Days since their last invoice. |
| `c.lat` / `c.lng` | 13 | `customer.lat` / `.lng` | ⬜ | 0 of 734. **Route planner cannot run.** |
| `c.trend[6]` | 7 | `customer.trend` | ➗ | JSON `[{month,value}]`, oldest first. |
| `c.churn` | 7 | `customer.churn` | ➗ | % below the shop's **own** baseline, NULL when not churning. |
| `c.growth` | 4 | `customer.growth` | ➗ | Mirror of churn. |
| `c.contact` | 6 | `customer.contact` | ⬜ | 0 of 734. |
| `c.phone` | 2 | `customer.phone` | ⬜ | 0 of 734. **Call and WhatsApp buttons do nothing.** |
| `c.area` / `c.place` | 11 | `customer.address` | ⬜ | 0 of 734. Fall back to `route`, which is populated for all 605. |
| `c.territory` | 3 | `customer.route` | ⚠️ | **19** values: 18 route sub-groups (`Dewas Debtors`, `Mhow-Rau-Bijalpur`, …) plus `Sundry Debtors` itself, where 63 shops sit unfiled. Not the prototype's 6 tidy zones. |
| `c.aov` | 5 | `customer.aov` | ➗ | Mean sales invoice value. |
| `c.cross[]` | 2 | — | ✂️ | Hand-written cross-sell copy. No source; derive from peer purchases instead — §5. |
| `c.isNew` | 2 | — | ⚠️ | A party created in the app doesn't exist in Tally until an invoice is posted. |
| — | — | `d0_30`…`d90_plus` | ➗ | **New.** Ageing buckets. |
| — | — | `ageing_gap` | ➗ | **New, and important.** Reconstruction minus actual balance. 82% land within 2%; show a caveat when `abs(gap)` is large. |
| — | — | `last_receipt` | ➗ | **New.** Date of their last payment. |
| — | — | `customer.gstin` | ⬜ | 0 of 734 registered. |

---

## 3. Pricing — the one that changes behaviour

The prototype treats `p.price` as *the* price and `rateOf(cid,pid)` as a
salesperson override held in memory.

**On your data, `item.rate` is wrong for 76% of (party, item) pairs** —
median 2.7% off, 90th percentile 11.7%. Rates are negotiated per shop and
recorded only in the invoices.

```
quote = party_rate.last_rate   WHERE party = <shop> AND item = <item>
        else item.rate
```

| Column | Meaning |
|---|---|
| `party_rate.last_rate` | what this shop last actually paid |
| `party_rate.last_date` | when — show it; a 6-month-old price deserves a second look |
| `party_rate.times` | how many times this pair has been billed |
| `party_rate.item_median` | median across all shops, for the outlier guard |

**Outlier guard:** if `last_rate` is outside 0.5×–2× `item_median`, do not
quote it. Real example: `VIM 85 WALA` was last billed at **₹75 against a
₹1,040 median**. Fall back to `item.rate` and flag it.

The UI's "₹X below our rate" badge still works — compare the quote against
`item.rate`.

---

## 4. `c.buys[]` → `party_buy`

The reorder engine. One row per (shop, item) bought twice or more: **3,858
rows, 327 core.**

| Prototype | Cloud | Notes |
|---|---|---|
| `b.p` | `party_buy.item` | Item **name**, not guid — join to `item.name`. |
| `b.qty` | `typical_qty` | Median of their last 6 quantities. |
| `b.cyc[0..1]` | `cyc_lo`, `cyc_hi` | Reorder window, from the **actual** gap distribution (25th–75th percentile). |
| `b.last` | `last_days` | Days since they last took this line. |
| `b.freq[0..1]` | `freq_n`, `freq_m` | Taken in n of their last m invoices. |
| `b.core` | `is_core` | **Redefined** — see below. |

`recsFor()` maps directly:

```
due    = last_days >= cyc_lo          -> "Reorder due"
core   = is_core AND not in cart      -> "Usually taken"
```

### ⚠️ The copy must change

The prototype says *"Usually in every order — purchased in **6 of previous
6** invoices."* Your shops do not behave that way:

```
lines appearing in >=80% of a party's invoices:     6 of 3,763
lines appearing in >=40%:                         277 of 3,763
median share:                                     about 20%
```

They order a **varying basket** each visit. `is_core` now means *"one of this
shop's 3 most-taken lines"*, which is relative and defensible. The copy must
read from `freq_n`/`freq_m`:

> ✅ "Taken in **3 of the last 10** bills"
> ❌ "Usually in every order"

---

## 5. `c.cross[]` — cross-sell

Hand-written in the demo (`why`, `peer`). Nothing to map. Derivable from
`history_line`: items bought by shops on the same `route` that this shop has
never taken, ranked by how many peers buy them. The prototype's
`pitchItems()` already does roughly this — point it at the real tables.

Its scheme-boosting branch (`score += 8`) goes dead, since `scheme` is NULL.

---

## 6. `ORDERS[]` → `cloud_order` / `cloud_order_line`

| Prototype | Cloud | Notes |
|---|---|---|
| `o.id` | `order_no` | **The cloud allocates this**, not the browser — 3–4 phones would otherwise collide. |
| `o.cid` | `customer_guid` | |
| `o.rep` / `o.by` | `salesperson` | Google email. The audit trail. |
| `o.lines[[p,q,r]]` | `cloud_order_line` | `item_guid`, `qty`, `rate`, `amount`. |
| `o.amount` | `total` | `subtotal` + `gst`, and **`gst` is always 0** by decision. |
| `o.status` | `status` | `placed` → `approved` / `rejected`. |
| `o.placed` | `created_at` | |
| `o.issue` | — | ⚠️ Derive: `credit` needs `credit_limit` (⬜ blank, so it can't fire yet); `stock` = any line where `qty > saleable`; `abnormal` = qty far from `typical_qty`. |
| — | `stock_asof` | **New.** How stale the stock was when the order was priced. |
| — | `pulled_at`, `tally_guid`, `tally_vchno` | **Laptop-only fields.** The website must never write these. |

**Status ownership:** the website owns `placed`/`approved`/`rejected`. Only
the laptop sets `invoiced`, and only with a Tally voucher in hand. One writer
per field, no merge logic.

---

## 7. What to change in the UI

**Cases → pieces.** Every price, stock figure, quantity stepper and total in
the prototype is per *case* (`'48 pcs/case'`, `price: 1056` per case). Your
Tally is PCS-only: `AdditionalUnits = "Not Applicable"` and `Conversion = 0`
on all 625 items. Nothing to map — the numbers change meaning on every
screen. **Do this before wiring anything.**

**Schemes.** `p.scheme` is read 20 times — item cards, the pitch ranking, the
cart. All of it goes dark. Decide whether to hide those branches or leave
them rendering nothing.

**Negative stock.** 54 items. The UI will print `-111` in a quantity column.
It needs an "oversold" state, not a number.

**Escaping.** Party and product names are interpolated raw in ~30 places.
`esc()` exists; it just isn't used on them. Real names contain apostrophes.

**The sync banner** reads *"4 min ago · 1,284 invoices, 18 SKUs, 14
accounts"* — hardcoded. Real figures come from `sync_status`: **10,659
vouchers, 625 SKUs, 605 accounts.**

---

## 8. Features with no data

Each column exists and is NULL, so the feature ships and lights up the day
someone enters the data. Nothing here needs a code change later.

| Feature | Needs | Today |
|---|---|---|
| Route planner | `lat`, `lng` | 0 of 734 — **cannot run at all** |
| Credit warnings, order-desk credit flag | `credit_limit`, `credit_days` | 0 of 734 |
| Call / WhatsApp | `phone`, `contact` | 0 of 734 |
| Brand filters | `brand` | 0 of 625 |
| Scheme prompts | `scheme` | none exist by decision |
| Critical-stock flag | `lead` | no Tally field; use a constant |
| GST on invoices | the whole tax setup | GST unconfigured — `gst` is 0 everywhere |

**Cheapest wins if you ever collect data:** `phone` (makes the call button
work) and `credit_limit` (lights up the credit flag, which the order desk is
built around).

---

## 9. Reading it

The phone should pull **per shop**, not the lot:

| Endpoint | Rows | Size |
|---|---|---|
| `/items` | 625 | 17 KB gzip |
| `/customers` (scoped by `user_route`) | 605 max | 13 KB gzip |
| `/party/<name>/rates` | ~24 avg | < 1 KB |
| `/party/<name>/buys` | ~8 avg | < 1 KB |
| `/party/<name>/history` | 50 | a few KB |

Whole catalogue + customer list is **~30 KB gzipped** — small enough to cache
offline in full, which is what the rep at a shop with no signal needs.
