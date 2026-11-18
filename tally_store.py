"""Local raw archive of Tally vouchers: SQLite, append-only, git-like.

Every version of every voucher is kept, keyed (guid, alterid), with the full
raw record gzipped alongside. Nothing is ever overwritten, so "what did this
invoice look like before it was edited?" stays answerable -- which Tally
itself does not make easy.

Three operations:
    backfill(conn)     day 1: pull history back to a date cutoff
    sync(conn)         thereafter: pull only what changed, via AlterID
    reconcile(conn)    catch deletions, which AlterID cannot see

Stdlib only. Run `python tally_store.py` for a self-check.
"""
import datetime as dt
import gzip
import json
import os
import sqlite3
import time

import tally_connector as t

from saarthi_runtime import data_directory
DB = str(data_directory() / "tally_raw.db")

# 4000-wide windows measured ~5 ms/voucher against ~32 ms at 500: per-request
# overhead dominates, so bigger windows are cheaper per row. There is no
# company-size threshold to branch on -- one window size serves a kirana shop
# and a large distributor alike. Detail comes from WALK (~1.2 KB/voucher),
# never the Day Book report, which wedges Tally and silently ignores its own
# date parameters.
WINDOW = 5000

SCHEMA = """
CREATE TABLE IF NOT EXISTS voucher (
    guid       TEXT    NOT NULL,
    alterid    INTEGER NOT NULL,
    vchtype    TEXT,
    vdate      TEXT,              -- YYYYMMDD, as Tally sends it
    party      TEXT,
    amount     REAL,
    blob       BLOB    NOT NULL,  -- gzipped JSON of the full raw record
    fetched_at TEXT    NOT NULL,
    PRIMARY KEY (guid, alterid)
);
CREATE INDEX IF NOT EXISTS voucher_vdate   ON voucher(vdate);
CREATE INDEX IF NOT EXISTS voucher_alterid ON voucher(alterid);

-- AlterID cannot see deletions: a deleted voucher stops existing and no
-- counter moves. reconcile() finds them and records them here. Rows in
-- voucher are never removed, so a cancelled invoice keeps its history.
CREATE TABLE IF NOT EXISTS deleted_voucher (
    guid        TEXT PRIMARY KEY,
    last_seen   INTEGER,
    detected_at TEXT NOT NULL
);

-- One table for every master type rather than six near-identical ones.
-- Same (guid, alterid) history as vouchers, same AlterID watermark: one
-- mechanism, so a 50,000-SKU trader needs no second code path.
CREATE TABLE IF NOT EXISTS master (
    objtype    TEXT    NOT NULL,   -- StockItem, Ledger, Group, ...
    guid       TEXT    NOT NULL,
    alterid    INTEGER NOT NULL,
    name       TEXT,
    parent     TEXT,
    blob       BLOB    NOT NULL,
    fetched_at TEXT    NOT NULL,
    PRIMARY KEY (objtype, guid, alterid)
);
CREATE INDEX IF NOT EXISTS master_name ON master(objtype, name);

CREATE TABLE IF NOT EXISTS deleted_master (
    objtype     TEXT NOT NULL,
    guid        TEXT NOT NULL,
    name        TEXT,
    last_seen   INTEGER,
    detected_at TEXT NOT NULL,
    PRIMARY KEY (objtype, guid)
);

-- Full-fidelity archive: all 267 populated fields per voucher, from the
-- day_book() report. Separate from `voucher` (42 fields, the every-minute
-- sync path) because the two have different cost and different coverage --
-- three fields exist only in the Collection path. Keeping both lets a
-- missing field be a query instead of a rediscovery.
CREATE TABLE IF NOT EXISTS voucher_raw (
    guid       TEXT    NOT NULL,
    alterid    INTEGER NOT NULL,
    vdate      TEXT,
    vchtype    TEXT,
    blob       BLOB    NOT NULL,
    fetched_at TEXT    NOT NULL,
    PRIMARY KEY (guid, alterid)
);
CREATE INDEX IF NOT EXISTS voucher_raw_vdate ON voucher_raw(vdate);

-- A day is recorded here only after its voucher count has been verified
-- against an independent source. An unrecorded day is simply re-fetched, so
-- a crash, a freeze or a half-written day costs one repeat and never a gap.
CREATE TABLE IF NOT EXISTS archived_day (
    vdate      TEXT PRIMARY KEY,
    vouchers   INTEGER NOT NULL,
    expected   INTEGER NOT NULL,
    bytes      INTEGER NOT NULL,
    fetched_at TEXT    NOT NULL
);

-- Stock quantity is polled, NOT synced incrementally. Measured: of 210
-- items sold in the last 7 days only 21% sit in the top decile of master
-- AlterID (against ~100% if a sale touched the master), so a SALE DOES NOT
-- BUMP THE STOCKITEM AlterID. An AlterID-driven masters sync therefore
-- serves a stale ClosingBalance indefinitely, and a website trusting it
-- oversells. observed_at is kept so staleness is visible rather than
-- assumed.
CREATE TABLE IF NOT EXISTS stock_snapshot (
    observed_at TEXT    NOT NULL,
    guid        TEXT    NOT NULL,
    name        TEXT,
    qty         REAL,
    unit        TEXT,
    rate        REAL,
    PRIMARY KEY (observed_at, guid)
);
CREATE INDEX IF NOT EXISTS stock_snapshot_guid ON stock_snapshot(guid);

-- Outstanding balance is polled for the same reason stock is. It is a
-- DERIVED value, not a master attribute: it does not even appear in the
-- 520-field 'List of Accounts' export, and a receipt against a customer no
-- more bumps their Ledger AlterID than a sale bumps a StockItem's. Serving
-- a stale balance would mean quoting the wrong credit position.
CREATE TABLE IF NOT EXISTS party_balance (
    observed_at TEXT NOT NULL,
    guid        TEXT NOT NULL,
    name        TEXT,
    closing     REAL,
    PRIMARY KEY (observed_at, guid)
);
CREATE INDEX IF NOT EXISTS party_balance_guid ON party_balance(guid);

CREATE TABLE IF NOT EXISTS sync_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# reconcile() refuses to tombstone when the live inventory is implausibly
# small against the local copy -- a degraded response would otherwise delete
# the whole archive.
MIN_LIVE_RATIO = 0.5


class CompanyMismatch(Exception):
    """Tally has a different company loaded than this database was built from.

    Syncing company B's vouchers into company A's archive is unrecoverable
    without a rebuild, and `company()` takes the first row of whatever is
    loaded -- so this is checked before every write.
    """

# Field names verified present on this Tally. Unknown names are dropped
# silently, so these were probed rather than guessed -- and some obvious
# guesses are wrong: it is GSTHSNCode not HSNCode, and PartyGSTIN is a tag on
# the Object export but NOT a valid Collection field name.
#
# ponytail: no batch / MFG-date / godown-wise stock. This company has
# IsBatchWiseOn='No' on all 625 items and a single godown, so there is
# nothing to fetch and no way to verify a walk name against it. A
# batch-tracking client needs this re-probed before Phase 3 promises expiry
# or godown-wise availability -- do not assume BatchAllocations is correct.
MASTER_SPECS = {
    "StockItem": (
        ("Name", "Parent", "Category", "BaseUnits", "AdditionalUnits",
         "Denominator", "Conversion", "GSTApplicable", "GSTHSNCode",
         "GSTTypeOfSupply", "ClosingBalance", "ClosingRate", "ClosingValue",
         "OpeningBalance", "OpeningRate", "ReorderLevel", "StandardPrice",
         "CostingMethod", "ValuationMethod", "IsBatchWiseOn", "HasMFGDate",
         "IsPerishableOn", "AlterID", "MasterID", "GUID"),
        ()),
    # Address is a FIELD that yields ADDRESS.LIST, not a WALK -- as a WALK it
    # silently returns nothing, same as any name Tally does not recognise.
    "Ledger": (
        ("Name", "Parent", "Address", "GSTRegistrationType", "LedStateName",
         "StateName", "CountryName", "Pincode", "ClosingBalance",
         "OpeningBalance", "IsBillWiseOn", "AlterID", "MasterID", "GUID"),
        ()),
    "Group": (("Name", "Parent", "IsRevenue", "IsDeemedPositive",
               "AlterID", "MasterID", "GUID"), ()),
    "StockGroup": (("Name", "Parent", "AlterID", "MasterID", "GUID"), ()),
    "Godown": (("Name", "Parent", "AlterID", "MasterID", "GUID"), ()),
    "Unit": (("Name", "OriginalName", "IsSimpleUnit", "AlterID", "GUID"), ()),
}


def open_db(path=DB):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _get(conn, key, default=None):
    r = conn.execute("SELECT value FROM sync_state WHERE key=?",
                     (key,)).fetchone()
    return r["value"] if r else default


def _set(conn, key, value):
    conn.execute("INSERT INTO sync_state(key,value) VALUES(?,?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, str(value)))


def verify_company(conn, log=print):
    """Bind this database to one company, and refuse any other.

    On first use the loaded company name is recorded. Every later call
    compares against it. Without this, opening a different company in Tally
    silently merges two sets of books.
    """
    name = t.company()
    if not name:
        raise t.TallyOffline("Tally has no company loaded")
    expected = _get(conn, "company")
    if expected is None:
        _set(conn, "company", name)
        conn.commit()
        log(f"company bound to {name!r}")
        return name
    if name != expected:
        raise CompanyMismatch(
            f"Tally has {name!r} loaded but this database was built from "
            f"{expected!r}. Refusing to mix two companies.")
    return name


def snapshot_stock(conn, log=print):
    """Poll every stock item's quantity. Cheap, unconditional, timestamped.

    Does not use AlterID: sales do not bump it (see stock_snapshot above), so
    an incremental approach would never see a quantity change. 625 items
    cost ~0.5 s, which is affordable at a 5-minute cadence even during
    business hours.
    """
    verify_company(conn, log=lambda *a: None)
    rows = t.collection("StockItem", "Name", "ClosingBalance", "ClosingRate",
                        "GUID", name="StkSnap", timeout=300)
    seen = dt.datetime.now().isoformat(timespec="seconds")
    payload = []
    for r in rows:
        if not r.get("GUID"):
            continue
        q, unit = t.qty(r.get("CLOSINGBALANCE"))
        rt, _ = t.rate(r.get("CLOSINGRATE"))
        payload.append((seen, r["GUID"], r.get("NAME"), q, unit, rt))
    conn.executemany(
        "INSERT OR IGNORE INTO stock_snapshot"
        "(observed_at,guid,name,qty,unit,rate) VALUES(?,?,?,?,?,?)", payload)
    conn.commit()
    log(f"stock: {len(payload)} items snapshotted at {seen}")
    return len(payload)


def snapshot_balances(conn, log=print):
    """Poll every party's closing balance. Same rationale as stock."""
    verify_company(conn, log=lambda *a: None)
    rows = t.collection("Ledger", "Name", "ClosingBalance", "GUID",
                        name="BalSnap", timeout=300)
    seen = dt.datetime.now().isoformat(timespec="seconds")
    payload = [(seen, r["GUID"], r.get("NAME"), t.amount(r.get("CLOSINGBALANCE")))
               for r in rows if r.get("GUID")]
    conn.executemany(
        "INSERT OR IGNORE INTO party_balance(observed_at,guid,name,closing)"
        " VALUES(?,?,?,?)", payload)
    conn.commit()
    nz = sum(1 for p in payload if p[3])
    log(f"balances: {len(payload)} parties ({nz} non-zero) at {seen}")
    return len(payload)


