"""The cloud database: its schema, and how to reach it.

Separated from tally_cloud so the website's API can import it without
pulling in the local SQLite archive, Tally, or anything else that only
exists on the shop laptop. The schema therefore has exactly one home --
which matters, because a column added in two places is a column that
eventually disagrees with itself.
"""
import os
import pathlib

import psycopg

from saarthi_runtime import data_directory
HERE = data_directory()


SCHEMA = """
-- ---------- pushed up by the laptop; read-only to the website ----------
-- Columns marked (blank) have NO source in Tally today -- every one is 0
-- of 625 or 0 of 734. They exist so the UI feature ships, renders an empty
-- state, and lights up unchanged the day the data is entered. Quantities
-- are in Tally's BASE UNIT (PCS); there is no case/pack conversion, because
-- AdditionalUnits is "Not Applicable" and Conversion is 0 on every item.
CREATE TABLE IF NOT EXISTS item (
    guid        TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    grp         TEXT,
    unit        TEXT,
    rate        NUMERIC(12,2),      -- StandardPrice: the FALLBACK only
    mrp         NUMERIC(12,2),      -- parsed out of the item NAME
    on_hand     NUMERIC(14,3),      -- may be NEGATIVE: 54 items are oversold
    saleable    NUMERIC(14,3),
    stock_asof  TIMESTAMP,
    daily       NUMERIC(14,3),      -- derived: units/day over 90 days
    cover_days  NUMERIC(10,1),      -- derived: floors at 0 when oversold
    brand       TEXT,               -- (blank) no Tally field
    category    TEXT,               -- (blank) Category is "Not Applicable"
    pack_size   INTEGER,            -- (blank) no pack/case structure exists
    scheme      TEXT                -- (blank) no schemes in this business
);

CREATE TABLE IF NOT EXISTS customer (
    guid         TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    route        TEXT,              -- the 18 route groups: the geography we DO have
    outstanding  NUMERIC(14,2),
    state        TEXT,
    balance_asof TIMESTAMP,
    gstin        TEXT,              -- (blank) 0 of 734 registered
    aov          NUMERIC(14,2),     -- derived
    last_days    INTEGER,           -- derived: days since their last invoice
    churn        INTEGER,           -- derived: % below their own baseline
    growth       INTEGER,           -- derived
    trend        TEXT,              -- derived: JSON [{month,value}] x6
    -- Derived by applying receipts FIFO: bill-wise accounting is OFF, so
    -- Tally records no bill references. 82% of balances reconstruct within
    -- 2%; ageing_gap carries the error for the rest rather than hiding it.
    overdue      NUMERIC(14,2),
    overdue_days INTEGER,
    d0_30        NUMERIC(14,2),
    d31_60       NUMERIC(14,2),
    d61_90       NUMERIC(14,2),
    d90_plus     NUMERIC(14,2),
    ageing_gap   NUMERIC(14,2),
    last_receipt TEXT,
    phone        TEXT,              -- (blank) 0 of 734
    contact      TEXT,              -- (blank) 0 of 734
    email        TEXT,              -- (blank) 0 of 734
    credit_limit NUMERIC(14,2),     -- (blank) 0 of 734 -- credit flags stay dark
    credit_days  INTEGER,           -- (blank) 0 of 734
    address      TEXT,              -- (blank) 0 of 734
    lat          NUMERIC(10,6),     -- (blank) no geo -- route planner stays dark
    lng          NUMERIC(10,6)
);
CREATE INDEX IF NOT EXISTS customer_route ON customer(route);

-- What each shop was LAST actually charged. StandardPrice is wrong for 76%
-- of these pairs, so this is the quote and item.rate is only the fallback.
-- Keyed on NAMES because that is what the vouchers carry; a master rename
-- in Tally orphans a row until the next rebuild.
CREATE TABLE IF NOT EXISTS party_rate (
    party       TEXT NOT NULL,
    item        TEXT NOT NULL,
    last_rate   NUMERIC(12,2),
    last_date   TEXT,
    times       INTEGER,
    item_median NUMERIC(12,2),
    PRIMARY KEY (party, item)
);
CREATE INDEX IF NOT EXISTS party_rate_party ON party_rate(party);

-- The reorder engine: what each shop takes, how often, and whether it is
-- due. freq_n/freq_m is the honest raw count -- UI copy must read from it
-- ("taken in 3 of the last 10 bills") and must NOT say "usually in every
-- order": only 6 of 3,763 lines appear in >=80% of a party's invoices.
CREATE TABLE IF NOT EXISTS party_buy (
    party       TEXT NOT NULL,
    item        TEXT NOT NULL,
    cyc_lo      INTEGER,
    cyc_hi      INTEGER,
    last_days   INTEGER,
    typical_qty NUMERIC(14,3),
    freq_n      INTEGER,
    freq_m      INTEGER,
    is_core     BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (party, item)
);
CREATE INDEX IF NOT EXISTS party_buy_party ON party_buy(party);

CREATE TABLE IF NOT EXISTS history_voucher (
    guid    TEXT PRIMARY KEY,
    party   TEXT NOT NULL,
    vdate   TEXT NOT NULL,
    vchtype TEXT,
    number  TEXT,
    amount  NUMERIC(14,2),
    source_alterid BIGINT,
    source_state TEXT NOT NULL DEFAULT 'active',
    observed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS history_voucher_party ON history_voucher(party, vdate DESC);

CREATE TABLE IF NOT EXISTS history_line (
    voucher_guid TEXT NOT NULL REFERENCES history_voucher(guid) ON DELETE CASCADE,
    line_no      INTEGER NOT NULL,
    item         TEXT,
    qty          NUMERIC(14,3),
    unit         TEXT,
    rate         NUMERIC(12,2),
    amount       NUMERIC(14,2),
    PRIMARY KEY (voucher_guid, line_no)
);

-- One row. Drives the staleness banner: a phone must be able to say how old
-- its stock figure is rather than presenting it as current.
CREATE TABLE IF NOT EXISTS sync_status (
    id                SMALLINT PRIMARY KEY DEFAULT 1,
    company           TEXT,
    stock_asof        TIMESTAMP,
    voucher_watermark BIGINT,
    pushed_at         TIMESTAMP,
    CONSTRAINT sync_status_single CHECK (id = 1)
);

-- ---------- auth: cloud-only, never pushed ----------
CREATE TABLE IF NOT EXISTS app_user (
    email  TEXT PRIMARY KEY,         -- exact Google address, allowlisted
    name   TEXT,
    role   TEXT NOT NULL DEFAULT 'salesperson',   -- admin | salesperson
    active BOOLEAN NOT NULL DEFAULT TRUE
);

-- Which routes a salesperson covers. Scoping by route means a rep cannot
-- see another route's customers or their outstanding balances.
CREATE TABLE IF NOT EXISTS user_route (
    email TEXT NOT NULL REFERENCES app_user(email) ON DELETE CASCADE,
    route TEXT NOT NULL,
    PRIMARY KEY (email, route)
);

-- A shop a salesperson found that has no ledger in Tally yet.
--
-- The rep is standing in the shop, so they cannot wait for head office to
-- approve anything -- they must be able to take the order now. So a
-- prospect is a real thing orders can be placed against, and it carries
-- its own id (prefixed NEW-) which can never collide with a Tally guid.
--
-- Only the LAPTOP ever sets tally_guid, and only after Tally has confirmed
-- the ledger exists. Until then every order against this prospect sits at
-- status 'pending_party' and the invoice batch will not touch it.
CREATE TABLE IF NOT EXISTS prospect (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    -- Routes ARE Tally ledger groups, so this doubles as the parent group
    -- the ledger gets created under. Get it wrong and the shop is invisible
    -- to the rep who found it, because scoping is by route.
    route       TEXT,
    phone       TEXT,
    address     TEXT,
    note        TEXT,
    proposed_by TEXT NOT NULL REFERENCES app_user(email),
    created_at  TIMESTAMP NOT NULL DEFAULT now(),
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending|approved|rejected
    decided_by  TEXT,
    decided_at  TIMESTAMP,
    decide_note TEXT,
    tally_guid  TEXT,            -- laptop only, once the ledger is real
    linked_at   TIMESTAMP
);
CREATE INDEX IF NOT EXISTS prospect_open ON prospect(status)
    WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS prospect_unlinked ON prospect(status)
    WHERE status = 'approved' AND tally_guid IS NULL;

-- ---------- written by the website, pulled down by the laptop ----------
-- The cloud allocates order numbers. With 3-4 phones ordering while the
-- laptop may be off, two allocators would collide; a UNIQUE primary key on
-- one side is also what makes concurrent salespeople safe.
CREATE TABLE IF NOT EXISTS cloud_order (
    order_no      TEXT PRIMARY KEY,
    created_at    TIMESTAMP NOT NULL DEFAULT now(),
    salesperson   TEXT,              -- email: the audit trail
    customer_guid TEXT NOT NULL,
    customer_name TEXT NOT NULL,
    route         TEXT,
    status        TEXT NOT NULL DEFAULT 'placed',
    subtotal      NUMERIC(14,2) NOT NULL,
    gst           NUMERIC(14,2) NOT NULL DEFAULT 0,
    total         NUMERIC(14,2) NOT NULL,
    stock_asof    TIMESTAMP,
    note          TEXT,
    -- set by the LAPTOP only, once Tally confirms. One writer per field.
    pulled_at     TIMESTAMP,
    tally_guid    TEXT,
    tally_vchno   TEXT
);
CREATE INDEX IF NOT EXISTS cloud_order_unpulled ON cloud_order(pulled_at)
    WHERE pulled_at IS NULL;

CREATE TABLE IF NOT EXISTS cloud_order_line (
    order_no  TEXT NOT NULL REFERENCES cloud_order(order_no) ON DELETE CASCADE,
    line_no   INTEGER NOT NULL,
    item_guid TEXT NOT NULL,
    item_name TEXT NOT NULL,
    unit      TEXT,
    qty       NUMERIC(14,3) NOT NULL,
    rate      NUMERIC(12,2) NOT NULL,
    amount    NUMERIC(14,2) NOT NULL,
    PRIMARY KEY (order_no, line_no)
);
"""

