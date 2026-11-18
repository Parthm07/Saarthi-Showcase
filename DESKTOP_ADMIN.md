# Saarthi Admin — Windows build 0.6.4

## Read-only Tally purchase rates (0.6.4)

Products & stock shows the latest effective purchase invoice rate, purchase unit,
purchase date and an estimated margin against the current reference selling rate.
The purchase rate is net inventory-line amount divided by billed quantity, so line
discounts are included. It is never a manually entered cost or closing valuation.
Purchase invoices, corrections, cancellations and deletions are read from the local
Tally archive. Missing costs and incompatible units show unavailable.

Party and product report estimates use the latest purchase invoice dated on or
before each sale or credit note. Later purchases do not reprice earlier sales.
Profit is inventory-line revenue minus estimated purchase cost. Margin is profit
as a percentage of inventory-line revenue; markup is profit as a percentage of cost.
These estimates exclude additional invoice ledgers, operating expenses and Tally's
FIFO/average valuation. A credit uses the purchase rate available on its own date.
If any billed item lacks a usable rate/quantity, the party total shows unavailable;
it does not report a misleading partial profit. Full Tally purchase history stays local.

Purchase rates have no editing/upload endpoint or form. New product creation is
blocked; create products in Tally and sync them. Existing selling-price editing and
selling-rate CSV uploads remain available. Earlier saved requests remain recoverable.

Website Owner accounts have a searchable Purchase rates view and read-only cost
labels on product cards. The server copies its shared catalogue before adding Owner
costs; salesperson responses never contain these fields. The dedicated cost endpoint
rejects non-Owners. Only one current rate/unit/date/source fact per product is stored
in a separate cloud table; supplier names and purchase vouchers are not published.

Deploy the backend and website code (including purchase_costs.py), then run this
additive migration in the configured cloud environment:

```powershell
python cloud_db.py --purchase-cost-schema
```

Install 0.6.4 and keep the agent/cloud jobs running. After the next catalogue push
and website refresh, Owners can see the rates. Purchase-rate changes must be made
in Tally. No live cloud migration or business Tally write was performed by this task.
This section replaces older notes saying margins are always unavailable.

## Search, sorting and website pictures (0.6.3)

Search prioritises a matching start of the name, then a matching start of a word,
then matches inside words or in other fields. This ordering applies before
pagination, including the sales product cards, stock catalogue and party lists.
Explicit sorting changes the order within the same relevance tier while searching.

Click a table column heading to toggle ascending/descending sorting. Main archive
lists, saved requests, analysis tables and explorer tables sort the entire result
set before paging. Product cards, stock and image selection also have sort menus.
Customer follow-ups can sort by priority, party or missing product count.

In Products & stock → Bulk rates & images, search and tick one or several products
(up to 100 per upload), then choose one PNG/JPEG/WebP picture. The same image is
saved against every selected product. A single product can also be managed from
its Rate / image / reorder button. Images are resized to at most 640 pixels on the
longest side and encoded as WebP for smaller website downloads. Selection remains
when changing the search; Clear selection removes it.

Images are saved locally first, included in local backups, then published by the
existing agent cloud-push job. The cloud stores each unique picture once by its
SHA-256 hash, with small product links; unchanged bytes are not sent every cycle.
Replacing a shared picture changes only the selected products. Unreferenced cloud
pictures are removed during sync. Original local backups remain untouched.
The phone catalogue contains image URLs, not embedded image files. Image requests
require website login and use private browser caching. A new picture appears after
the agent pushes and the website refreshes its catalogue.

Deploy product_media.py, api.py, cloud_db.py and the updated website assets. From
the existing configured cloud environment, run this additive migration once:

```powershell
python cloud_db.py --media-schema
```

Then install the 0.6.3 ZIP and keep the existing cloud jobs enabled. Until the schema
is deployed, image sync reports pending and keeps the local uploads for retry.
No real cloud migration or Tally entry was performed during these development checks.
This image-publication behavior supersedes the older local-only image notes below.

## Focused owner workspace (0.6.2)