def balances_now(conn):
    """Latest balance per party, with its observation time."""
    return {r["guid"]: r for r in conn.execute(
        "SELECT b.* FROM party_balance b JOIN ("
        "  SELECT guid, MAX(observed_at) m FROM party_balance GROUP BY guid"
        ") x ON b.guid = x.guid AND b.observed_at = x.m")}


def prune_balances(conn, keep=288):
    conn.execute(
        "DELETE FROM party_balance WHERE observed_at NOT IN ("
        " SELECT DISTINCT observed_at FROM party_balance"
        " ORDER BY observed_at DESC LIMIT ?)", (keep,))
    conn.commit()


def stock_now(conn):
    """Latest snapshot per item, with its observation time.

    The observation time is part of the answer: the website must be able to
    say how stale its stock figure is rather than presenting it as current.
    """
    return [dict(r) for r in conn.execute(
        "SELECT s.* FROM stock_snapshot s JOIN ("
        "  SELECT guid, MAX(observed_at) m FROM stock_snapshot GROUP BY guid"
        ") x ON s.guid = x.guid AND s.observed_at = x.m")]


def prune_snapshots(conn, keep=288):
    """Keep the newest `keep` snapshot rounds; drop older ones.

    At a 5-minute cadence 288 rounds is one day. Unbounded growth would be
    625 rows x 288/day = 180k rows/day.
    """
    conn.execute(
        "DELETE FROM stock_snapshot WHERE observed_at NOT IN ("
        " SELECT DISTINCT observed_at FROM stock_snapshot"
        " ORDER BY observed_at DESC LIMIT ?)", (keep,))
    conn.commit()


