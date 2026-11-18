# Saarthi API request and response reference

The cloud backend exposes a **REST-style HTTP API built with FastAPI**. The caller chooses a method and path, such as `GET /api/orders` or `POST /api/orders`. Each route has a defined response. Some routes name an action, such as logout or acknowledge. The source uses explicit HTTP routes and JSON payloads, with binary image responses as an exception. Windows Admin has a separate local HTTP API. The agent uses SQL to reach PostgreSQL and XML over HTTP to reach Tally.

The project defines **76 operational HTTP endpoints** across its own servers. There are 27 cloud endpoints, 38 Windows Admin endpoints, and 11 older local Order Desk endpoints. This counts each method and path on each server separately, including the cloud health check and the two desktop connection routes. It excludes pages, static assets, generated framework routes, and external services. The main cloud and Windows Admin surfaces account for **65** of the 76. `POST /api/website/active` is a local Windows Admin endpoint that forwards the change to the cloud.

## How to read the contract

| Part | Example |
| --- | --- |
| Method and path | `POST /api/orders` chooses the create-order operation |
| Path parameter | In `/api/prospects/{pid}/decide`, `pid` identifies the proposal |
| Query parameter | In `/api/list?page=2`, `page` is a filter sent in the URL |
| Credentials | Cloud browser routes use a session cookie. Cloud desktop management routes use a scoped bearer token. Windows Admin uses its own local session |
| Request body | A JSON object for a submitted order, approval or other action |
| Response | HTTP status plus a body, usually JSON. Images and CSV exports use their own formats |

### Example: submit one order

Illustrative identifiers below. A real request uses customer and item IDs from the catalogue and the browser session cookie.

```json
{
  "customer_guid": "customer-guid",
  "lines": [{"item_guid": "item-guid", "qty": 10, "rate": 992}],
  "request_id": "7c49450c-1234-4af0-bc53-57a1cfd3ef89"
}
```

A successful response for an existing customer looks like this:

```json
{
  "order_no": "WEB-1112123456",
  "total": 9920,
  "lines": 1,
  "customer": "Example customer",
  "status": "placed",
  "note": null
}
```

The server gets salesperson identity from the session, looks up names and units, and calculates each line amount and the total. The browser uses the order number to track the result. The agent collects it separately. A later order read may show the linked invoice and its current observed lines.

## Cloud routes

The caller listed below receives the response. An em dash means no body or query input beyond the stated credentials or path.

