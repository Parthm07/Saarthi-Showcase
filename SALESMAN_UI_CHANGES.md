# Sales catalogue and mobile ordering — 31 October 2026

## Behaviour

- Every signed-in salesperson can browse and order for all sales parties.
  Route membership is now a filter, not an access restriction. The local
  customer catalogue still only includes the Sundry Debtors tree, with the
  existing non-shop exclusions. Creditors and cash/bank ledgers are excluded.
- Party lists render 50 rows per page. Search considers the entire loaded
  sales-customer snapshot, across routes and pages. Route chips use actual
  Tally customer groups. Choosing a chip clears the global search.
- Product search considers the entire synced catalogue before paging and
  resets the category to All items. Choosing a category clears the search.
- Live product names remain the exact names extracted from Tally StockItem
  masters into the local archive and published to cloud item rows. No name
  rewriting or external product catalogue is introduced. MRP embedded in the
  name stays; the separate MRP price field is hidden on sales cards.
- Our rate uses the latest active, non-optional Sales invoice price for that
  product across customers. Invoice date, then AlterID, then GUID determine
  ordering. Cancellation/deletion causes a rebuild from the remaining valid
  invoices. If never invoiced, Tally StandardPrice remains the fallback;
  if neither exists the salesperson must enter a price.
- The editable quote continues to prefer this customer's own last billed
  rate, with the existing outlier guard. Existing drafts retain their quoted
  prices. Product rate labels display paise rather than rounding to rupees.
- Buying-frequency counts, purchase counts, due-cycle text, and price
  comparison badges are removed from sales cards. The underlying facts
  remain available internally. Review uses the normal accent colour.
- Unknown, zero, and negative stock receive a card border and explicit stock
  warning. Add/order remains available. Stock warnings do not reject orders.
- Bottom navigation occupies its own row outside the scrolling content.
  The compact order bar shows only the total and Review button. Navigation
  and the order bar hide while the phone keyboard is open and return when it
  closes. The checkout confirmation bar also stays above navigation.
- The normal Draft saved notice is removed inside parties. Saved drafts
  remain listed on the party-selection screen. Storage errors, changed-price
  and stock warnings, uncertain confirmation, and safe retry remain active.
- Other salespeople's submitted orders and admin commands retain their own
  server-side permissions. Optional preferred-route fields remain for admin
  use; they no longer restrict customer access.

## Data and rollout

The local `item_invoice_rate` table is created automatically by `shop.open_db`
on agent startup. It contains item name, last invoice date/rate, voucher GUID,
and source AlterID. `rebuild_party_rates` rebuilds it alongside customer rates.
The regular cloud push publishes the resulting reference rate into existing
`item.rate`; no new cloud columns are required for these changes.

Deploy the API and web assets together, then restart the single laptop agent
using the updated files. Allow its rates rebuild and cloud push to finish,
then reload the website. Prior cloud schema upgrades are still required if
they have not already been deployed. This change does not run live cleanup,
submit orders, or modify Tally vouchers.

## Verification

- Node browser-logic suite: 36 passing tests, including catalogue search,
  route chips, customer pagination, stock warnings, decimal rates, keyboard
  visibility, draft persistence, and safe retries.
- Python suite: 37 passing tests, including latest invoice pricing,
  cancellation fallback, cloud rate selection, exact master names, creditor
  exclusion, customer access, and transaction/retry handling.
- Local browser preview at 390 × 844: confirmed full-catalogue search across
  a selected category and measured both order/review bars above navigation.
  Uses demo fixtures; no actual Tally order was placed.
- Keyboard behaviour has a deterministic viewport regression test. A physical
  Android/iPhone keyboard was not available during verification.

Pagination currently bounds rendered cards. The API still loads the complete
customer and product snapshot so search can cover everything without another
network request; this is not server-side pagination.