- Overview defaults to month to date in India time, with editable automatic date
  filters and an MTD reset. It shows overall sales, current debtor balances,
  credit notes, waiting orders, daily sales hover/focus values, business by route
  and the top 10 products. Click Debtors to inspect the current customer balances.
- Analysis now has Business performance, Sales team and Customer insights.
  Business performance keeps gross sales, current debtors, a matching-duration month-on-month
  comparison, revenue trend, routes, leading parties and attribution. The old
  monthly/yearly, Products and Billing tabs and target/average-invoice clutter
  are removed from the interface. Date/year/all-time scope choices remain.
- Customer insights identify parties who bought last month or in the previous
  three months but have not responded this month, and previous products not yet
  bought. Pending website orders prevent false “no order” alerts. Top 10 parties
  have View all and searchable/exportable rows. Profit, margin on sales and
  markup on cost show unavailable: the existing archive has no reliable Tally
  cost-of-goods valuation. Purchase rates are not silently treated as cost.
- Sales team shows synced visits, orders and billed sales, with individual rep
  drill-down. Visits count one party per salesperson per India-calendar day.
  Missing visit sync is displayed as unavailable, never inferred from invoices.
- Transactions opens on party-first Create sale, with product search/cards,
  quantity and rate controls, stock warnings, and review before saving a request.
  Record purchase and Add product are removed from its navigation. Existing
  saved requests, including purchases, remain visible and safely retryable.
- Products & stock has catalogue, reorder/out-of-stock and bulk upload sections.
  Individual reorder levels/target quantities save locally. Rate changes use
  durable requests and become future quotes only after Tally confirmation.
  Rate-only imports preserve Tally renames. Bulk CSV accepts item_guid, product,
  rate, and previews/validates 1–1000 rows before queuing. Download the template
  from the product workspace. Retries retain the original request IDs.
- Product image batches accept up to 100 PNG/JPEG/WebP files, each up to 1.5 MB
  and 8192×8192 pixels. Match filenames to GUIDs/exact names or map each product
  explicitly; one image per product per batch. Failed rows can retry. Images
  remain local and are included in Admin backups, not uploaded to the phone.

### Visit rollout

Deploy api.py, sales_visits.py and the updated website assets, then apply this
additive migration using the existing configured cloud environment:

```powershell
python cloud_db.py --visit-schema
```

Install the 0.6.2 desktop/agent update and enable the existing live/cloud jobs.
The phone keeps local visits through connection failures and retries only
unacknowledged facts. The endpoint checks login identity, company and customer,
and accepts one fact per rep/day/customer. The agent archives facts in local
sales_visit before cloud acknowledgement. Only acknowledged cloud facts older
than seven days are removed; unpulled facts remain until archived. No historic
visit totals are fabricated for phones that have not synced. This task's tests
did not migrate or modify the actual cloud or Tally company.

The older sections below describe earlier versions; the simplified navigation
and upload/visit behavior above takes precedence in 0.6.2.

## Calendar and party analysis (0.6.1)

Analysis opens on the current calendar year, with January–December monthly
sales, credits, net sales, invoices, buying parties and prior-year changes.
Choose a historical year, or **All time** for year-by-year sales across the
available archive. Missing calendar years are included with zero activity.
The current month/year compares matching elapsed dates last year. Future months
are marked upcoming and excluded from growth. All time ends today; it has no
three-year custom-range limit. Reports describe the available archive, which
can still have historical gaps.

The **Compare with** selector and **Apply filters** button have been removed.
Date, year, route, salesperson and party selections refresh the report
automatically. Custom dates use the preceding equal-length period as the
automatic baseline; yearly views use matching dates last year.

Choose a full party name from the searchable suggestions, or click a customer
in the customer table, to open **Party purchases**. It shows the party's trend,
latest 25 invoices/credit notes and products with quantities bought/returned,
net value, invoice count and latest sales-line rate in the selected period.
Units stay separate. Without a party filter this table shows each party/product
combination. Calendar tables and product purchases can be searched and exported.
Party and all-time scopes do not reuse a team/date target.

## Complete installation and coordinated updates (0.6.0)