| Method and path | Caller and input | Success response |
| --- | --- | --- |
| `GET /api/config` | Website or owner approval screen, public | `google_client_id`, `dev_login` |
| `POST /api/auth/google` | Sign-in screen, `credential` from Google | `email`, `name`, `role`, and a session cookie |
| `POST /api/auth/logout` | Website, current session if present | `ok` and session cleared |
| `GET /api/me` | Website or owner approval screen, session | `email`, `name`, `role`, `routes` |
| `POST /api/auth/dev` | Development sign-in, `email`, only when enabled | `email`, `name`, `role`, `dev`, and a session cookie |
| `GET /healthz` | Hosting monitor or HTTP client, public | `ok: true` when PostgreSQL is reachable. Failure returns HTTP 503 with `ok: false` and an error |
| `GET /api/data` | Sales or admin website, session | `me`, `status`, `products`, `customers`, `cats`, `payments`, `daily`, `lines` |
| `GET /api/product-image/{digest}` | Browser image request, session and hash; optional `If-None-Match` | Image bytes and MIME type, or HTTP 304 if cached |
| `GET /api/purchase-rates` | Admin session | Product IDs mapped to `rate`, `unit`, `date` |
| `GET /api/orders` | Website, session | Array of orders with `order_no`, `at`, `by`, `customer`, `route`, `status`, `total`, `vchno`, `with_laptop`, `invoice`, `invoice_restricted`, and `lines` |
| `POST /api/orders` | Order form, session, `customer_guid`, `lines` with `item_guid`, `qty`, `rate`; optional `request_id` and `note` | `order_no`, `total`, line count in `lines`, `customer`, `status`, `note`. Archived retries may also return `archived` |
| `POST /api/orders/ack` | Website after saving, session, `order_no` and optional `request_id` | `acknowledged` boolean for the matching account and submission |
| `POST /api/visits` | Sales visit tracker, session, `email`, `company`, `rows` with customer ID `cid` and timestamp `at` | `saved` |
| `GET /api/prospects` | Website, session | Array with `id`, `name`, `route`, `phone`, `address`, `note`, `by`, `at`, `status`, `decided_by`, `decide_note`, `in_tally`, `orders`, `order_value`. Admins can see all proposals |
| `POST /api/prospects` | Office admin, `name`, `route`, optional `phone`, `address`, `note`, `confirm_duplicate` | `id`, `name`, `route`, `status` |
| `POST /api/prospects/{pid}/decide` | Office admin, proposal ID, `approve`, optional `route`, `note` | `id`, `name`, `status` |
| `GET /api/routes` | Admin session | Array of `route` and `shops` |
| `GET /api/users` | Admin session | Array of `email`, `name`, `role`, `active`, `routes`, `shops`, `is_you` |
| `POST /api/users` | Admin session, `email`, optional `name`, `role`, `routes` | `email`, `role`, `routes` |
| `POST /api/users/{email}/active` | Admin session, account email, `active` boolean | `email`, `active` |
| `POST /api/users/{email}/remove` | Admin session and account email | `email`, `removed` |
| `POST /api/desktop/approve` | Owner approval screen, admin session, desktop `challenge` | `grant`, `expires_in` (300 seconds) to the browser for the callback |
| `POST /api/desktop/token` | Local desktop service, `grant` and `verifier` | `access_token`, `expires_in` (3,600 seconds), `email`, `name` |
| `GET /api/desktop/users` | Local desktop service, bearer token | Same user list as browser admin |
| `GET /api/desktop/routes` | Local desktop service, bearer token | Same route list as browser admin |
| `POST /api/desktop/users` | Local desktop service, bearer token and account fields | Same account save response as browser admin |
| `POST /api/desktop/users/{email}/active` | Local desktop service, bearer token, account email, `active` | `email`, `active` |

In `GET /api/orders`, the original `lines` include line number, item, unit, quantity, rate and amount. The separate `invoice` can contain its own `guid`, `customer`, `date`, `number`, `total`, `version`, `state`, `observed_at` and `lines`. The invoice can be null if the necessary source fact is unavailable. Important error responses include 401 for a missing session, 403 for permission failures, 404 for a missing record on relevant routes, 409 for a conflict and 422 for invalid request structure. See the response body for the specific reason.

Source: [Cloud API](../../api.py).

## Windows Admin routes

The local Admin screen is the caller and recipient unless otherwise stated. Local write requests also check the browser's local origin. Fields in GET requests are query parameters unless shown in the path.