# Idempotent column adds. CREATE TABLE IF NOT EXISTS silently does nothing to
# a table that already exists, so a column added later never appears on a
# database created by an earlier version -- and CREATE TEMP TABLE (LIKE item)
# then copies the OLD shape and the insert fails with "column does not
# exist". Every column added after the first deploy belongs here as well as
# in the CREATE above.
ORDER_RETRY_MIGRATION = """
ALTER TABLE cloud_order ADD COLUMN IF NOT EXISTS client_request_id TEXT;
ALTER TABLE cloud_order ADD COLUMN IF NOT EXISTS request_fingerprint TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS cloud_order_submission
    ON cloud_order(salesperson, client_request_id)
    WHERE client_request_id IS NOT NULL;
"""

TALLY_UPDATES_MIGRATION = """
ALTER TABLE history_voucher ADD COLUMN IF NOT EXISTS source_alterid BIGINT;
ALTER TABLE history_voucher ADD COLUMN IF NOT EXISTS source_state TEXT NOT NULL DEFAULT 'active';
ALTER TABLE history_voucher ADD COLUMN IF NOT EXISTS observed_at TIMESTAMPTZ;
"""

FACT_RETENTION_MIGRATION = """
ALTER TABLE cloud_order ADD COLUMN IF NOT EXISTS retry_protected BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE cloud_order ADD COLUMN IF NOT EXISTS retention_hold BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE cloud_order ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ;
ALTER TABLE history_voucher ADD COLUMN IF NOT EXISTS retention_hold BOOLEAN NOT NULL DEFAULT FALSE;
CREATE TABLE IF NOT EXISTS order_receipt (
    salesperson TEXT NOT NULL,
    client_request_id TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    order_no TEXT NOT NULL UNIQUE,
    tally_guid TEXT,
    response JSONB NOT NULL,
    archived_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (salesperson,client_request_id)
);
ALTER TABLE order_receipt ADD COLUMN IF NOT EXISTS tally_guid TEXT;
CREATE INDEX IF NOT EXISTS order_receipt_voucher ON order_receipt(tally_guid)
    WHERE tally_guid IS NOT NULL;
CREATE TABLE IF NOT EXISTS daily_sales (
    vdate TEXT NOT NULL,
    party TEXT NOT NULL,
    salesperson TEXT NOT NULL DEFAULT '',
    invoices INTEGER NOT NULL,
    amount NUMERIC(14,2) NOT NULL,
    PRIMARY KEY(vdate,party,salesperson)
);
CREATE INDEX IF NOT EXISTS cloud_order_cleanup ON cloud_order(completed_at)
    WHERE status='invoiced' AND retry_protected=FALSE AND retention_hold=FALSE;
"""