The complete setup now bundles the dashboard **and** Tally agent, including the
runtime needed on a laptop without Python. It can create `tally_raw.db` by reading
the company open in Tally, or preserve an existing Saarthi archive. New imports
start with read-only local syncing; invoicing and cloud jobs are enabled
separately under **Updates & setup**. The same screen installs publisher-signed
release ZIPs, with verified local backups, migrations and failure recovery.

Follow [Deployment and updates](DEPLOYMENT_AND_UPDATES.md) for the first business
laptop installation, obtaining the archive, future builds and optional online
release hosting. The older source-worker instructions below apply to legacy
installations only. The complete managed installer supersedes the old viewer-only
installer; updating Admin and its agent no longer requires Python on that laptop.

This is an offline admin workspace over the complete local SQLite archive.
It does not use the cloud website to render reports. No internet fonts,
charts, scripts or CDN assets are required.

## Purchases, sales and product management

Purchase and sale forms use one searchable party combobox. Type a name or
current group to see matching results immediately, then click a result or use
Arrow keys and Enter. Searches cover the entire customer/supplier list; the
popup shows up to 50 matches at a time. Editing the text clears the party ID
until a result is selected, preventing a sale against a stale selection.
Other invoice fields are preserved. Escape closes the results.

Open **Transactions** to record a no-GST purchase or sale, create a customer or
supplier party, create a product, change its name/future selling price, or attach
a product image. Purchases require supplier bill number/date, a Purchase Accounts
ledger, a godown and product quantity/rate lines. Sales select a sales party,
Sales Accounts ledger, date, godown and product lines. Tally assigns voucher numbers.
New products use existing Tally stock groups and units; quantities enter through
purchases, never by overwriting a closing stock field. Batch-tracked products
must be entered in Tally. GST entries are outside this workflow, as the business
confirmed these purchase bills have no GST.

**Save request for Tally** first saves an immutable local request. The agent
checks it every 15 seconds between other serial jobs. Queued is not invoiced.
Confirmed means the matching voucher/master was read back from Tally. When a
reply is lost, the request becomes uncertain; **Recheck only** reads Tally and
never sends that uncertain import again. An interrupted in-flight request has
the same rule. Definite refusals or failures before sending can be retried.
The request ID prevents replay duplicates; separate intentional sales have
separate IDs, even with the same products. Purchases additionally reject an
existing supplier bill number for that supplier in the same financial year.

The viewer keeps accounting data read-only in `tally_raw.db`. Requests, their
results, and local product-image bytes live beside it in `saarthi_admin.db`.
The one locked agent is the only Tally writer. It checks the bound company,
uses GUIDs for product identity, and refuses stale rename requests. Successful
entries bring forward normal voucher/master/stock/rate/cloud sync jobs.
Stock is not increased optimistically in the UI before confirmation.

A confirmed selling-price change takes effect from today as a Tally standard
selling price, and becomes the global future quote after the synced master
matches it. Existing invoice amounts remain untouched. Customer negotiated
prices remain separate from this global selling price.

Images are local to Windows Admin in this release, keyed by product GUID so
renames preserve the attachment. PNG/JPEG/WebP uploads are limited to 1.5 MB.
They are not uploaded to cloud storage or shown on the phone website yet.
**Create verified backup** saves both databases when the Admin database exists;
the agent also makes an hourly verified Admin database backup under
`cloud_backup/local_archive`, including images and requests. Copy both database
backups to a separate backed-up drive. There is no restore/delete UI.

Update the source files used by the worker as well as the desktop files, and
restart the old agent once to load the new jobs. Updating the viewer alone does
not update a Python process already running old code. This release was tested
with disposable databases and mocked Tally imports; verify a controlled entry
in the actual company before using this workflow for normal bookkeeping.