| Method and path | Input | Response |
| --- | --- | --- |
| `GET /start/{token}` | Browser launch token | Local session cookie and redirect |
| `GET /desktop/callback` | Browser redirect with `state` and `grant` | Connection message after grant exchange |
| `GET /api/health` | — | App identity, archive path and version |
| `GET /api/agent` | — | Agent status |
| `POST /api/agent/start` | Start action | Agent startup status |
| `POST /api/agent/live` | `enabled` boolean | Setting saved and explanatory message |
| `GET /api/website/status` | — | Configured website and connection state |
| `POST /api/website/connect` | `url` or saved website | Pending status and browser opened for approval |
| `POST /api/website/disconnect` | Disconnect action | `disconnected` |
| `GET /api/website/users` | Connected owner session | Website account list |
| `GET /api/website/routes` | Connected owner session | Website route list |
| `POST /api/website/users` | Email, name, role and routes | Result of saving the website account |
| `POST /api/website/active` | `email` and `active` | Result of updating website access |
| `GET /api/work/context` | — | Company, eligible parties, products, units, groups and ledgers for forms |
| `GET /api/work/party-rates` | Customer `guid` | Customer rates |
| `GET /api/work/history` | `page`, `sort`, `direction` | Page of saved requests |
| `GET /api/work/detail` | Request `id` | Request state, details, result and error when available |
| `POST /api/work/submit` | Request `id`, operation `kind`, and `body` with form fields | Saved request with ID, kind, state, timestamps, summary, total, result and error when available |
| `POST /api/work/retry` | Request `id` | `queued` flag. The request can be queued for a retry or for verification depending on its previous state |
| `GET /api/work/image` | Product `guid` | Local image bytes |
| `POST /api/work/image` | Product `guid`, `image` data | Saved image result |
| `GET /api/inventory` | — | Product inventory and planning figures |
| `POST /api/inventory/policy` | Product `guid`, `minimum`, optional `target` | `saved` |
| `POST /api/inventory/rates/preview` | `csv` text with product and rate rows | Validated preview of rate update requests |
| `GET /api/overview` | `start`, `end` | Overview figures |
| `GET /api/analysis` | `start`, `end`, `route`, `rep`, `party`, `comparison`, `mode`, `year` | Sales analysis for the chosen period and scope |
| `GET /api/management` | `start`, `end`, `route`, `rep`, `party`, `mode`, `year` | Management figures for the chosen period and scope |
| `POST /api/analysis/target` | `start`, `end`, `route`, `rep`, `amount` | `saved`. Zero clears the matching target |
| `GET /api/list` | `kind`, `q`, `page`, `category`, `start`, `end`, `sort`, `direction` | Filtered archive records |
| `GET /api/detail` | `kind`, `id`, optional `version` | Archive record or requested version |
| `GET /api/explorer` | `table`, `page`, `q`, `rowid`, `sort`, `direction` | Selected table data or row |
| `GET /api/export` | `table` | CSV file |
| `POST /api/backup` | Backup action | Backup details and paths |
| `GET /api/updates` | — | Update status |
| `POST /api/updates/check` | Check action | Publisher-feed result |
| `POST /api/updates/open` | `online` and `recover` options | Updater launch result |
| `POST /api/settings` | Changed settings such as `update_feed`, `website_url`, `agent_script`, `agent_python` | `saved` |
| `POST /api/exit` | Close action | `closed` and local viewer shutdown |

Sources: [Local routes](../../desktop_app.py), [command validation and response](../../desktop_workflows.py).

## Tally interface and older Order Desk

| Caller and operation | Request | Response |
| --- | --- | --- |
| Agent, read from Tally | HTTP POST with an XML export request. The XML names the company query, collection or report and the fields and filters | XML records or a Tally error. The agent validates and stores relevant data locally |
| Agent, write to Tally | HTTP POST with an XML import request. The XML describes the voucher, ledger or product change | XML import counts and errors. The agent uses a separate read-back request to verify what changed |
| Agent, pull website orders and visits | Direct PostgreSQL SQL queries over its database connection | Database rows copied to SQLite. The agent records pickup in PostgreSQL |

The older local Order Desk has a separate set of routes. Its browser calls `GET /api/items` (search, group, stock-only and party → items), `GET /api/history` (party and limit → purchases), `GET /api/usual` (party and count → usual items), `GET /api/customers` (search and route → customers), `GET /api/groups` and `GET /api/routes` (no filters → group or route list), `GET /api/orders` (status → local orders), `GET /api/freshness` (no input → observations), and `GET /api/order/{number}` (order number → order). The write routes are `POST /api/orders` (party, lines, note and oversell option → saved local order) and `POST /api/order/{number}/status` (number and status → updated result).

Sources: [Tally XML transport](../../tally_connector.py), [order pickup](../../tally_cloud.py#L353), [older local Order Desk](../../tally_desk.py#L224).
