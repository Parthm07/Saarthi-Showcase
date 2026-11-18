# Tally edits: local archive, cloud, and live screens

The salesman fact/retention implementation now supersedes the earlier
unbounded cloud detail policy. See CLOUD_RETENTION_DECISIONS.md for the
seven-day window, protection flags, compact retry receipts, local backup
verification, daily summaries and the required additive migration. Full source
history stays local; old edits continue to correct facts without re-uploading
expired unprotected invoice details. Admin detailed charts now identify their
retained-detail window; a new admin summary interface remains deferred.

Implemented locally on 30 October 2026. Original website orders stay unchanged;
current invoice amounts and lines are separate authoritative Tally facts.

## Verified example

Order WEB-1029942543 / Tally voucher 4440:

- Submitted order: 3 pieces at 84 = 252.
- Latest observed Tally version: 8 pieces at 84 = **672**, AlterID 28621.
- Local archive retains both versions (28619 and 28621).
- The existing cloud history already contained 672 during the read-only check.
- The old order response and screen still used 252 because that is the original
  order. The missing link was current invoice details, plus client refresh.

The user's approximate 762 figure was not written into any database. The value
observed from Tally is used. No invoice is altered or created by edit publishing.

## Change flow

`tally_updates.py` compares latest observed source versions with the cloud
history. The regular one-minute agent push publishes changed headers and
replaces their line sets in one Postgres transaction. Historical edits and
recovered records are detected by per-GUID versions rather than today's date or
one global watermark. Previously published versions cannot be replaced by an
older source version. An advisory transaction lock serializes new publishers.

The source archive keeps its prior rows. No update changes `shop_order.total`,
`shop_order_line`, `cloud_order.total`, or `cloud_order_line`. Confirmed deletion,
cancellation and optional states remove lines from active reports while
preserving the source status. Absence from an incomplete export alone does
not establish deletion. Tally purchases/supplier costs remain excluded.

The hourly slow push now uses this same history publisher rather than truncating
history and discarding version metadata. A new voucher version also makes the
rates/insights job due before the next regular push.

Customer-specific last billed prices now publish changed rows on the regular
fast push as well; they no longer wait for the hourly slow push. Standard item
prices remain separate. For same-day invoices, the source AlterID resolves the
latest rate deterministically; optional and cancelled vouchers do not establish
a billed price. Sachin Kirana [Chawani]'s local rate for HARPIC BLUE 500 ML MRP
110 was verified read-only at 80 on 20261030. No manual price was forced into
Tally or the cloud. Existing drafts keep their quoted prices; a new order uses
the latest customer rate after sync.

The source projection is limited to Sales, Receipt and Credit Note vouchers for
recognized customer parties. Stock and balances retain their separately
observed snapshots and timestamps. This is not a general-purpose bidirectional
editor or a claim that every possible Tally field is now replicated.

## What clients display

- `/api/orders` retains original order totals/lines and adds a separate
  `invoice` containing the current Tally date, party, number, source version,
  amount, state and lines.
- Admins receive company sales invoices. Salesmen receive only their own order
  list, and current invoice details require access to the invoice's current
  customer route. A moved invoice outside that scope is not disclosed.
- Order list, admin order desk and bill/print views show only current active
  Tally invoice quantities and amounts after billing. Original submission
  values remain stored internally for retries and recovery and are not shown
  alongside the invoice. Current invoice customer names replace submitted
  customer names in these views as well.
- Unbilled orders show submitted values. Once an invoice exists, missing,
  restricted, cancelled, deleted, optional or unavailable invoice details never
  fall back to submitted quantities or amounts: a status message and no amount
  are shown instead. These states cannot print a stale submitted order as a bill.
- Sales dashboard data is rebuilt from current source lines, replacing stale
  or illustrative rows. Source GUIDs distinguish two invoices for the same
  party on the same day. The full 90-day chart range is fetched instead of a
  silent 4,000-line cap. Active source states drive sales and receipt reporting.
- Open pages refresh every 30 seconds when visible, and on reconnect/return.
  A Refresh button is available on My orders. Refresh preserves draft quantities
  and quotes, defers while a field is being edited or a submission is in flight,
  and refuses to discard an unsaved memory-only cart after a storage failure.
