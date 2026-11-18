# Saarthi

Saarthi helps a distribution business take orders in the field, prepare invoices in the office, and keep owners informed through a mobile website connected to Tally. It brings together the salesperson visiting a shop, the office team handling billing and inventory, and the owner reviewing business performance.

The project is used by a real business. This showcase contains anonymized examples and a fresh Git history prepared for public review.

## The problem it addresses

A distributor sells many products to shops across different routes. Each shop has its own buying history, outstanding balance, and previously agreed selling prices. During a visit, a salesperson needs enough context to take a useful order quickly. Meanwhile, the office needs the correct customer, products, quantities and rates to prepare the invoice in Tally.

When orders travel through phone calls, messages or handwritten notes, the office has to interpret and enter them again. A salesperson may need to call for a stock check or ask what price a customer last paid. An owner away from the office has to ask someone for updates on orders, invoices and field visits.

Saarthi provides a shared workflow for those handoffs. The field team captures structured orders on their phones. A Windows agent transfers orders to the office's Tally installation and publishes updated data to the website. The owner can review the information remotely.

## Who uses Saarthi

| Person | What Saarthi helps them do |
| --- | --- |
| Field salesperson | Open a customer, review previous purchases and stock, take an order, record a visit, and follow submitted orders. |
| Office team | Review orders and Tally vouchers, manage selling rates and product presentation, and control synchronization through the Windows Admin application. |
| Business owner | Review sales, invoices, customer and team activity, access purchase rates, and pause or resume the laptop's scheduled work from the website. |

## A sales visit from start to finish

Imagine a salesperson visiting a neighbourhood shop that regularly buys soap, hair oil and household cleaning products.

1. **Open the shop's account.** The salesperson selects the customer and sees the account context, including outstanding dues and products bought before.
2. **Build an informed order.** Previously purchased products are grouped together, with available stock prioritized within the relevant groups. The salesperson can search the wider catalogue and check stock quantities while discussing the order.
3. **Use the right starting price.** The rate field gives priority to the business's configured selling rate. If it is unavailable, the customer's last sold price is used where available. The salesperson can enter the order's agreed rate and quantity.
4. **Review before submitting.** The review screen shows the items and total, supports an invoice date, and suggests previously purchased or relevant products that could be added to the bill.
5. **Send the order to the office.** The cloud API saves the submission. The laptop agent picks it up for processing in Tally when the required services and live invoicing are available.
6. **Follow the outcome.** The submitted order and its billing information can be reviewed through the application's order views. Updated Tally data is synchronized back through the local archive and cloud.

The draft is saved on the device during order entry. If the connection drops while placing the order, Saarthi retains the submission identity and offers a check/retry flow so the backend can recognize a repeated request.

## More than order entry

**Customer context during a visit.** Purchase history helps a salesperson remember a shop's usual basket and identify items to discuss. Visit records also make activity visible when a customer visit produces no order.

**Product management in the office.** The office can configure its selling rates, assign product images, and hide selected products from the website. Bulk tools help manage a larger catalogue. Product and purchase information comes from Tally, while Saarthi manages the website's presentation and its own selling rates.

**Visibility for an owner away from the office.** Owner views bring together sales, order status, invoices, visits and team activity. Purchase rates are restricted to owners. These views support everyday questions about what has been billed, which orders need attention and how the field team is performing.

**A usable Windows application for the business laptop.** Admin provides access to the local archive and agent controls. Publisher-signed updates let the business install newer versions through an update feed. Owners can also send pause and resume commands from the website to control scheduled laptop work.

## Working with real operating conditions

The workflow accounts for interrupted connections, a laptop being offline, and records being edited in Tally. Saved drafts and persistent submission identities support recovery during order placement. Company identity checks help keep the agent bound to the intended Tally company. Voucher synchronization includes reconciliation of invoice edits and deletions.

The local SQLite archive supports office-side history and processing, while PostgreSQL holds the cloud data used by the website. Synchronization depends on the business laptop, agent, Tally and network being available. Submitted cloud orders await pickup while the laptop is offline or syncing is paused.

## Why I built it

Saarthi grew around the needs of an operating distribution business. Building it involved connecting mobile order entry, a Windows application, cloud APIs and an existing accounting system, then handling the failures and corrections that appear during everyday use.

The result is a project that demonstrates both product workflow and engineering work. Customer history informs the ordering screen, access rules determine what each person can see, and synchronization and recovery logic carry an order across the phone, cloud and office laptop.

## Try the demo

With Python installed, serve the sample website from this repository.

```sh
python -m http.server 8000 --directory web
```

Open **http://localhost:8000/?demo=1**. This explicit demo mode uses illustrative browser data. Explore parties, select products and review a sample order. It requires neither a cloud database nor a Tally connection. Production integrations require the setup described below.

## What it does

- Mobile product catalogue with customer purchase history, stock, editable order quantities, and selling prices.
- Saved order drafts and submission retries using a persistent request identity.
- Local Windows application for product visibility, images, selling rates, and synchronization controls.
- Tally invoice integration with company identity verification and edit/deletion reconciliation.
- Owner views for sales, invoices, visits, customer activity, and team performance.
- Publisher-signed Windows updates and owner controls for pausing the laptop's scheduled work.

## Architecture

```text
Salesperson / owner phone
          |
          v
JavaScript website + FastAPI
          |
          v
PostgreSQL cloud database
          ^
          |
Windows agent + local SQLite archive
          |
          v
       TallyPrime
```

The agent also checks the website's control endpoint to receive owner pause and resume commands.

## Technology

Python, FastAPI, JavaScript, HTML/CSS, SQLite, PostgreSQL, Neon, Railway, Tally XML integration, and packaged Windows applications.

## Code map

| Area | Entry points |
| --- | --- |
| Cloud API and access control | `api.py`, `cloud_db.py` |
| Mobile website | `web/` |
| Tally connector and scheduled work | `tally_connector.py`, `tally_agent.py`, `tally_store.py` |
| Local Windows application | `desktop_app.py`, `desktop/` |
| Order delivery to Tally | `tally_invoice.py`, `tally_cloud.py` |
| Updates and releases | `build_desktop.ps1`, `desktop_release.py`, `publish_desktop.py` |
| Regression checks | `tests/` |

## Development and integration setup

Start with [DESKTOP_ADMIN.md](DESKTOP_ADMIN.md), [DEPLOYMENT_AND_UPDATES.md](DEPLOYMENT_AND_UPDATES.md), and [ARCHITECTURE.md](ARCHITECTURE.md). `.env.example` documents environment settings. Provision your own credentials for a live integration.

The root scripts `test_api.py`, `test_newparty.py`, and `test_people.py` exercise connected environments and can create or change records. Use an isolated test environment and set `TEST_OWNER_EMAIL` to its authorized owner account. The `tests/` directory contains separate regression checks.

The XML files are fictional reference examples. Their ledger names, products, prices and quantities require adaptation to a test Tally company before importing.

## Public copy

Production credentials, local databases, backups, private signing keys and the original repository history are excluded. The architecture review PDF is an earlier technical assessment with an anonymized recipient. The desktop update links in the deployment documentation point to the existing publisher feed.