def live_vouchers(conn, cutoff=None):
    """Vouchers that represent real sales: not tombstoned, not cancelled.

    `current()` returns whatever is stored, so anything doing stock or
    revenue logic must exclude deleted and cancelled vouchers explicitly.
    IsCancelled matters because a cancelled voucher still EXISTS in Tally --
    reconcile() will never flag it.
    """
    q = ("SELECT v.guid, v.alterid, v.vchtype, v.vdate, v.party, v.amount,"
         " v.blob FROM voucher v JOIN ("
         "  SELECT guid, MAX(alterid) m FROM voucher GROUP BY guid"
         ") x ON v.guid = x.guid AND v.alterid = x.m"
         " LEFT JOIN deleted_voucher d ON d.guid = v.guid"
         " WHERE d.guid IS NULL")
    args = ()
    if cutoff:
        q += " AND v.vdate >= ?"
        args = (cutoff,)
    out = []
    for r in conn.execute(q, args):
        rec = json.loads(gzip.decompress(r["blob"]))
        if (rec.get("ISCANCELLED") or "No").strip() == "Yes":
            continue
        if (rec.get("ISOPTIONAL") or "No").strip() == "Yes":
            continue
        out.append(rec)
    return out


def save(conn, rows, cutoff=None):
    """Store raw voucher records. Returns how many were new.

    Idempotent: re-storing a (guid, alterid) already held is a no-op, so a
    retried or overlapping window costs nothing.
    """
    now = dt.datetime.now().isoformat(timespec="seconds")
    payload = []
    for r in rows:
        guid, alt = r.get("GUID"), r.get("ALTERID")
        if not guid or not str(alt or "").strip():
            continue                      # rows without identity
        vdate = r.get("DATE") or ""
        if cutoff and vdate < cutoff:
            continue
        payload.append((
            guid, int(alt), r.get("VOUCHERTYPENAME"), vdate,
            r.get("PARTYLEDGERNAME"), t.amount(r.get("AMOUNT")),
            gzip.compress(json.dumps(r, separators=(",", ":")).encode(), 6),
            now))
    before = conn.total_changes
    conn.executemany(
        "INSERT OR IGNORE INTO voucher"
        "(guid,alterid,vchtype,vdate,party,amount,blob,fetched_at)"
        " VALUES(?,?,?,?,?,?,?,?)", payload)
    conn.commit()
    return conn.total_changes - before


def current(conn, guid):
    """The newest stored version of one voucher, raw record decoded."""
    r = conn.execute("SELECT blob FROM voucher WHERE guid=? "
                     "ORDER BY alterid DESC LIMIT 1", (guid,)).fetchone()
    return json.loads(gzip.decompress(r["blob"])) if r else None


def history(conn, guid):
    """Every stored version of one voucher, oldest first."""
    return [(r["alterid"], json.loads(gzip.decompress(r["blob"])))
            for r in conn.execute(
                "SELECT alterid,blob FROM voucher WHERE guid=? "
                "ORDER BY alterid", (guid,))]


def _pull(conn, lo, hi, cutoff, window=WINDOW, log=print):
    """Walk AlterID (lo, hi] in windows, storing each as it lands."""
    new = total = 0
    at = lo
    while at < hi:
        top = min(at + window, hi)
        t0 = time.time()
        rows = t.vouchers(since=at, upto=top, timeout=600)
        n = save(conn, rows, cutoff)
        new += n
        total += len(rows)
        # Advance the watermark only after the rows are durably stored, so an
        # interrupted backfill resumes from the last window that finished
        # instead of skipping one.
        _set(conn, "voucher_alterid", top)
        conn.commit()
        if rows:
            log(f"   AlterID({at}..{top}] {len(rows):>5} fetched "
                f"{n:>5} new  {time.time() - t0:5.1f}s")
        at = top
    return new, total


def backfill(conn, months=6, log=print):
    """Day 1: pull history back `months`, resumable if interrupted."""
    cutoff = (dt.date.today()
              - dt.timedelta(days=int(months * 30.44))).strftime("%Y%m%d")
    _set(conn, "cutoff", cutoff)
    _, hi = t.watermarks()
    start = int(_get(conn, "voucher_alterid", 0))
    log(f"backfill: cutoff {cutoff}, AlterID {start} -> {hi}")
    new, total = _pull(conn, start, hi, cutoff, log=log)
    log(f"backfill done: {total} fetched, {new} stored (>= {cutoff})")
    return new


def sync(conn, log=print):
    """Thereafter: only what changed since the stored watermark.

    Keyed on AlterID, not date: a voucher DATED in June can be ENTERED in
    October, and a date-driven sync would miss it permanently.
    """
    verify_company(conn, log=lambda *a: None)
    _, hi = t.watermarks()
    lo = int(_get(conn, "voucher_alterid", 0))
    # Tally's counter going BACKWARDS means the company was restored from a
    # backup, reset, or replaced. `lo >= hi` would then be true forever and
    # sync would report "nothing new" while silently never syncing again.
    if hi < lo:
        msg = (f"WATERMARK ROLLBACK: Tally is at {hi}, local is at {lo}. "
               f"The company was probably restored or replaced. Sync is "
               f"halted; a human must confirm the baseline.")
        log(f"sync: {msg}")
        _set(conn, "watermark_rollback",
             f"{dt.datetime.now().isoformat(timespec='seconds')}"
             f" tally={hi} local={lo}")
        conn.commit()
        return 0
    if lo >= hi:
        log(f"sync: nothing new (AlterID {hi})")
        return 0
    new, total = _pull(conn, lo, hi, _get(conn, "cutoff"), log=log)
    log(f"sync: {total} changed, {new} stored")
    return new