- An open order drawer can receive the new invoice details. The sidebar reports
  when data was loaded, rather than a hardcoded “4 min ago” claim.
- Normal order statuses are Order placed, At the office / awaiting invoicing,
  and Invoiced. A confirmed current invoice does not wait for a later Tally edit.
  Only a confirmed invoice whose detail snapshot has not arrived displays
  “Details are loading.” Newly submitted orders retain their line details in
  the browser immediately while awaiting invoicing.

## Rollout

Deploying/restarting has not been implied merely by passing tests.

Completed in this session: the additive cloud metadata upgrade and publication
of 7,952 current vouchers / 21,240 current lines. A subsequent cloud read verified
WEB-1029942543: original order 252, invoice 4440, current invoice 672,
AlterID 28621, active, quantity 8 at rate 84. API/web deployment and restarting
the running agent remain outstanding.

1. Apply the additive history metadata upgrade and publish current local source
   records, using the intended cloud configuration:

   ```powershell
   python tally_updates.py --publish --upgrade-schema
   ```

   This checks cloud/archive company agreement, opens SQLite read-only, and
   writes only the cloud sales projection and its three new metadata columns.
   It neither contacts Tally nor creates invoices. Both schema changes and
   publication share one transaction. Normal schema creation includes the
   upgrade as well. Standalone schema-only command:
   `python cloud_db.py --tally-updates-schema`.
2. Deploy the updated API and complete `web/` together; asset versions are
   updated. Existing older clients remain able to read the first five line-array
   fields, but need the new frontend to display current invoice details.
3. Restart the single agent from the updated project directory to activate
   minute-cycle edit publishing and the non-truncating slow publisher. An old
   running process retains its imported old implementation until restart.
4. Reload the website and inspect voucher 4440: current invoice 672, quantity 8,
   rate 84; original order 252 stays internal. Verify the dashboard's source
   amount and authorized/unauthorized accounts.

## Tests and limits

Visit and insight refresh corrections:

- Manual check-ins are stored in browser localStorage per signed-in account
  and company, with a real visit timestamp. Reload restores them; background
  refresh merges them without duplicates; a previous day's visit is not today's
  checkmark. Storage failures produce a visible warning. This is same-device
  persistence, not a cloud visit log or cross-device/team check-in sync.
- Confirmed invoice activity is reconstructed per source GUID and salesperson,
  using the current invoice header total where supplied. Submitted orders fill
  the unbilled activity; confirmed invoices do not count a second time. Manual
  check-ins carry no duplicate order revenue. The API now appends the current
  invoice total as the eighth line-array field for this purpose.
- Customer rates and reorder/frequency facts both publish changed rows on the
  regular fast push. The 15-minute insights job now recomputes even with an
  unchanged voucher watermark, so day-based recency and stock cover can age.
  New voucher versions still make that job due earlier.
- Frequency counts distinct invoices among the last 10 actual invoices, not
  distinct invoice dates. Reorder cycles use gaps between distinct purchase
  dates. With insufficient dates, cycle/due stays unknown while observed
  frequency remains available.
- Typical cadence with the updated single agent running: voucher reads and
  cloud pushes every 60 seconds, insights every 900 seconds or earlier after
  source edits, visible browser refresh every 30 seconds. These are scheduling
  intervals, not hard deadlines; Tally availability, job duration and the
  existing API catalogue/status cache can add delay.

```powershell
python -m unittest discover -s tests -p test_tally_updates.py -v
node --test tests/order_recovery.test.cjs
```

Real Postgres SQL was also checked with temporary tables, with publication
running inside a read-only transaction after temporary table creation:

```powershell
python tests/check_tally_updates_postgres.py --temporary-tables
```

This checks replacement, repeated publication and cancellation without touching
permanent application tables. API tests verify current-vs-original amounts and
route protection; browser-script tests verify invoice/print content, dashboard
replacement and draft-preserving refresh. Visual device/browser acceptance and
end-to-end live deployment verification are separate checks.
