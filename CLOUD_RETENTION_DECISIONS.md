# Cloud retention decisions

Recorded from the discussion on 30 October 2026. The implementation below is
prepared locally; deployment and applying the new cloud migration remain
separate steps. No live cleanup was run while building or testing it.

## Confirmed: protect unresolved orders and retries

- A submission with an uncertain response keeps its exact request body and
  request UUID on the submitting device until confirmation is obtained.
- A saved order awaiting Tally invoicing keeps its full cloud header and lines,
  download/status information, and any confirmed invoice link until resolved.
  Seven days is an alert/escalation threshold, not an automatic deletion date.
- Preserve the original downloaded order, lines, invoice attempts/errors and
  confirmed Tally invoice GUID/number locally. Backups must be verified before
  a future archive policy relies on the laptop copy.
- The cloud retains the submitting user identity, request ID, fingerprint and
  order-number link needed to identify retries. Do not expire this protection
  while an order is unresolved. A post-resolution expiry policy must account
  for old phone retries; a compact record cannot expire safely if clients can
  still submit an old request as a new order.
- Current order storage already preserves accepted submissions and retry
  identifiers. This decision does not require deleting or recreating data.

## Implemented salesman fact and retention policy

- Salesman order reads and visit-report display cover today (India business
  timezone). Admin order reads retain their existing recent-order scope.
- Retain completed order details in the cloud for seven days, then archive only
  after verifying the local copy and backup. The hourly agent retention job
  uses completion time, not submission time. Existing confirmations without a
  completion timestamp receive one when next observed, granting a fresh grace
  period rather than assuming an old completion date.
- Keep long-term daily salesman/customer invoice summaries. Orders placed, confirmed
  invoiced sales and recorded physical visits need separate definitions and
  dates. The implemented daily_sales table summarizes confirmed invoices and
  amounts only, not physical visits or submitted-order counts. Old Tally edits
  rebuild and correct the relevant historical summary. Admin summary screens
  remain for a later design discussion.
- Summaries do not replace customer/product last-price and frequency facts.
- Manual check-ins currently persist on the same browser/device. A shared
  cloud visit log and its retention/backup flow require further implementation.

Customer/product facts continue to use party_rate and party_buy. They contain
last confirmed price/date, a distinct-invoice purchase count, typical quantity,
frequency among the last 10 invoices, reorder interval and recency. They are
rebuilt from all locally available source history; a refresh never increments a
counter. Counts describe the available archive, not an unverified lifetime.

## Deletion vetoes and retry receipts

- cloud_order.retry_protected defaults TRUE, including pre-existing orders.
  The submitting account acknowledges the exact order/request after receiving
  confirmation. Failed acknowledgements remain queued on the device, while
  the server conservatively retains the protection flag.
- cloud_order.retention_hold and history_voucher.retention_hold are explicit
  vetoes for exceptional work; cleanup never clears them. These are database
  controls; no new admin hold-management UI has been added.
- Only status=invoiced, a completion older than seven days, an acknowledged
  retry, no hold, and matching local original lines plus a confirmed source
  snapshot qualify. Missing details, mismatched versions, cancelled/optional
  invoices and unverified orders stay in the cloud.
- A checked cumulative daily SQLite backup is created before any deletion.
  Cloud order/detail deletion and insertion of a compact order_receipt share
  one transaction. The receipt retains user, UUID, fingerprint, order number,
  invoice GUID and the saved confirmation response. Receipts are not expired
  by this policy, so old device retries still cannot recreate an order.
- Old invoice detail is pruned only when its exact source version exists in
  that backup and no retained order/hold still needs it. Publishing skips old
  unprotected detail, preventing the following sync from uploading it again.
  Customer facts and daily totals are recomputed locally even for old edits.
  Existing legacy rows with missing version metadata are first reconciled
  against the local source; already archived rows are not uploaded again.
- Each hourly pass is bounded to 50 candidate orders and 1,000 unprotected old
  voucher headers, so an initial backlog drains gradually. Unverifiable
  candidates are retained for investigation rather than forced through.
- Archived-request responses are marked archived; the browser resolves the
  pending draft without recording a fresh order or visit today.

## Deployment

1. Back up the cloud database using the existing backup procedure. Review the
   seven-day detail window with the operator: old detail rows will be removed
   after activation, while local history, price/pattern facts, daily totals and
   compact retry receipts remain.
2. Apply only the additive upgrade before deploying the API:
   `python cloud_db.py --fact-retention-schema`.
3. Deploy API plus web files together, then restart the single updated laptop
   agent. The new retention job runs hourly (including its first due tick).
4. Configure LOCAL_BACKUP_DIR for a separately backed-up volume if required.
   Default: cloud_backup/local_archive in the project, excluded from Git.
   A same-disk copy alone does not provide disaster recovery from disk loss.

Existing detailed admin charts will cover retained detail (normally seven
days), and now say so explicitly. Older daily sales totals remain in
daily_sales; older product/invoice drill-down remains local. The new admin
summary interface is intentionally not invented as part of this salesman task.

## Validation

`python -m unittest discover -s tests -p test_*.py`

`node --test tests/order_recovery.test.cjs`

Explicit real SQL verification, TEMP tables only:
`python tests/check_fact_retention_postgres.py --temporary-tables`.
This exercises holds, unresolved/recent/mismatched orders, backup verification,
atomic rollback, archived retries, corrections after expiry, cancellation and
stale-fact refusal without touching permanent cloud tables.

## Growth considerations

Likely storage growth comes first from cloud_order_line/history_line and
their parent order/voucher records, then from distinct customer/product pairs
in party_rate and party_buy. Daily totals and a future cloud visit log also
grow by their chosen customer/salesman/date grain.

Query/write load also depends on API polling, repeated history reads, full
catalogue/customer snapshot pushes, joins and indexes. These are architecture
expectations, not measured rankings of the running database. Measure database
size, query duration, connection use and transferred bytes before selecting
cleanup or performance work. Users/permissions and compact retry identifiers
should not be the first retention targets merely to save small rows.
