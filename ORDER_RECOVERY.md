# Saved drafts and safe order retries

## Policy update — 30 October 2026

New-party creation is now office/admin-only. The salesman sees existing
parties; the Add party option is hidden and POST /api/prospects returns 403
for a salesperson even if called directly. Admin creation and the existing
approval queue remain available. Existing proposals/orders are not deleted.

The laptop invoice job now has a 120-second interval and runs during working
hours as well as outside them. Historical archive/reconciliation retain their
maintenance gates. Before automatic invoicing, the loaded Tally company must
match this archive's company binding. A stuck Tally retains its existing
30-minute backoff; the two-minute cadence does not override a known refusal.

Restart the currently running agent **from this updated project folder** to
load the new schedule. For a terminal-run agent, press Ctrl+C in its terminal,
then run `python tally_agent.py` there again. An agent started by Windows Task
Scheduler needs its existing task stopped/restarted instead. Do not leave an
old project copy running alongside this one. Deploy the updated API and web
files for the creation restriction to take effect on the live website.

Orders are downloaded on the separate roughly one-minute push/pull cycle.
Combined with the two-minute invoice interval and the 15-second scheduler
tick, normal delivery is approximately two to three minutes plus processing,
not a strict two-minute deadline. Offline connectivity, other serial jobs,
Tally being busy, or a rejected invoice can extend this. This change has not
restarted the running agent or created a test voucher in live Tally.

Isolated schedule/company-guard checks: `python -m unittest discover -s tests -p test_agent_policy.py -v`.

Implemented 29 October 2026. This change addresses interrupted order entry,
uncertain submission results, and the unsafe production-to-demo fallback.
It does not implement an offline synchronization queue or change stock allocation.

## Salesman behavior

- Selecting a party, changing quantities, and typing prices automatically saves
  a draft in browser storage on that device. Separate drafts are kept per
  signed-in email, company, and party. Saving runs on input, not only on blur.
- Reloading after reconnecting restores the selected party, products, quantities,
  and quoted prices. The home screen also lists drafts that can be resumed.
- A restored draft keeps the quote and flags differences from current prices,
  insufficient displayed stock, or removed items. Removed items remain visible
  in the draft and must be removed before a new submission.
- Pressing Place order persists a unique submission ID and freezes the body
  before the request is sent. An uncertain request shows its original items
  and a **Check / Retry** button, including after a reload.
- Retry uses exactly that ID and body. The API returns the existing saved order
  if the first request committed. It creates an order only if that request has
  not been saved. An ID reused for different content receives HTTP 409.
- Double-clicks are ignored while sending. A second tab reuses an already
  persisted pending submission rather than creating a new ID for that party.
- Only confirmed success clears the draft. Validation errors preserve its
  items and allow corrections. Timeouts, lost replies and server errors leave
  the original request available for safe checking. Saved requests can also be
  resolved after their party or product disappears from the refreshed list.
- Browser requests time out after 30 seconds. A timeout is an uncertain result,
  never evidence that the server did not save the order.
- If local storage fails, an on-screen message says so and submission pauses;
  the app does not promise that a memory-only draft will survive closing.

Drafts are device/browser-local, not a cross-device backup. Clearing site data
or using a browser mode that discards storage removes local drafts. The existing
server allowlist/route checks remain authoritative; locally retaining a draft
does not grant access to a party on the server.

Production startup failures show **Retry connection**. Demo mode is explicitly
selected using `?demo=1`; it never activates because an API call failed.
The API serves this repository's `web/` by default instead of silently picking
an older Desktop prototype. `WEB_DIR` remains an explicit override.

## Server design

The existing `cloud_order` row gains `client_request_id` and
`request_fingerprint`. A partial unique index scopes request IDs to the
salesperson. Request fingerprints include customer, quantities, quoted rates,
and note; line ordering does not change the fingerprint.

A transaction-scoped Postgres advisory lock serializes retries for the same
user and ID. Under the normal READ COMMITTED isolation level, the lookup after
the lock sees the first transaction's committed row. The order and lines share
one transaction; rollback leaves that ID available to retry. Order-number
collisions retry an atomic `INSERT ... ON CONFLICT ... RETURNING` rather than a
race-prone existence check.

The shared FastAPI database dependency now uses function scope so transaction
commit completes **before** HTTP success is sent. Returning a success before
the database commits could otherwise erase the device draft on a failed save.

Older clients may omit `request_id` during rollout, but their submissions do
not have safe replay protection. The updated browser always sends one. Existing
cloud backup/restore includes the new columns through its existing row dump.

## Deployment order

These steps have been prepared but have **not** been run against the live cloud.

1. With the intended cloud database configured, apply only the additive upgrade:

   ```powershell
   python cloud_db.py --order-retry-schema
   ```

   This adds two nullable columns and the unique index; it does not run the
   unrelated historical timestamp conversions. Normal full schema creation also
   includes this upgrade. Re-running the command is supported.
2. Deploy the updated API and the complete `web/` together, including `drafts.js`.
   Asset versions in index.html were refreshed. Deploy the API before or with
   the browser so new submission IDs are never silently ignored by an old API.
3. Verify WEB_DIR points to the updated web folder if explicitly configured.
   The legacy `sync_web.py` copies from a Desktop directory; do not overwrite
   these repository changes with a stale prototype. Its asset allowlist now
   requires `drafts.js`, so a source missing this module is rejected.
4. Smoke-test a saved draft, reload, an interrupted submission, and a retry in
   an isolated environment before making real business orders.

## Validation

Run only the isolated tests below. The root-level legacy `test_api.py`,
`test_people.py`, and `test_newparty.py` can mutate live cloud/Tally data and
are not part of this test command.

```powershell
node --test tests/order_recovery.test.cjs
python -m unittest discover -s tests -p test_order_retry.py -v
```

Browser tests run the actual application scripts in a Node VM with isolated
storage and network responses. They cover ten-line draft restoration, account
and company separation, lost-response replay after reload, double-clicks,
pending-order edit protection, two tabs, storage errors, and disappearing data.

API tests call the real handler/models with a cursor double and exercise the
ASGI application for commit-before-response behavior and injected commit failure.
They cover repeat/changed requests, ten retained order lines, user scoping,
catalogue disappearance, line-order normalization and order-number collision.

No live database, Tally writes or deployment is used by these tests. An actual
Postgres concurrency test and device/browser acceptance test remain deployment
checks; the cursor double does not prove Postgres locking behavior.