def reconcile(conn, log=print):
    """Find deletions by comparing Tally's live GUID set against ours.

    AlterID is blind to deletions, so without this a cancelled invoice stays
    in the archive forever and the website keeps serving it. Uses the cheap
    metadata sweep (no WALK), which is the authoritative list of what still
    exists.

    ponytail: sweeps every voucher and filters by date in Python. ~47s at
    10k vouchers; if that grows painful, filter on $Date inside the TDL
    expression instead.
    """
    cutoff = _get(conn, "cutoff") or "00000000"
    live = {r["GUID"] for r in t.vouchers(since=0, detail=False, timeout=900)
            if r.get("GUID") and (r.get("DATE") or "") >= cutoff}
    ours = {r["guid"] for r in conn.execute(
        "SELECT DISTINCT guid FROM voucher WHERE vdate >= ?", (cutoff,))}
    known_gone = {r["guid"] for r in conn.execute(
        "SELECT guid FROM deleted_voucher")}

    # A truncated, wrong-company or otherwise degraded response makes `live`
    # small or empty, and `ours - live` then tombstones the entire archive.
    # Tally returns wrong data with STATUS=1 and no error, so this is a
    # realistic failure, not a hypothetical one. Refuse to tombstone a bulk
    # difference; a genuine mass deletion is an operator decision, not
    # something a 02:00 cron should infer.
    if ours and len(live) < MIN_LIVE_RATIO * len(ours):
        log(f"reconcile ABORTED: live inventory is {len(live)} against "
            f"{len(ours)} local (< {MIN_LIVE_RATIO:.0%}). Nothing tombstoned."
            f" Suspect a truncated response or the wrong company.")
        _set(conn, "reconcile_aborted",
             f"{dt.datetime.now().isoformat(timespec='seconds')}"
             f" live={len(live)} local={len(ours)}")
        conn.commit()
        return {"live": len(live), "local": len(ours), "deleted": [],
                "missing": [], "restored": [], "aborted": True}

    gone = (ours - live) - known_gone
    missing = live - ours - known_gone
    now = dt.datetime.now().isoformat(timespec="seconds")
    conn.executemany(
        "INSERT OR IGNORE INTO deleted_voucher(guid,last_seen,detected_at)"
        " VALUES(?,?,?)",
        [(g, conn.execute("SELECT MAX(alterid) a FROM voucher WHERE guid=?",
                          (g,)).fetchone()["a"], now) for g in gone])
    # A voucher that reappears was restored, or deleted and re-entered under
    # the same identity. Either way it is live again.
    back = known_gone & live
    conn.executemany("DELETE FROM deleted_voucher WHERE guid=?",
                     [(g,) for g in back])
    conn.commit()

    log(f"reconcile: {len(live)} live, {len(ours)} local, "
        f"{len(gone)} newly deleted, {len(missing)} missing locally, "
        f"{len(back)} restored")
    # Detecting a gap is not repairing it. _pull() advances the watermark
    # whether or not a window returned rows, so a window that came back
    # wrongly empty leaves vouchers that no future sync() will ever look for
    # again. Rewinding the watermark to just below the oldest missing record
    # makes the next sync() re-walk that range and heal it.
    if missing:
        lo = min((int(r["alterid"]) for r in conn.execute(
            "SELECT MIN(alterid) alterid FROM voucher")), default=None)
        rewind = _rewind_target(conn, missing)
        log(f"   {len(missing)} vouchers in Tally but not here; rewinding "
            f"watermark to {rewind} so the next sync() refetches them")
        _set(conn, "voucher_alterid", rewind)
        conn.commit()
    return {"live": len(live), "local": len(ours), "deleted": sorted(gone),
            "missing": sorted(missing), "restored": sorted(back),
            "aborted": False}


def _rewind_target(conn, missing):
    """Lowest AlterID among the missing vouchers, minus one.

    Cheap metadata sweep rather than a guess: we already know the GUIDs, so
    ask Tally for their AlterIDs and rewind to just below the smallest.
    """
    want = set(missing)
    lowest = None
    for r in t.vouchers(since=0, detail=False, timeout=900):
        if r.get("GUID") in want and r.get("ALTERID", "").strip():
            a = int(r["ALTERID"])
            lowest = a if lowest is None else min(lowest, a)
    return max((lowest or 1) - 1, 0)


def expected_per_day(timeout=900):
    """Authoritative voucher count per date, from the cheap metadata sweep.

    An independent second opinion on what each day should contain. Tally
    answers requests it does not understand by returning something else with
    STATUS=1, so a day's archive is only trusted when its count matches.
    """
    counts = {}
    for r in t.vouchers(since=0, detail=False, timeout=timeout):
        d = (r.get("DATE") or "").strip()
        if d:
            counts[d] = counts.get(d, 0) + 1
    return counts


def _retry(fn, attempts=4, base=5, log=print):
    """Retry on a busy Tally with fixed backoff. Deterministic: no judgement.

    TallyBusy means a request is already in flight and there is no way to
    cancel it, so the only safe response is to wait -- never to retry harder
    or widen the request. TallyOffline is not retried here: the caller stops
    and resumes when Tally is back.
    """
    for i in range(attempts):
        try:
            return fn()
        except t.TallyBusy as e:
            if i == attempts - 1:
                raise
            wait = base * (2 ** i)
            log(f"   Tally busy, waiting {wait}s ({i + 1}/{attempts - 1})")
            time.sleep(wait)


def archive(conn, log=print, limit=None, expected=None):
    """Day-chunked full-fidelity archive. Resumable, idempotent, verified.

    Fetches only days that actually hold vouchers, skips days already
    verified, and records a day as done only once its count matches
    expected_per_day(). Safe to run repeatedly and safe to interrupt.

    `expected` is the per-day count map; it costs a ~47s sweep to build, so
    pass it in when calling archive() more than once in a run.
    """
    if expected is None:
        expected = expected_per_day()
    # Compare what we STORED against what Tally says the day holds RIGHT
    # NOW -- not against the expected count recorded at fetch time. A
    # backdated voucher entered into an already-verified day leaves both
    # stored columns equal and agreeing with each other, so the old
    # `vouchers = expected` test called the day done and skipped it forever.
    # Observed: 20260401 verified at 64/64 on 10 Oct, a sale dated 1 Apr
    # entered on 12 Oct made Tally report 65, and the day was never
    # re-fetched.
    done = {r["vdate"] for r in conn.execute(
        "SELECT vdate, vouchers FROM archived_day")
        if r["vouchers"] == expected.get(r["vdate"])}
    todo = [d for d in sorted(expected) if d not in done]
    if limit:
        todo = todo[:limit]
    log(f"archive: {len(expected)} days with vouchers, {len(done)} already "
        f"verified, {len(todo)} to fetch")

    now = dt.datetime.now().isoformat(timespec="seconds")
    stored = mismatched = stale = 0
    for day in todo:
        rows = _retry(lambda: t.day_book(day), log=log)
        # Day Book goes STALE: after a few requests it serves the last day it
        # computed, regardless of SVCURRENTDATE. Observed on 2026-10-12 --
        # day_book() for 0402, 0403 and 0404 all returned the same 37 rows
        # dated 0402. A count-only check misses this whenever two adjacent
        # days hold the same number of vouchers, and would store one day's
        # vouchers under another day's name. So check the payload's own
        # dates, which is the only thing that cannot be faked by a cache.
        got_dates = {(r.get("DATE") or "").strip() for r in rows if r.get("DATE")}
        if rows and got_dates != {day}:
            log(f"   {day} STALE RESPONSE: Day Book returned "
                f"{sorted(got_dates)} -- nothing stored, will retry")
            stale += 1
            continue
        payload = []
        for r in rows:
            guid = (r.get("GUID") or r.get("@REMOTEID") or "").strip()
            alt = (r.get("ALTERID") or "").strip()
            if not guid or not alt:
                continue
            payload.append((
                guid, int(alt), (r.get("DATE") or "").strip(),
                r.get("VOUCHERTYPENAME") or r.get("@VCHTYPE"),
                gzip.compress(json.dumps(r, separators=(",", ":")).encode(), 6),
                now))
        nbytes = sum(len(p[4]) for p in payload)
        conn.executemany(
            "INSERT OR IGNORE INTO voucher_raw"
            "(guid,alterid,vdate,vchtype,blob,fetched_at) VALUES(?,?,?,?,?,?)",
            payload)
        # Record the day with both counts. The `vouchers = expected` filter
        # above means a mismatched day is simply retried next run, with no
        # human needed to notice or decide.
        conn.execute(
            "INSERT INTO archived_day(vdate,vouchers,expected,bytes,fetched_at)"
            " VALUES(?,?,?,?,?) ON CONFLICT(vdate) DO UPDATE SET"
            " vouchers=excluded.vouchers, expected=excluded.expected,"
            " bytes=excluded.bytes, fetched_at=excluded.fetched_at",
            (day, len(payload), expected[day], nbytes, now))
        conn.commit()
        stored += len(payload)
        if len(payload) != expected[day]:
            mismatched += 1
            log(f"   {day} MISMATCH: got {len(payload)}, expected "
                f"{expected[day]} -- will retry next run")
    log(f"archive: {stored} vouchers stored, {mismatched} days mismatched,"
        f" {stale} stale responses")
    if stale:
        log("   Day Book is serving a cached day. Those days stay"
            " unarchived until it recovers; nothing wrong was stored.")
    return {"days": len(todo), "vouchers": stored, "mismatched": mismatched,
            "stale": stale}