Implementation references: Tally's [sample XML](https://help.tallysolutions.com/sample-xml/)
and [integration guide](https://help.tallysolutions.com/integration-initiated-from-tpas/).

## Website employee access and automatic agent startup

Open **Website access**, enter your deployed website's HTTPS origin, and click
**Connect as owner**. Your browser opens the website's Google sign-in and an
explicit approval page. Return to Admin and press Refresh after approval.
Add an employee's Google email and name, choose Salesperson or Owner, and save.
Employees sign in using that Google account; no separate employee code or
password is introduced. You can edit names/roles and enable or disable access.
Disabling preserves their order history and takes effect on the next request.
All salespeople can browse all sales parties. Route preferences are retained
when an existing employee is edited.

This screen requires internet and an active website Owner. It never takes a
cloud database password. One-use connection grants expire after five minutes;
scoped desktop tokens last up to one hour and stay in process memory. Owner
role and active status are checked on every website-management request.
Closing the viewer process loses the connection. Local reports still work offline.

Deploy the updated `api.py`, `cloud_db.py`, and the two new web assets
`web/desktop-connect.html` and `web/desktop-connect.js` before connecting.
Apply this additive migration once with the existing backend environment:

```powershell
python cloud_db.py --desktop-access-schema
```

The migration adds only `desktop_grant`. It does not change accounting records.
Building the desktop program does not deploy this cloud update.

Opening Admin normally starts the existing `tally_agent.py` beside the selected
active `tally_raw.db`, or attaches to an already running copy. **Sync & backups**
shows startup status and has Start / check agent and script/interpreter settings.
The worker uses its existing `.env`, Python dependencies, logs and schedule.
It remains running when Admin closes. This does not add a Windows startup task.
A worker detected in another folder blocks a second launch; updated workers
also hold a shared per-Windows-account lock as well as the original folder lock.
Backups or differently named archives cannot automatically launch a worker.
The agent still requires an installed Python with its normal dependencies
(including psycopg), reachable cloud services and the correct Tally company open.
Running does not guarantee an invoice succeeds: the normal refusal checks apply.

### Performance update (0.1.1)

The first build re-decoded all vouchers whenever a five-second cache expired
and read every historical stock/balance snapshot before choosing the latest
per item. The updated build retains decoded voucher metadata until the archive
changes, decodes only new source versions, and reads the newest indexed snapshot
round with individual fallback for items absent from a partial round. It uses
the existing source indexes and does not alter the database.

On the developer's read-only check of the 10,704-voucher local archive, Products
fell from 0.8–1.4 seconds to about 29 ms, Parties from 0.5–0.6 seconds to about
12 ms, and the first Overview from 5–10 seconds to about 0.7 seconds. These are
local data-read timings, not promises for every computer or full window startup.
Navigation also acknowledges the new screen immediately while its data loads.

To update an installed copy, choose **Exit admin**, close its window, then run
the updated installer and select the same existing database. The database and
the independently running Tally agent remain in place.

## Install and use

1. Run `SaarthiAdminSetup.exe` and choose the existing `tally_raw.db` used by
   the current laptop agent. Keep the database in its current location.
2. Click Install. The program installs for your Windows account and creates
   a Start menu shortcut. The viewer includes its Python runtime; the existing
   Tally worker uses the separate installed Python described above.
3. Open **Saarthi Admin** from Start. Edge (or Chrome) supplies a separate
   application window; a normal browser tab is the fallback if neither exists.
4. Admin starts or attaches to the laptop agent. Its successful
   updates appear on the dashboard's next refresh, normally within 30 seconds.

The executables still lack Windows Authenticode signing. The new managed release
ZIPs have separate publisher signatures; these do not remove Smart App Control
restrictions. See the deployment guide before moving to a protected business PC.

## Included

- Overview: active Sales, Receipt and Credit Note totals for selected dates,
  daily sales chart, leading parties, pending/error counts and latest activity.
- Orders: all locally pulled salesman orders, owner from recorded order note,
  status, billing error and current linked Tally invoice. Original submissions
  remain available as internal recovery details.
- Vouchers: every locally archived voucher type, including purchases. Search,
  filtering, current state, item lines, earlier saved versions and raw fields.
- Parties: all accounting ledgers, including customer and supplier groups,
  latest observed balances, source fields, derived facts and voucher history.
- Products: exact Tally names, groups, stock, local rates and original fields.
- Data explorer: every SQLite table, schema column names, whole-table text
  search, 50-row pages, full row inspection and decompressed source records.
- CSV exports: every row in the selected table, including decompressed JSON.
  Spreadsheet formula-like string cells are escaped; numeric values remain numeric.
- Sync & backups: recorded job times, snapshot freshness, recorded invoice
  refusal, full sync-state fields, agent startup/settings, and verified backups.
- Website access: owner-approved employee creation, roles and access toggles.
- Transactions: durable purchase/sale requests and party/product management.

Gross sales and credit notes are shown separately. Do not treat the Sales
metric as net sales after credits. Cancelled, optional and deleted vouchers
are excluded. Old versions never count as extra invoices. A billed order
whose invoice is missing does not fall back to its original submitted total.
Unattributed Tally entries are not assigned to a salesman by guesswork.

## Data ownership and limitations

The program opens the selected archive with SQLite `mode=ro` and
`query_only=ON`. The viewer does not migrate the local archive or directly edit
vouchers, stock, balances or original orders. Its separate worker performs
normal Tally/cloud synchronization and invoicing after startup.
The full archive is browsable; only the records actually extracted by the
existing agent are available. Source fields never extracted from Tally cannot
be reconstructed. Earlier versions carry their historical values and states;
a later deletion is flagged across that voucher's history.

This is a working release, not the final admin feature set. Editing/deleting
previously posted vouchers, one-click catch-up commands,
tray operation, automatic updates, restore controls and larger
analysis reports remain follow-up work. Manual phone visits currently stored
only in a browser are absent from the local archive and cannot appear here yet.

The admin trusts the signed-in Windows account on the Tally computer. It
must not be installed as a shared/public remote dashboard. The HTTP listener
binds only to 127.0.0.1 on a random port. A random per-run session and strict
Host checks protect reads; commands also require the local Origin. Static
serving is an explicit five-file allowlist. No raw SQL command is exposed.

App settings and the instance lock live under `%LOCALAPPDATA%\SaarthiAdmin`.
The installed executable lives under `%LOCALAPPDATA%\Programs\Saarthi Admin`.
Backups default to `%LOCALAPPDATA%\SaarthiAdmin\backups`; copy them to a
separate backed-up drive as well. Database files and secrets are never packaged.
**Exit admin** stops the viewer. Closing the Edge window leaves the viewer
running in the background. The sync agent runs independently.
A second launch attaches to the same viewer instead of launching
another backend. Changing archives requires exiting the current viewer first;
run `SaarthiAdmin.exe --choose-db` to select another.

## Run from source

```powershell
python desktop_app.py --db "D:\Tally Project 2\Tally Project\tally_raw.db"
```

For a browser preview with disposable data, `--serve` prints the local start
URL. Pass `--profile-dir` pointing to a disposable folder to isolate settings.
Preview `--serve` never automatically launches a worker. For a normal viewer
without automatic startup, add `--no-agent`. Explicit Start / check still works.

## Build

```powershell
python -m pip install --target .desktop-build-tools -r requirements-desktop-build.txt
.\build_desktop.ps1 -Python python
```

Build outputs default to the sibling `outputs\saarthi-admin` directory.
PyInstaller bundles the desktop launcher, archive reader, worker controller,
website-access client, Python's standard library, and the `desktop` assets.
The installer bundles the viewer
executable and its small installation UI. It never bundles `.env`, the cloud
backend, the agent, `tally_raw.db`, backups or the Tally company directory.
Packaging follows the official [PyInstaller usage documentation](https://pyinstaller.org/en/stable/usage.html).

## Verify

```powershell
python -m unittest discover -s tests -p 'test_*.py'
node --test tests/order_recovery.test.cjs tests/desktop_ui.test.cjs tests/analysis_ui.test.cjs
python tests/check_desktop_access_postgres.py --temporary-tables
```

Tests use disposable SQLite archives and a loopback server, with no cloud or
Tally imports. Worker launches are mocked. The explicit Postgres check uses only
temporary tables, including testing the migration. Tests cover current-vs-original values, inactive vouchers,
historical versions, source names, suppliers, full raw data, read-only
enforcement, verified backup, local session/Origin protections, duplicate worker
prevention, scoped authorization, one-use grants, expiry and access revocation.


## Offline Analysis

The Analysis tab is implemented in `desktop_analysis.py` and `desktop/analysis.js`.
It reads the current active versions in the local archive and runs without the
cloud. It adds no cloud tables or network requests. Original Tally records are
read-only; editable targets are saved in the separate `saarthi_admin.db`, in
`analysis_goal`, and included in the existing Admin backup.

- Business performance: gross sales, credit notes, net sales, average invoice,
  daily net sales chart, observed growth, repeat buyers and top-five customer
  concentration. A locally entered net-sales target has a progress gauge.
- Sales team: rankings by invoiced gross sales, customers, invoice averages,
  comparison revenue, submitted/billed orders, billing completion and all-date
  unresolved work. Saved targets for individual salespeople appear in the table.
- Customers and routes: revenue, credits, current debit balance, last observed
  purchase, returning previous-period customers and prior buyers now inactive.
- Products: inventory-line sales, credit notes, net quantities separated by unit,
  distinct buying parties, invoices and latest stock snapshot.
- Billing and aging: website-order cohort counts, completion and unresolved work
  sorted by age. A billed order requires a current active linked Sales voucher.

Filters include custom dates, daily/weekly/monthly/quarterly/year-to-date presets,
calendar years, all time, route, salesperson and party. They apply automatically.
Custom dates compare the previous equal-day period; yearly views compare matching
dates last year. Dates are interpreted in India time.
Targets are keyed to the exact dates, route and salesperson. A quarterly target
does not automatically apply to a partial quarter. Zero clears a saved target.

Salesperson ownership uses only the order's exact Tally GUID link and original
salesperson identity. Direct Tally sales and unlinked credit notes are Unattributed.
No assumption is made from a customer's current route. Per-salesperson net targets
cannot allocate unlinked credit notes; gross revenue remains the leaderboard basis.
Current route master groups are used for territory filters, not historical routes.
Growth is hidden for a missing/zero baseline or when the comparison begins before
the earliest observed sale. Local history can still have gaps. First observed
purchase does not prove a customer was new to the business.

The product table uses inventory amounts and can differ from invoice totals when
other ledgers/taxes/rounding appear. Stock and debit balances are current snapshots,
not period-end values. Credit notes are subtracted on their own voucher dates.
The billing funnel uses submission dates; its completion rate is not a sales win
rate or forecast. Waiting alerts intentionally include older unresolved work too.

No local opportunity, calls, emails, meetings, lead-stage or loss-reason data exists.
Win rates, stage velocity, weighted forecasts and profit are therefore not fabricated.
Those metrics require a separate recording workflow and reliable costs/CRM history.

Tables search all available matching rows, render 20 per page, and export every
matching row to CSV, including filter/definition metadata and spreadsheet-formula
escaping. Immutable voucher inventory versions are cached to keep repeat screen
loads fast. Automatic refresh runs every 30 seconds while no form is being edited.

Validation commands:

```powershell
python -m unittest discover -s tests -p 'test_*.py'
node --test tests/order_recovery.test.cjs tests/desktop_ui.test.cjs tests/analysis_ui.test.cjs
```

## Product search and all Tally orders

Product fields in purchase and sales lines, Edit product and Product image use a single searchable picker. Results update as you type across the entire Tally-synced catalogue, including product groups and units; name prefixes appear first. Select a result with a click or arrow keys and Enter. Adding/removing lines preserves the remaining selections, quantities and rates. Purchase rates remain entered by the admin; sales use the selected product’s available selling rate. Add product still accepts a new exact name.

Orders includes all synced Sales vouchers alongside website requests. Invoices linked by Tally GUID appear once, with their current date, number, party and amount. Direct Tally sales open voucher details. Inactive invoices stay clearly labelled with no active sales value. See SALES_APP_INSTALL.md for the separately installable sales phone website.