DESKTOP_ACCESS_MIGRATION = """
CREATE TABLE IF NOT EXISTS desktop_grant (
    code_hash TEXT PRIMARY KEY,
    email TEXT NOT NULL REFERENCES app_user(email),
    challenge TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    used_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS desktop_grant_expiry ON desktop_grant(expires_at);
"""

MIGRATE = ORDER_RETRY_MIGRATION + TALLY_UPDATES_MIGRATION + FACT_RETENTION_MIGRATION + DESKTOP_ACCESS_MIGRATION + """
ALTER TABLE item     ADD COLUMN IF NOT EXISTS daily        NUMERIC(14,3);
ALTER TABLE item     ADD COLUMN IF NOT EXISTS cover_days   NUMERIC(10,1);
ALTER TABLE item     ADD COLUMN IF NOT EXISTS brand        TEXT;
ALTER TABLE item     ADD COLUMN IF NOT EXISTS category     TEXT;
ALTER TABLE item     ADD COLUMN IF NOT EXISTS pack_size    INTEGER;
ALTER TABLE item     ADD COLUMN IF NOT EXISTS scheme       TEXT;
ALTER TABLE customer ADD COLUMN IF NOT EXISTS gstin        TEXT;
ALTER TABLE customer ADD COLUMN IF NOT EXISTS aov          NUMERIC(14,2);
ALTER TABLE customer ADD COLUMN IF NOT EXISTS last_days    INTEGER;
ALTER TABLE customer ADD COLUMN IF NOT EXISTS churn        INTEGER;
ALTER TABLE customer ADD COLUMN IF NOT EXISTS growth       INTEGER;
ALTER TABLE customer ADD COLUMN IF NOT EXISTS trend        TEXT;
ALTER TABLE customer ADD COLUMN IF NOT EXISTS overdue      NUMERIC(14,2);
ALTER TABLE customer ADD COLUMN IF NOT EXISTS overdue_days INTEGER;
ALTER TABLE customer ADD COLUMN IF NOT EXISTS d0_30        NUMERIC(14,2);
ALTER TABLE customer ADD COLUMN IF NOT EXISTS d31_60       NUMERIC(14,2);
ALTER TABLE customer ADD COLUMN IF NOT EXISTS d61_90       NUMERIC(14,2);
ALTER TABLE customer ADD COLUMN IF NOT EXISTS d90_plus     NUMERIC(14,2);
ALTER TABLE customer ADD COLUMN IF NOT EXISTS ageing_gap   NUMERIC(14,2);
ALTER TABLE customer ADD COLUMN IF NOT EXISTS last_receipt TEXT;
ALTER TABLE customer ADD COLUMN IF NOT EXISTS phone        TEXT;
ALTER TABLE customer ADD COLUMN IF NOT EXISTS contact      TEXT;
ALTER TABLE customer ADD COLUMN IF NOT EXISTS email        TEXT;
ALTER TABLE customer ADD COLUMN IF NOT EXISTS credit_limit NUMERIC(14,2);
ALTER TABLE customer ADD COLUMN IF NOT EXISTS credit_days  INTEGER;
ALTER TABLE customer ADD COLUMN IF NOT EXISTS address      TEXT;
ALTER TABLE customer ADD COLUMN IF NOT EXISTS lat          NUMERIC(10,6);
ALTER TABLE customer ADD COLUMN IF NOT EXISTS lng          NUMERIC(10,6);
-- Set when the order was placed against a prospect rather than a real
-- ledger. customer_guid holds the prospect's id until the laptop links it,
-- then becomes the Tally guid; this column stays as the audit trail of how
-- the order came in.
ALTER TABLE cloud_order ADD COLUMN IF NOT EXISTS prospect_id TEXT;
-- How billing is actually going, so a stall is visible instead of silent.
-- An invoice run that stops has no symptom of its own: orders simply stay
-- "at the office" and look normal. These are what let the owner's screen
-- say "14 orders waiting, oldest 4 hours, Tally needs restarting".
ALTER TABLE sync_status ADD COLUMN IF NOT EXISTS orders_waiting INTEGER;
ALTER TABLE sync_status ADD COLUMN IF NOT EXISTS oldest_wait_mins INTEGER;
ALTER TABLE sync_status ADD COLUMN IF NOT EXISTS orders_set_aside INTEGER;
ALTER TABLE sync_status ADD COLUMN IF NOT EXISTS invoice_blocked_since TIMESTAMP;
ALTER TABLE sync_status ADD COLUMN IF NOT EXISTS invoice_error TEXT;

-- Times the DATABASE generates, now with their offset kept.
--
-- These were `timestamp without time zone` filled by now(), and Railway
-- runs in UTC: an order placed at 5:20pm in Indore was stored as 11:50
-- and shown as 11:50, because a naive timestamp carries nothing to
-- convert from. With timestamptz the instant survives and every client
-- renders it in its own local time.
--
-- Safe to re-run, and safe on the existing rows: every one of these was
-- written by now() on the server, so they are all UTC already -- even the
-- ones from when the API ran on the laptop, because it was still this
-- database's clock that filled them.
ALTER TABLE cloud_order ALTER COLUMN created_at TYPE TIMESTAMPTZ
    USING created_at AT TIME ZONE 'UTC';
ALTER TABLE cloud_order ALTER COLUMN pulled_at  TYPE TIMESTAMPTZ
    USING pulled_at AT TIME ZONE 'UTC';
ALTER TABLE prospect ALTER COLUMN created_at TYPE TIMESTAMPTZ
    USING created_at AT TIME ZONE 'UTC';
ALTER TABLE prospect ALTER COLUMN decided_at TYPE TIMESTAMPTZ
    USING decided_at AT TIME ZONE 'UTC';
ALTER TABLE prospect ALTER COLUMN linked_at  TYPE TIMESTAMPTZ
    USING linked_at AT TIME ZONE 'UTC';
"""