def archive_stats(conn):
    r = conn.execute(
        "SELECT COUNT(*) versions, COUNT(DISTINCT guid) vouchers,"
        " MIN(vdate) frm, MAX(vdate) to_, SUM(LENGTH(blob)) bytes"
        " FROM voucher_raw").fetchone()
    d = conn.execute(
        "SELECT COUNT(*) n, SUM(vouchers = expected) ok FROM archived_day"
    ).fetchone()
    return {"versions": r["versions"], "vouchers": r["vouchers"],
            "from": r["frm"], "to": r["to_"], "bytes": r["bytes"] or 0,
            "days": d["n"], "days_verified": d["ok"] or 0}


def raw_voucher(conn, guid):
    """Newest full-fidelity version of one voucher, all 267 fields."""
    r = conn.execute("SELECT blob FROM voucher_raw WHERE guid=? "
                     "ORDER BY alterid DESC LIMIT 1", (guid,)).fetchone()
    return json.loads(gzip.decompress(r["blob"])) if r else None


def save_masters(conn, objtype, rows):
    """Store master records. Returns how many were new. Idempotent."""
    now = dt.datetime.now().isoformat(timespec="seconds")
    payload = []
    for r in rows:
        guid, alt = r.get("GUID"), r.get("ALTERID")
        if not guid or not str(alt or "").strip():
            continue
        payload.append((
            objtype, guid, int(alt), r.get("NAME"), r.get("PARENT"),
            gzip.compress(json.dumps(r, separators=(",", ":")).encode(), 6),
            now))
    before = conn.total_changes
    conn.executemany(
        "INSERT OR IGNORE INTO master"
        "(objtype,guid,alterid,name,parent,blob,fetched_at)"
        " VALUES(?,?,?,?,?,?,?)", payload)
    conn.commit()
    return conn.total_changes - before


def master(conn, objtype, name=None, guid=None):
    """Newest stored version of one master, by name or guid."""
    if guid:
        q, a = "guid=?", (objtype, guid)
    else:
        q, a = "name=?", (objtype, name)
    r = conn.execute(f"SELECT blob FROM master WHERE objtype=? AND {q} "
                     "ORDER BY alterid DESC LIMIT 1", a).fetchone()
    return json.loads(gzip.decompress(r["blob"])) if r else None


def sync_masters(conn, full=False, log=print):
    """Pull masters changed since the stored AlterID watermark.

    Masters carry the same AlterID counter as vouchers, so day-1 backfill and
    every later sync are the same call -- `full=True` just resets the
    watermark to 0. No separate backfill path to keep in step.
    """
    hi, _ = t.watermarks()
    lo = 0 if full else int(_get(conn, "master_alterid", 0))
    if lo >= hi:
        log(f"masters: nothing new (AltMstId {hi})")
        return 0
    verify_company(conn, log=lambda *a: None)
    new = total = 0
    todo = dict(MASTER_SPECS)

    # Accounting masters come from the complete export (520 fields/ledger
    # against 14 from a named list). This is not just richness: if an edited
    # ledger were stored from the narrow Collection, its new (guid, alterid)
    # row would become `current` and the ledger would APPEAR TO LOSE its
    # GSTIN and address, because current() takes MAX(alterid) and
    # INSERT OR IGNORE cannot later enrich that same version.
    #
    # The export cannot be AlterID-filtered, so on an incremental pass we
    # use the cheap Collection purely to DETECT change (~8 ms) and only pay
    # for the 15.5 MB export when something actually changed. Masters change
    # rarely, so most ticks cost nothing.
    if not full:
        changed = 0
        for objtype in ("Ledger", "Group"):
            fields, _ = MASTER_SPECS[objtype]
            probe = t.collection(objtype, "Name", "AlterID", name=f"P{objtype}",
                                 where=f"$AlterID > {lo} AND $AlterID <= {hi}",
                                 timeout=300)
            changed += len(probe)
        if not changed:
            todo.pop("Ledger", None)
            todo.pop("Group", None)

    if full or "Ledger" in todo:
        t0 = time.time()
        # NEITHER source is a superset, so they are merged rather than one
        # replacing the other. The 520-field export has PartyGSTIN, Email,
        # CreditLimit and the rest, but does NOT contain CLOSINGBALANCE or
        # LEDSTATENAME at all. The 14-field Collection has those two. Taking
        # only the export silently zeroed every customer's outstanding and
        # dropped their state.
        extra = {}
        for objtype in ("Ledger",):
            fields, _ = MASTER_SPECS[objtype]
            extra[objtype] = {r.get("GUID"): r for r in t.collection(
                objtype, *fields, name=f"X{objtype}", timeout=600)
                if r.get("GUID")}
        for objtype, rows in t.all_accounts().items():
            if objtype == "Currency":
                continue
            side = extra.get(objtype) or {}
            for r in rows:
                for k, v in (side.get(r.get("GUID")) or {}).items():
                    if k not in r or not str(r.get(k) or "").strip():
                        r[k] = v
            n = save_masters(conn, objtype, rows)
            new += n
            total += len(rows)
            todo.pop(objtype, None)
            log(f"   {objtype:<11} {len(rows):>6} full    {n:>6} new  "
                f"{time.time() - t0:5.1f}s  (export + collection merged)")

    for objtype, (fields, walks) in todo.items():
        t0 = time.time()
        rows = t.collection(objtype, *fields, name=f"M{objtype}", walk=walks,
                            where=f"$AlterID > {lo} AND $AlterID <= {hi}",
                            timeout=600)
        n = save_masters(conn, objtype, rows)
        new += n
        total += len(rows)
        if rows:
            log(f"   {objtype:<11} {len(rows):>6} changed {n:>6} new  "
                f"{time.time() - t0:5.1f}s")
    # Watermark only after every type is stored, so an interruption re-runs
    # the whole pass rather than leaving some types behind.
    _set(conn, "master_alterid", hi)
    conn.commit()
    log(f"masters: {total} changed, {new} stored (AltMstId {lo} -> {hi})")
    return new


