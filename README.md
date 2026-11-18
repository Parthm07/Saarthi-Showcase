# Saarthi

Saarthi connects a distribution business's field sales team to Tally. Salespeople use a mobile website to browse products, check stock and customer pricing, record visits, and place orders. A Windows agent exchanges data between the local Tally installation and the cloud application.

The project is used by a real business. This showcase contains anonymized examples and a fresh Git history prepared for public review.

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