def env(key, default=None, path=None):
    """Read one key from .env without a dotenv dependency."""
    if os.environ.get(key):
        return os.environ[key]
    f = pathlib.Path(path or (HERE / ".env"))
    if f.exists():
        for ln in f.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#") and "=" in ln:
                k, _, v = ln.partition("=")
                if k.strip() == key:
                    return v.strip().strip('"').strip("'")
    return default


def connect(url=None, *, connect_timeout=30):
    url = url or env("CLOUD_DB_URL")
    if not url:
        raise RuntimeError(
            "CLOUD_DB_URL is not set. Copy .env.example to .env and put the "
            "Railway PUBLIC connection string in it.")
    if "sslmode=" not in url:
        url += ("&" if "?" in url else "?") + "sslmode=require"
    return psycopg.connect(url, connect_timeout=connect_timeout)


INVOICE_DATE_MIGRATION = 'ALTER TABLE cloud_order ADD COLUMN IF NOT EXISTS invoice_date DATE'


OUR_RATE_MIGRATION = """
CREATE TABLE IF NOT EXISTS pending_our_rate (
    guid TEXT PRIMARY KEY,
    price NUMERIC(12,2) NOT NULL,
    revision UUID NOT NULL
);
"""


def pending_our_rates_available(cur):
    cur.execute("SELECT to_regclass('pending_our_rate')")
    return bool(cur.fetchone()[0])