def reconcile_masters(conn, log=print):
    """Catch deleted masters -- AlterID is blind to them, same as vouchers.

    A deleted stock item that lingers locally means the website keeps
    offering a product that no longer exists in the books.
    """
    out = {}
    now = dt.datetime.now().isoformat(timespec="seconds")
    for objtype in MASTER_SPECS:
        # name+guid only: cheap enough to sweep in full at any company size
        live = {r["GUID"] for r in t.collection(objtype, "Name", "GUID",
                                                name=f"R{objtype}",
                                                timeout=600)
                if r.get("GUID")}
        ours = {r["guid"] for r in conn.execute(
            "SELECT DISTINCT guid FROM master WHERE objtype=?", (objtype,))}
        known = {r["guid"] for r in conn.execute(
            "SELECT guid FROM deleted_master WHERE objtype=?", (objtype,))}
        gone, missing, back = (ours - live) - known, live - ours - known, known & live
        conn.executemany(
            "INSERT OR IGNORE INTO deleted_master"
            "(objtype,guid,name,last_seen,detected_at) VALUES(?,?,?,?,?)",
            [(objtype, g,
              conn.execute("SELECT name FROM master WHERE objtype=? AND guid=?"
                           " ORDER BY alterid DESC LIMIT 1",
                           (objtype, g)).fetchone()["name"],
              conn.execute("SELECT MAX(alterid) a FROM master WHERE objtype=?"
                           " AND guid=?", (objtype, g)).fetchone()["a"],
              now) for g in gone])
        conn.executemany("DELETE FROM deleted_master WHERE objtype=? AND guid=?",
                         [(objtype, g) for g in back])
        out[objtype] = {"live": len(live), "local": len(ours),
                        "deleted": sorted(gone), "missing": sorted(missing),
                        "restored": sorted(back)}
        if gone or missing:
            log(f"   {objtype:<11} live={len(live):<6} local={len(ours):<6} "
                f"deleted={len(gone)} missing={len(missing)}")
    conn.commit()
    tot_d = sum(len(v["deleted"]) for v in out.values())
    tot_m = sum(len(v["missing"]) for v in out.values())
    log(f"reconcile masters: {tot_d} newly deleted, {tot_m} missing locally")
    if tot_m:
        log("   WARNING: masters exist in Tally but not here -- run"
            " sync_masters()")
    return out


def master_stats(conn):
    rows = conn.execute(
        "SELECT objtype, COUNT(*) versions, COUNT(DISTINCT guid) n,"
        " SUM(LENGTH(blob)) bytes FROM master GROUP BY objtype"
        " ORDER BY objtype").fetchall()
    return {r["objtype"]: {"count": r["n"], "versions": r["versions"],
                           "bytes": r["bytes"] or 0} for r in rows}


def stats(conn):
    r = conn.execute(
        "SELECT COUNT(*) versions, COUNT(DISTINCT guid) vouchers,"
        " MIN(vdate) frm, MAX(vdate) to_, SUM(LENGTH(blob)) bytes"
        " FROM voucher").fetchone()
    d = conn.execute("SELECT COUNT(*) n FROM deleted_voucher").fetchone()["n"]
    return {"versions": r["versions"], "vouchers": r["vouchers"],
            "from": r["frm"], "to": r["to_"], "stored_bytes": r["bytes"] or 0,
            "deleted": d, "watermark": _get(conn, "voucher_alterid")}


def _row(guid, alt, date="20260601", amt="100.00"):
    return {"GUID": guid, "ALTERID": str(alt), "DATE": date,
            "VOUCHERTYPENAME": "Sales", "PARTYLEDGERNAME": "Shop",
            "AMOUNT": amt}


def _check_logic():
    """Store semantics, no Tally needed."""
    import tempfile
    c = open_db(os.path.join(tempfile.mkdtemp(), "t.db"))

    assert save(c, [_row("g1", 10), _row("g2", 11)]) == 2
    assert save(c, [_row("g1", 10)]) == 0, "save must be idempotent"
    # an edit is a new version, never an overwrite
    assert save(c, [_row("g1", 20, amt="250.00")]) == 1
    assert len(history(c, "g1")) == 2, "history must keep both versions"
    assert t.amount(current(c, "g1")["AMOUNT"]) == 250.0, "current = max alterid"
    # rows with no identity, and rows before the cutoff, are skipped
    assert save(c, [{"GUID": None, "ALTERID": "9"}]) == 0
    assert save(c, [_row("g9", 30, date="20250101")], cutoff="20260101") == 0
    assert stats(c)["vouchers"] == 2

    c.execute("INSERT INTO deleted_voucher(guid,last_seen,detected_at)"
              " VALUES('g2',11,'now')")
    c.commit()
    assert stats(c)["deleted"] == 1
    c.close()
    print("store logic ok (append-only history, idempotent, cutoff, deletes)")


