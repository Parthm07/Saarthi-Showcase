# Saarthi desktop and phone system — proposed design

Updated 30 October 2026. This document records the product direction; it does
not claim that the desktop/mobile packaging or edit propagation is implemented.

## Products and access

One phone application has role-based views. A salesman receives the sales
workspace. An admin receives a compact company dashboard as well as permitted
operational screens. Windows provides the full admin workspace.

| Capability | Windows admin | Phone admin | Phone salesman |
|---|---|---|---|
| Company overview and sync health | Detailed | Compact overview and alerts | Relevant freshness/order status |
| All salesmen's orders | Full search, filters, drill-down, comparison | Recent orders, filters, individual details | Own orders |
| Current Tally invoice and changes | Full authorized details and history | Summary plus individual invoice details | Relevant authorized invoices |
| Analytics | Detailed periods, comparisons, tables and exports | Essential charts and totals | Own/assigned sales information |
| Parties, stock and outstanding | Full authorized sales workspace | Essential lookup and summaries | Assigned parties and permitted catalogue |
| New party creation | Admin only | Optional admin workflow | Forbidden |
| Local agent setup and maintenance | On designated Tally computer | Health monitoring; remote controls only if explicitly enabled | Forbidden |
| Permissions, bulk work and diagnostics | Full administrative workspace | Reduced everyday controls | Forbidden |

The exact mobile admin feature list remains a product decision. A smaller
screen changes the interface, not the truth of the underlying records. Both
admin views must use the same metric definitions and include a data-as-of time.
Permissions are enforced by the cloud backend. Hiding a button is not access
control, and a client-supplied device type must not grant permissions.

## Deployment and agent ownership

- Windows admin installer on the designated Tally computer supervises one agent.
- Opening the admin application attaches to the existing agent or starts it,
  then requests a bounded catch-up sync. Another dashboard window does not
  create another worker.
- A machine-wide lock and stable data directory outside source checkouts prevent
  the duplicate-folder agents encountered during testing.
- Closing the window normally leaves the agent running in the tray. An explicit
  shutdown command stops syncing. Windows login startup is managed in one place.
- Additional Windows admin installations are cloud viewers unless deliberately
  configured as the designated Tally connector. Mobile admins never run a second
  local Tally agent.
- Phones and admin clients use authenticated cloud requests; they do not receive
  database credentials or direct access to Tally's port.
- With the designated computer offline, the cloud still accepts valid orders,
  but Tally changes and invoice creation wait. Clients show freshness accurately.

## Two data flows

Website/phone order flow:

    Sales app -> cloud saved order -> local agent/SQLite -> Tally invoice
                  <- linked invoice number and verified result <-

Tally-origin change flow, including entries created without the sales app:

    Tally create/edit/cancel -> agent -> local versions/current projection
      -> authorized cloud projections -> Windows admin and phone views

The cloud may retain an admin-only accounting projection alongside the smaller
sales projection if broader financial reporting is required. The existing
sales-only cloud projection intentionally excludes purchases and supplier costs;
new admin data must not widen salesperson access.

## Authority and record identity

| Record/fact | Authority |
|---|---|
| Submitted customer request, original quote and submitting salesman | Cloud order |
| Draft before submission | User/device draft storage |
| Actual posted voucher, quantities, rates and amount | Tally |
| User access and route assignments | Cloud administration |
| Observed source versions, checkpoints and recoverable work | Local archive/agent |
| Dashboard totals | Derived from the appropriate authoritative records |

An order and its invoice are different records, linked using the application
reference and Tally GUID. Editing the invoice does not overwrite the original
submitted order. Show ordered versus actually invoiced values where they differ.

Use company identity plus GUID for source identity. Voucher numbers and party
names are display attributes, not immutable identities. AlterID identifies a
new observed source version. If an original application's reference is later
edited in Tally, the existing GUID link must keep the invoice attached.