def create_schema(cur, log=print):
    cur.execute(SCHEMA)
    cur.execute(MIGRATE)
    cur.execute(INVOICE_DATE_MIGRATION)
    from sales_visits import CLOUD_SCHEMA
    cur.execute(CLOUD_SCHEMA)
    from product_media import CLOUD_SCHEMA as MEDIA_SCHEMA
    cur.execute(MEDIA_SCHEMA)
    from purchase_costs import CLOUD_SCHEMA as PURCHASE_SCHEMA
    cur.execute(PURCHASE_SCHEMA)
    cur.execute(OUR_RATE_MIGRATION)
    log("schema ok")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Apply an isolated additive schema upgrade.")
    upgrade = parser.add_mutually_exclusive_group(required=True)
    upgrade.add_argument("--order-retry-schema", action="store_true")
    upgrade.add_argument("--tally-updates-schema", action="store_true")
    upgrade.add_argument("--fact-retention-schema", action="store_true")
    upgrade.add_argument("--desktop-access-schema", action="store_true")
    upgrade.add_argument("--visit-schema", action="store_true")
    upgrade.add_argument("--media-schema", action="store_true")
    upgrade.add_argument("--purchase-cost-schema", action="store_true")
    upgrade.add_argument("--invoice-date-schema", action="store_true")
    upgrade.add_argument("--our-rate-schema", action="store_true")
    args = parser.parse_args()
    with connect() as cx, cx.cursor() as cur:
        from sales_visits import CLOUD_SCHEMA
        from product_media import CLOUD_SCHEMA as MEDIA_SCHEMA
        from purchase_costs import CLOUD_SCHEMA as PURCHASE_SCHEMA
        cur.execute(OUR_RATE_MIGRATION if args.our_rate_schema else INVOICE_DATE_MIGRATION if args.invoice_date_schema else PURCHASE_SCHEMA if args.purchase_cost_schema else MEDIA_SCHEMA if args.media_schema else CLOUD_SCHEMA if args.visit_schema else ORDER_RETRY_MIGRATION if args.order_retry_schema else
                    FACT_RETENTION_MIGRATION if args.fact_retention_schema else
                    DESKTOP_ACCESS_MIGRATION if args.desktop_access_schema else TALLY_UPDATES_MIGRATION)
    print("schema upgrade ready")