def _check_live():
    """Pull, resume, and reconcile against the real Tally."""
    import tempfile
    try:
        _, hi = t.watermarks()
    except t.TallyError as e:
        print(f"skipping live check -- {type(e).__name__}: {e}")
        return

    c = open_db(os.path.join(tempfile.mkdtemp(), "live.db"))
    quiet = lambda *a, **k: None

    # Resume: a half-done backfill picks up where it stopped. Seeding the
    # watermark near the top must fetch only the tail, not restart from 0.
    _set(c, "voucher_alterid", hi - 300)
    _set(c, "cutoff", "20000101")
    c.commit()
    backfill(c, months=1200, log=quiet)
    tail = stats(c)["vouchers"]
    assert int(_get(c, "voucher_alterid")) == hi, "watermark did not advance"
    assert 0 < tail < 1000, f"resume refetched too much ({tail})"
    print(f"resume ok: seeded at {hi - 300}, fetched only the tail "
          f"({tail} vouchers)")

    # Now the real day-1 path: from scratch, whole company.
    _set(c, "voucher_alterid", 0)
    c.commit()
    t0 = time.time()
    backfill(c, months=1200, log=quiet)
    secs = time.time() - t0
    s = stats(c)
    assert s["vouchers"] > tail, "full backfill stored no more than the tail"
    assert s["from"] and s["to"], "stored rows look empty"
    print(f"backfill ok: {s['vouchers']} vouchers, {s['versions']} versions, "
          f"{s['from']}..{s['to']}, {s['stored_bytes']:,} B in {secs:.0f}s "
          f"({s['stored_bytes'] // max(s['vouchers'], 1)} B/voucher)")

    assert sync(c, log=quiet) == 0, "sync right after a backfill must be a no-op"

    # The status and reference fields must actually arrive: a cancelled
    # voucher still exists in Tally, so reconcile() cannot catch it and only
    # IsCancelled distinguishes it from a live sale.
    one = current(c, c.execute("SELECT guid FROM voucher WHERE vchtype='Sales'"
                               " LIMIT 1").fetchone()["guid"])
    for k in ("ISCANCELLED", "ISOPTIONAL", "UPDATEDDATETIME", "PARTYNAME",
              "EFFECTIVEDATE"):
        assert k in one, f"{k} missing from stored voucher"
    nlines = len(one.get("ALLINVENTORYENTRIES.LIST", []))
    print(f"   voucher records carry {len(one)} fields, {nlines} line items "
          f"(minimal field list gave 34 and dropped the walks)")

    # plant a voucher Tally has never heard of; reconcile must catch it
    c.execute("INSERT INTO voucher(guid,alterid,vchtype,vdate,party,amount,"
              "blob,fetched_at) VALUES('phantom',1,'Sales','20260601','X',1,"
              "?,'now')", (gzip.compress(b'{"GUID":"phantom"}'),))
    c.commit()
    out = reconcile(c, log=quiet)
    assert "phantom" in out["deleted"], "reconcile missed a deleted voucher"
    assert not out["missing"], f"sync left gaps: {out['missing'][:3]}"
    print(f"reconcile ok: caught the phantom among {out['live']} live "
          f"vouchers, no gaps")

    # and it must not re-report the same deletion next run
    again = reconcile(c, log=quiet)
    assert not again["deleted"], "deletion re-reported on second pass"

    # --- masters ---------------------------------------------------------
    t0 = time.time()
    n = sync_masters(c, full=True, log=quiet)
    secs = time.time() - t0
    assert n, "masters sync stored nothing"
    ms = master_stats(c)
    for objtype in MASTER_SPECS:
        assert objtype in ms and ms[objtype]["count"], f"no {objtype} stored"
    print(f"masters ok in {secs:.0f}s: "
          + ", ".join(f"{k} {v['count']}" for k, v in ms.items()))

    # The full export must bring far more per ledger than a named field list.
    led = master(c, "Ledger", name=c.execute(
        "SELECT name FROM master WHERE objtype='Ledger' AND name IS NOT NULL"
        " LIMIT 1").fetchone()["name"])
    assert led and len(led) > 100, \
        f"ledger has only {len(led or {})} fields -- full export not used"
    print(f"   ledger records carry {len(led)} fields "
          f"(named-field path gave 11)")

    # AlterID filtering must work on masters too, not just vouchers
    assert sync_masters(c, log=quiet) == 0, "masters sync is not idempotent"

    # the fields we actually depend on must have arrived
    sample = c.execute("SELECT name FROM master WHERE objtype='StockItem'"
                       " AND name IS NOT NULL LIMIT 1").fetchone()["name"]
    item = master(c, "StockItem", name=sample)
    assert item and item.get("BASEUNITS"), "stock item missing BASEUNITS"
    count = c.execute("SELECT COUNT(*) n FROM master WHERE"
                      " objtype='StockItem'").fetchone()["n"]
    assert count >= 500, f"only {count} stock items stored"
    print(f"   sample item {sample[:34]!r} "
          f"units={item.get('BASEUNITS')} price={item.get('STANDARDPRICE')!r} "
          f"hsn={item.get('GSTHSNCODE')!r}")

    out = reconcile_masters(c, log=quiet)
    assert not any(v["missing"] for v in out.values()), \
        f"masters reconcile found gaps: {out}"
    assert not any(v["deleted"] for v in out.values()), \
        "masters reconcile invented deletions"

    # plant a phantom stock item; reconcile must catch it
    c.execute("INSERT INTO master(objtype,guid,alterid,name,parent,blob,"
              "fetched_at) VALUES('StockItem','ghost',1,'Ghost Item','X',?,"
              "'now')", (gzip.compress(b'{"NAME":"Ghost Item"}'),))
    c.commit()
    out = reconcile_masters(c, log=quiet)
    assert "ghost" in out["StockItem"]["deleted"], "missed a deleted master"
    assert not reconcile_masters(c, log=quiet)["StockItem"]["deleted"], \
        "master deletion re-reported"
    print("masters reconcile ok: caught the ghost, no gaps")

    # --- full-fidelity day archive ---------------------------------------
    exp = expected_per_day()          # built once, reused below
    # Compare against what the backfill actually stored, never a literal
    # count: a hardcoded 10657 broke the moment two vouchers were entered.
    # Tombstoned rows are excluded: expected_per_day() reflects what Tally
    # holds live, and this check plants a phantom voucher earlier on.
    stored_now = c.execute(
        "SELECT COUNT(DISTINCT v.guid) n FROM voucher v"
        " LEFT JOIN deleted_voucher d ON d.guid = v.guid"
        " WHERE d.guid IS NULL").fetchone()["n"]
    assert exp, "expected-count sweep returned nothing"
    assert sum(exp.values()) == stored_now, (
        f"expected-count sweep ({sum(exp.values())}) disagrees with what the "
        f"backfill stored ({stored_now})")
    t0 = time.time()
    r1 = archive(c, log=quiet, limit=3, expected=exp)
    secs = time.time() - t0
    a = archive_stats(c)
    assert r1["vouchers"], "archiver stored nothing"
    assert not r1["mismatched"], f"{r1['mismatched']} days failed verification"
    # Day Book can go stale mid-run and serve a cached day. The invariant is
    # not "3 days archived" -- it is "everything archived is correct, and
    # anything suspect was refused rather than stored under the wrong date".
    assert a["days"] == a["days_verified"], \
        f"an archived day is not verified: {a}"
    assert a["days"] + r1.get("stale", 0) == 3, \
        f"days unaccounted for: archived {a['days']}, stale {r1.get('stale')}"
    wrong = c.execute(
        "SELECT a.vdate FROM archived_day a LEFT JOIN voucher_raw r"
        " ON r.vdate = a.vdate GROUP BY a.vdate"
        " HAVING COUNT(DISTINCT r.guid) != a.vouchers").fetchall()
    assert not wrong, f"wrong-day rows stored for {[r['vdate'] for r in wrong]}"
    print(f"archive ok: {a['days']} days verified"
          + (f", {r1['stale']} refused as stale" if r1.get("stale") else "")
          + f", {a['vouchers']} vouchers, {a['bytes']:,} B in {secs:.0f}s")

    # full fidelity must actually be fuller than the sync path
    g = c.execute("SELECT guid FROM voucher_raw LIMIT 1").fetchone()["guid"]
    full, lean = raw_voucher(c, g), current(c, g)
    assert full, "raw voucher not retrievable"
    assert len(full) > len(lean or {}), \
        f"archive ({len(full)}) is not richer than sync ({len(lean or {})})"
    print(f"   same voucher: {len(full)} fields archived vs {len(lean)} synced")

    # resume: verified days are skipped, so a second run advances to new days
    done_before = {(r["vdate"], r["vouchers"], r["fetched_at"])
                   for r in c.execute("SELECT vdate,vouchers,fetched_at"
                                      " FROM archived_day")}
    r2 = archive(c, log=quiet, limit=3, expected=exp)
    done_after = {(r["vdate"], r["vouchers"], r["fetched_at"])
                  for r in c.execute("SELECT vdate,vouchers,fetched_at"
                                     " FROM archived_day")}
    # The invariant is that already-verified days are SKIPPED, untouched --
    # not that new days appear, which depends on Day Book not being stale.
    assert done_before <= done_after, \
        "a second run re-fetched days that were already verified"
    assert not r2["mismatched"]

    # A day whose CURRENT expected count no longer matches what we stored
    # must be re-fetched. This is the backdated-entry case: a voucher dated
    # into an already-verified day. Simulated by claiming one more voucher
    # exists for that day than we hold.
    after = {r["vdate"] for r in c.execute(
        "SELECT vdate FROM archived_day")}
    day = min(after)
    exp2 = dict(exp)
    exp2[day] = exp2[day] + 1
    assert day in [d for d in sorted(exp2)
                   if d not in {r["vdate"] for r in c.execute(
                       "SELECT vdate, vouchers FROM archived_day")
                       if r["vouchers"] == exp2.get(r["vdate"])}], \
        "a day whose live count grew was still treated as done"

    # a day recorded as short must be retried, not treated as done
    short = min(after)
    c.execute("UPDATE archived_day SET vouchers = expected - 1 WHERE vdate=?",
              (short,))
    c.commit()
    r3 = archive(c, log=quiet, limit=1, expected=exp)
    assert r3["days"] == 1, "a mismatched day was not retried"
    assert not r3["mismatched"], "retry of a good day still mismatched"
    assert c.execute("SELECT vouchers = expected v FROM archived_day"
                     " WHERE vdate=?", (short,)).fetchone()["v"], \
        "retried day was not re-verified"
    print("archive resume ok: verified days skipped, short day re-fetched "
          "and re-verified")

    # --- safety guards ----------------------------------------------------
    # 1. company binding: a different company must be refused, not merged
    assert _get(c, "company"), "company was never bound"
    _set(c, "company", "Some Other Company Pvt Ltd")
    c.commit()
    try:
        verify_company(c, log=quiet)
    except CompanyMismatch:
        pass
    else:
        raise AssertionError("a different company was NOT refused")
    _set(c, "company", t.company())
    c.commit()

    # 2. watermark rollback: Tally going backwards must halt, not idle
    real = _get(c, "voucher_alterid")
    _set(c, "voucher_alterid", 99_999_999)
    c.commit()
    assert sync(c, log=quiet) == 0
    assert _get(c, "watermark_rollback"), "rollback was not recorded"
    _set(c, "voucher_alterid", real)
    conn_rb = _get(c, "watermark_rollback")
    c.execute("DELETE FROM sync_state WHERE key='watermark_rollback'")
    c.commit()

    # 3. reconcile must refuse to tombstone on an implausible live set
    saved_fn = t.vouchers
    t.vouchers = lambda *a, **k: []          # simulate a degraded response
    try:
        out = reconcile(c, log=quiet)
    finally:
        t.vouchers = saved_fn
    assert out["aborted"], "reconcile did not abort on an empty live set"
    assert not out["deleted"], "reconcile tombstoned on a degraded response"
    assert c.execute("SELECT COUNT(*) n FROM deleted_voucher").fetchone()["n"] \
        < 10, "bulk tombstoning happened anyway"
    print(f"guards ok: company mismatch refused, rollback halted "
          f"({conn_rb.split()[-2]}), empty live set aborted reconcile")

    # 4. stock snapshot: polled, not AlterID-driven, and timestamped
    n = snapshot_stock(c, log=quiet)
    assert n >= 600, f"only {n} items snapshotted"
    now_stock = stock_now(c)
    assert len(now_stock) == n, "stock_now did not return one row per item"
    assert all(r["observed_at"] for r in now_stock), "snapshot has no timestamp"
    nonzero = [r for r in now_stock if r["qty"]]
    assert nonzero, "every stock quantity came back zero"
    print(f"stock ok: {n} items, {len(nonzero)} non-zero, "
          f"observed_at={now_stock[0]['observed_at']}")

    nb = snapshot_balances(c, log=quiet)
    bals = balances_now(c)
    assert nb >= 700, f"only {nb} balances snapshotted"
    assert len(bals) == nb
    owing = [b for b in bals.values() if b["closing"]]
    assert len(owing) >= 300,         f"only {len(owing)} parties have a balance -- the merge regressed"
    assert all(b["observed_at"] for b in bals.values())
    print(f"balances ok: {nb} parties, {len(owing)} non-zero, polled not "
          f"derived from the master record")

    # the merge must give BOTH a 520-field ledger AND its balance/state
    nm = c.execute("SELECT name FROM master WHERE objtype='Ledger'"
                   " AND name IS NOT NULL LIMIT 1").fetchone()["name"]
    lg = master(c, "Ledger", name=nm)
    assert len(lg) > 100, f"ledger lost the full export ({len(lg)} fields)"
    assert "CLOSINGBALANCE" in lg,         "merge dropped CLOSINGBALANCE -- the export alone does not have it"
    print(f"ledger merge ok: {len(lg)} fields incl. CLOSINGBALANCE")

    # 5. live projection excludes tombstoned and cancelled vouchers
    allv = {r["guid"] for r in c.execute("SELECT DISTINCT guid FROM voucher")}
    live = live_vouchers(c)
    assert live, "live_vouchers returned nothing"
    assert len(live) <= len(allv)
    victim = next(iter(allv))
    c.execute("INSERT OR IGNORE INTO deleted_voucher(guid,last_seen,"
              "detected_at) VALUES(?,1,'now')", (victim,))
    c.commit()
    assert len(live_vouchers(c)) == len(live) - 1, \
        "live_vouchers did not exclude a tombstoned voucher"
    print(f"live projection ok: {len(live)} live of {len(allv)} stored, "
          f"tombstones excluded")
    c.close()
    print("store ok")


if __name__ == "__main__":
    _check_logic()
    _check_live()