## Tally edit propagation

1. The agent detects changed source versions, including backdated edits. It does
   not limit detection to vouchers dated today.
2. It validates company and response identity before advancing trusted state.
3. SQLite retains the prior version, stores the new version, and updates its
   current projection. An observed change can be described without pretending
   to know the human editor or exact edit time when Tally did not provide them.
4. Dependent facts are invalidated: invoice details, sales totals, relevant
   customer history/rates, reconstructed ageing, and stock/balance freshness.
   Stock and balances still need their own Tally observations; they must not
   merely be guessed from one edited invoice.
5. The changed invoice and related cloud projections update transactionally.
   Store the source version and observation time so older arrivals cannot
   replace newer data and screens can identify their freshness.
6. Online clients refresh affected authorized records, initially through bounded
   foreground/reconnect polling. Notifications or a persistent update connection
   can be added later. App startup alone is not sufficient refresh behavior.
7. Offline clients retain the last observed data with a stale/as-of indication
   and fetch the current authorized version on reconnect.

Example: the salesman submitted 10 pieces at 20 (order value 200). The office
changes the linked Tally invoice to 8 pieces at 20 (invoice value 160). Windows
and permitted phone views show the current invoice as 160, retain the original
order as 200, and show that the linked invoice differs from the order. Sales
reporting uses the invoiced value; order-taking reports may use the ordered value.

Cancellation or deletion must update the linked invoice state and reporting
without silently erasing history or causing automatic recreation of the bill.
Deletion needs validated reconciliation because incremental counters alone do
not establish absence. Returns/credit notes are their own linked documents.

## Who receives a change

- Company admins may view the authorized company data on both platforms.
- Salesmen see assigned-party data and their own order records according to
  server-side authorization. The backend applies access checks when fetching
  a changed record as well as when announcing an update.
- A Tally-entered invoice without a recorded submitting salesman must not be
  attributed to a guessed salesman. Route visibility and sales attribution are
  different facts.
- Define historical-order visibility after a route transfer explicitly; owning
  an old order does not automatically grant all current financial data for that
  party. User deactivation is enforced on future requests.
- Never broadcast full customer/accounting payloads to every phone and rely on
  the interface to hide other parties or supplier costs.

## Implementation gaps observed in the present code

- Voucher sync already uses AlterID and local version storage. This is a useful
  foundation for detecting edits.
- Agent history push currently runs every 3,600 seconds. That is not a suitable
  sole path for prompt visibility of edited invoice details.
- `push_confirmations` primarily copies invoice identity/status and only writes
  when the voucher number differs. It does not maintain current edited invoice
  quantities, prices and amount alongside the original order.
- The browser does not yet provide comprehensive automatic foreground refresh
  of those projections. Merely packaging the current page will not solve this.
- Complete validated deletion/cancellation propagation, metric invalidation,
  recovery after rollback, and global agent ownership require further work.
- The admin dashboard's earlier initialization/data-completeness findings must
  be resolved before presenting its figures as authoritative.

## Build sequence and acceptance evidence

1. Stabilize one agent/data directory and establish reliable invoice/recovery
   behavior before packaging it.
2. Implement linked current-invoice projections and Tally-edit propagation.
3. Establish shared reporting definitions and authorization for all three views.
4. Build detailed Windows admin and compact phone admin interfaces.
5. Package Windows and phone applications with durable drafts, managed startup,
   authenticated controls, updates and backup/restore behavior.

Acceptance tests must exercise real representative Tally edits in a test
company: quantity/rate/date/party edits, backdating, cancellation, deletion,
returns, renamed masters and changes made while offline. Verify the resulting
source version and details in local SQLite, cloud, Windows and both authorized
and unauthorized phone accounts. Do not accept only a heartbeat or matching
grand total as evidence. Establish a measured propagation target under normal
load rather than promising immediate visibility independent of Tally/laptop
availability.
