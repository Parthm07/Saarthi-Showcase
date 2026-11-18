# Hosting, retention and data ownership

## Railway serves the website. The local database configuration points to Neon.

The address used for Saarthi in this conversation is a Railway web URL. The deployment entry point starts the FastAPI web service. The current local project's `CLOUD_DB_URL` points to a Neon host. Both the cloud API and office agent take their database address from configuration. The deployed web service can have its own environment variable, so checking the local configuration establishes the local setting. Confirm the host in the web service's deployed `CLOUD_DB_URL` to establish that both processes use the same database and branch. The connection URL contains credentials.

Older source comments refer to Railway as the database provider. Those comments and the web URL explain the earlier Railway attribution. Neither establishes where the current PostgreSQL instance lives.

## Application retention by table

| Data | Current source behavior |
| --- | --- |
| `cloud_order`, `cloud_order_line` | Eligible for cleanup **more than seven days after `completed_at`**. The order must be invoiced and picked up, have usable retry identity and fingerprint, and have its retry protection cleared. Holds, source verification and backup checks can prevent deletion. The compact receipt is written in the same transaction before detailed rows are removed |
| `history_voucher`, `history_line` | Cleanup uses the **Tally business date** against a seven-day cutoff. Protected, held or still-linked records may remain. Cleanup also checks the archived version before deletion. The publisher uses a recent-detail window when admitting old records |
| `sales_visit` | After the local copy commits, the cloud row can be marked pulled. Pulled rows with a visit timestamp older than seven days are deleted by the visit sync |
| `order_receipt` | Holds the compact reply and identity for a retired order. The current cleanup has no age-based deletion rule for these receipts |
| `daily_sales`, `party_rate`, `party_buy` | Republished derived data. Older detailed invoices can be deleted while summaries survive. Rows can be replaced or removed when the underlying calculation changes |
| `item`, `customer`, and media | Current projections. Pushes update them and can remove entries absent from the projection. Unreferenced image data is cleaned up |
| `app_user`, `user_route`, `prospect` | No blanket seven-day expiry. These are website records with their own update or removal behavior |
| `desktop_grant` | A grant is redeemable once within five minutes. The approval handler deletes grants that expired more than one day ago |

The seven-day policy runs through the office agent. It depends on active jobs, a matching company, fresh published facts and a verified local backup for order/history cleanup. Older rows may remain when those conditions are unmet. The source defines the policy. The live row ages, the deployed migrations and recent cleanup execution were not verified for this guide.

Neon's **storage allowance and billing** are plan-dependent. Its **restore history window** governs how far back the database can be restored. Saarthi's deletion rules determine when rows leave the active database. These are three separate limits. [Neon plans](https://neon.com/docs/introduction/plans) · [Neon history window](https://neon.com/docs/postgres/backup-restore/history-window)

Sources: [Order and voucher cleanup](../../cloud_retention.py), [visit pickup and pruning](../../sales_visits.py), [history publication](../../tally_updates.py), [desktop approval](../../api.py#L1199).

## Who owns the data

| Record | Authoritative system | What is copied |
| --- | --- | --- |
| Invoice, stock movement, or customer ledger | Tally | Observed versions or snapshots go to `tally_raw.db`. The agent publishes selected fields to PostgreSQL |
| Original website order | PostgreSQL at submission | The agent copies order and line details into `shop_order` and `shop_order_line`. After eligible cleanup, SQLite retains the detailed copy and PostgreSQL keeps the retry receipt |
| Visit recorded on the website | PostgreSQL at submission | Picked-up visit facts are copied into local `sales_visit`. The cloud copy can age out |
| Website user and route assignment | PostgreSQL | Windows Admin can read and edit these through the website API. The standard sync does not mirror those account tables into SQLite |
| Shop proposal and approval record | PostgreSQL | Approved proposals can create a Tally ledger. The ledger later appears in the archive. The proposal, approval notes and approval history remain cloud application data |
| Order acknowledgement, archived retry reply, desktop connection grant | PostgreSQL | The normal order and visit pickup jobs do not replicate these cloud records |
| Desktop request, local reorder policy or sales target | `saarthi_admin.db` | The desktop request may produce a Tally result. These local settings and workflow records have their own store |

The office archive is the long-term record of **what Saarthi observed in Tally** and of **orders and visits copied down**. PostgreSQL contains original website state as well as projections. An order that has not been picked up exists only in the cloud order store. If the two stores disagree, first identify whether you are looking at a website submission, an office copy, or the accounting result.

Sources: [Order pickup](../../tally_cloud.py#L353), [approved parties](../../tally_cloud.py#L435), [visit pickup](../../sales_visits.py), [website account management](../../api.py#L628).
