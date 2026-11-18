# Install Saarthi Sales on a phone

Deploy the updated `web/` folder with the existing cloud API, including
`manifest.webmanifest`, `pwa.js` and every file in `web/icons/`. No database
migration is needed for this change. The website must use HTTPS in production.
Do not publish a local Tally snapshot or business database as a static asset.

After deployment, reload the website and open the account menu (initials at the
top right), then **Install Saarthi app**. On supporting Android browsers, this
opens the browser's installation prompt when available. Otherwise, the app
shows instructions for the browser menu's **Install app / Add to Home screen**.
On iPhone, use Safari's **Share → Add to Home Screen**, with **Open as Web App**
enabled if offered. Installation is performed by the user on their phone.

The launcher opens the normal signed-in sales website in a standalone window.
The app keeps its existing login, permissions, draft saving, manual retry and
duplicate prevention. Sign in again if the installation uses a separate browser
session. Internet is needed to open current data and confirm orders. This change
does not add an offline database, background ordering, or a service worker.
Normal browser storage holds the existing per-account drafts. The server still
has to confirm an order before it is considered placed.

The install button never launches an installation prompt automatically. It
shows instructions when a native prompt is unavailable and recognises when
the website is already running in standalone mode. Browser eligibility and
installation UI vary; real Android/iPhone installation must be verified after
deployment. Local automated checks cover manifest/icon integrity and the
prompt/manual-instructions paths.

Reference: [MDN: Making PWAs installable](https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Guides/Making_PWAs_installable).

## Orders and review changes

Windows admin Orders now combines all local website requests with every current
voucher whose Tally type is `Sales`. Matching uses the Tally GUID so a linked
website order appears once. Older saved voucher versions remain in details;
current edits are shown in the list. Native invoices open the Tally voucher
detail screen. Cancelled, deleted and optional vouchers are labelled and have
no active sales value. Tally invoices with no linked salesman are Unattributed.
Search, status filtering and pagination cover the combined list. Salesmen's
website order permissions are unchanged.

The phone order review no longer repeats stock-exceeded messages above the
order. Per-product stock warnings remain visible and ordering is still allowed.
Missing-product, changed-price, storage and uncertain-submission messages remain.
