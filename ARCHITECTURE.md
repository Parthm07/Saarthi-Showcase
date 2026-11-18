# TallyPrime → Local Archive: Technical Reference

State as of 2026-10-10. Everything below is verified against the live
TallyPrime instance on this machine, not inferred from documentation.

---

## 1. What exists

| File | Lines | Role |
|---|---|---|
| `tally_connector.py` | 470 | Protocol layer. Speaks XML-over-HTTP to Tally. No state. |
| `tally_store.py` | 769 | Persistence + sync logic. SQLite, append-only. |
| `tally_agent.py` | 382 | Unattended scheduler. One process, one loop. |
| `tally_raw.db` | 108 MB | The archive. 10,657 vouchers, 1,433 masters. |

Stdlib only. No `pip install`. Python 3.14.6.

Each module has a self-check runnable as `python <file>.py`
(`tally_agent.py --self-check`). They hit the live Tally and assert against
real data, not fixtures.

### Current archive contents

```
synced   : 10657 vouchers, watermark AltVchId=28581
archived : 10657 vouchers, 185/185 days verified
masters  : StockItem 625, Ledger 734, Group 47, StockGroup 25,
           Godown 1, Unit 1
span     : 20260401 .. 20261010
```

---

## 2. Environment (verified)

| Fact | Value | How established |
|---|---|---|
| Tally release | **TallyPrime 7.0.0 (27974)** | Gateway Server banner on :9999 |
| Licence | Permanent, serial 738214632 | same |
| **TallyPrime Developer** | **No License** | same — closes the TDL/TCP route |
| XML API | `tally.exe` on **:9000** | `Get-NetTCPConnection` owner = PID of tally.exe |
| Gateway Server | `tallygatewayserver.exe` on **:9999** | licence server only, **no data API** |
| Company | `Demo Bill - (from 1-Apr-26)` | `SVCURRENTCOMPANY` |
| Data path | `D:\Tally Project\100000` | `tally.ini: Load=100000` |
| Python | `%LOCALAPPDATA%\Python\bin\python.exe` | bare `python` hits a Store stub that exits 49 |

`tally.ini` reads `Client Server=None` yet :9000 serves XML — the runtime
setting from the Tally UI overrides the ini file. **Do not "fix" that line.**

---

## 3. The protocol, and its five silent failure modes

One endpoint, `POST http://localhost:9000`, `Content-Type: text/xml`. Two
verbs: `<TALLYREQUEST>Export</TALLYREQUEST>` to read,
`Import` to write (unused so far).

**The single most important property: Tally answers a request it does not
understand by returning something else, with `<STATUS>1</STATUS>` and no
error.** Every difficulty in this project traces to this. Proven cases:

| Request | Result |
|---|---|
| `SVEXPORTFORMAT = $$SysName:JSON` | byte-identical **XML** |
| `Day Book` + `SVFROMDATE`/`SVTODATE` alone | **today's** data; byte-identical across 3 different ranges |
| `Day Book` + `SVVOUCHERTYPE` | filter ignored entirely |
| Unknown `NATIVEMETHOD` (`HSNCode`, `PartyGSTIN`, `Email`, `CreditPeriod`, `MRPRate`, `RoundLimit`) | field simply absent |
| Unevaluable `FILTER` (`$$IsLedOfGrp:$Parent:…`) | **0 rows** |
| **Fewer** fields requested on a Voucher collection | different default field set **and all `WALK`s dropped** — 1,038 vouchers, 0 line items |

### The exception: a bad OBJECT TYPE is not silent, it is destructive

The three kinds of mistake are not equally safe, and this one cost two days:

| Wrong thing | Consequence |
|---|---|
| **field** name | silently dropped. Harmless. |
| **filter** formula | zero rows, no error. Harmless. |
| **object type** | `Incorrect Object Type!` internal error. Took the loaded company down and left `tally.exe` squatting on port 9000 for two days. |

From this project's own `tallyerr.log`:

```
11-10-2026 13:48:01, PID: 46776
Internal Error.  Contact Tally Solutions.
Incorrect Object Type!
```

Caused by probing `PriceLevel` and `PriceList` — types that do not exist.
`collection()` now refuses any `obj_type` outside `KNOWN_TYPES` before
sending it, because a typo must not be able to crash an accountant's Tally.

### TDL is available after all

A TDL extension gets compile-time schema checking and none of the silent
failures above. TallyPrime Developer is absent — but that is only needed to
produce a distributable compiled `.tcp`. **Plain-text TDL source loads on
this licence**, verified: a collection defined in a `.txt` file registered
via `TDL=` in `tally.ini` returned all 625 stock items when queried by name
over the XML API, with no inline definition. (The probe has since been
removed and `tally.ini` restored byte-identical.)

Not used, deliberately. The XML layer works and is tested, and TDL's main
benefit is a cost already paid. TDL is held in reserve for the two things
XML genuinely cannot reach:

1. **Godown-wise and batch stock.** `GODOWNNAME` never returned from any
   Collection and `BatchAllocations` as a WALK gave nothing. A TDL probe of
   `Batch : StockItem` also returned 0 rows — but this company has one
   godown and `IsBatchWiseOn=No` on all 625 items, so it is untestable here.
   Needs a batch-enabled company.
2. A purpose-built report replacing several WALK queries, if performance
   ever bites at ~360k vouchers.

Field names must still be **probed, never assumed**.

The one genuine error string is:

```xml
<RESPONSE>Unknown Request, cannot be processed</RESPONSE>
```

It means **malformed request XML**, not a connection problem.

### Other hard constraints

- **Single-threaded.** One request at a time. A heavy request makes every
  other one time out *and freezes the operator's UI*.
- **No cancellation.** Once accepted, a request cannot be called back. An
  unbounded `Day Book` export burned 3,000 CPU-seconds and 2.7 GB over two
  hours; killing the HTTP client did nothing. Recovery = kill `tally.exe`.
  The only defence is never sending the request.
- **Illegal XML in the payload.** Tally emits `&#4;` (a control char, invalid
  in XML 1.0) ~6,045 times in a full master export, as a wildcard marker on
  names like `&#4; Any`. ElementTree rejects the **whole document** over one.

---

## 4. Layer 1 — `tally_connector.py`

Stateless. Builds envelopes, parses responses, classifies failures.

### Configuration

```python
URL = os.environ.get("TALLY_URL", "http://localhost:9000")
```

Resolved **per call**, not as a default argument — `def tally(xml, url=URL)`
binds at def time and would make the setting permanently unchangeable.
`ServerPort` is configurable in `tally.ini` and Tally may sit on another
machine, so this must be overridable at deploy time.

### Error taxonomy

```python
class TallyError(Exception):      ...   # base
class TallyOffline(TallyError):   ...   # nothing listening
class TallyBusy(TallyError):      ...   # accepted, not answering
```

The distinction is load-bearing: **a busy Tally must not be retried**, since
retrying piles a second heavy request onto a wedged process. `urlopen` cannot
tell a connect timeout from a read timeout, so the socket is asked directly —
a wedged Tally still completes a TCP handshake while its UI is frozen:

```python
except (urllib.error.URLError, OSError) as e:
    reason = getattr(e, "reason", e)
    if isinstance(reason, (TimeoutError, socket.timeout)):
        if _port_accepts(url):
            raise TallyBusy(...) from e
        raise TallyOffline(...) from e
    raise TallyOffline(...) from e
```

### XML sanitiser

```python
_CHARREF = re.compile(r"&#(x[0-9a-fA-F]+|[0-9]+);")

def _legal(code):
    return (code in (0x9, 0xA, 0xD)
            or 0x20 <= code <= 0xD7FF
            or 0xE000 <= code <= 0xFFFD
            or 0x10000 <= code <= 0x10FFFF)
```

Applied to every response before `ET.fromstring`.

### `collection(obj_type, *fields, name, where, walk, timeout)`

The workhorse. Emits a TDL `<COLLECTION>` with explicit `<NATIVEMETHOD>`
fields, optional `<WALK>` sub-collections, and an optional `<FILTER>`:

```python
walks = "".join(f"<WALK>{w}</WALK>" for w in walk)
flt   = "<FILTER>Flt</FILTER>" if where else ""
sysf  = (f'<SYSTEM TYPE="Formula" NAME="Flt">{escape(where)}</SYSTEM>'
         if where else "")
```

Three non-obvious details, each one a bug that was shipped and then fixed:

1. **`escape(where)` is mandatory.** TDL comparisons contain `<` and `&`
   (`$AlterID <= 28581`), which start a tag or an entity and get the whole
   envelope rejected as "Unknown Request".
2. **Results are scoped to `<DATA>`.** The response header carries a
   `<CMPINFO>` block of per-type counters using the *same tag names*
   (`<COMPANY>0</COMPANY>`), which a document-wide `.//COMPANY` search picks
   up as a phantom record with a null name.
3. **`WALK` is the only way to get sub-collections.** `<FETCH>*</FETCH>` is
   ignored (returned 27 rows instead of 3, no nested data);
   `TYPE=Object` with `ID TYPE="Name"|"ID"|"GUID"` all answer
   `Could not find Voucher`. Nested sub-collections ride along
   automatically — walking `AllInventoryEntries` also yields its
   `BATCHALLOCATIONS.LIST` and `ACCOUNTINGALLOCATIONS.LIST` children.

### Value parsers

Tally sends quantities and rates as strings with the unit baked in, and uses
a blank tag for both zero and not-set:

```python
qty("12 PCS")    -> (12.0, "PCS")
qty("1,250 BOX") -> (1250.0, "BOX")
qty("")          -> (0.0, None)
rate("1.00/PCS") -> (1.0, "PCS")
amount("-1,234.50") -> -1234.5
```

### Party classification

```python
IS_CUSTOMER = "$$IsLedOfGrp:$Name:$$GroupSundryDebtors"
IS_SUPPLIER = "$$IsLedOfGrp:$Name:$$GroupSundryCreditors"
```

**`$Name`, not `$Parent`.** `$$IsLedOfGrp:$Parent:…` and
`$$IsLedOfGrp:$Guid:…` both return **0 rows, silently**.

And `$Parent = "Sundry Debtors"` returns **63 of 606** customers — it drops
90% of the base, because customers live in 18 route-wise sub-groups
(`Dewas Debtors`, `Nagda Route`, `Mhow-Rau-Bijalpur`, …). Classification is
by **group membership**, never by balance sign: 18 of 606 customers carry a
positive (Cr) balance from advances and 8 of 59 suppliers carry a negative
one.

### Change detection

```python
def watermarks():
    """(master_alter_id, voucher_alter_id) -- the company's current maxima."""
    c = collection("Company", "Name", "AltMstId", "AltVchId", name="Cmp")
    return int(c[0]["ALTMSTID"]), int(c[0]["ALTVCHID"])

def vouchers(since=0, upto=None, detail=True, timeout=180):
    where = f"$AlterID > {since} AND $AlterID <= {upto}"
    return collection("Voucher", *VOUCHER_FIELDS, name="VSync", where=where,
                      walk=VOUCHER_WALKS if detail else (), timeout=timeout)
```

`AlterID` is a per-company counter incremented on every create and edit,
shared across masters and vouchers in one sequence, with separate maxima
(`AltMstId=11208`, `AltVchId=28581`).

**Sync is keyed on AlterID, never on date.** A voucher *dated* in June can be
*entered* in October — routine in Indian accounting — and a date-driven
"since yesterday" sync would miss it permanently. AlterID catches it because
the edit is new even when the date is old.

Verified: range filters honour their bounds exactly, and `AlterID 0..max`
returns all 10,657 vouchers, matching the independent date-based count.

**AlterID cannot see deletions.** A deleted voucher stops existing and no
counter moves. That is `reconcile()`'s job (§5).

### `day_book(day)` — full fidelity, one day

```python
f'<SVFROMDATE TYPE="Date">{day}</SVFROMDATE>'
f'<SVTODATE TYPE="Date">{day}</SVTODATE>'
f'<SVCURRENTDATE TYPE="Date">{day}</SVCURRENTDATE>'   # <-- mandatory
f'<EXPLODEFLAG>Yes</EXPLODEFLAG>'
```

**`SVCURRENTDATE` is the whole trick.** Without it the report silently
returns *today*; with it, exactly the requested day. A *range* is still not
honoured even with it, so the caller loops one day at a time — which is also
what keeps each request bounded (~9 MB on a 133-voucher day) and unable to
wedge Tally.

Yields **783 fields per voucher** (267 of them populated) against the
Collection path's 42.

### `all_accounts()` — full fidelity, accounting masters

`TYPE=Data, ID=List of Accounts`. Returns **520 distinct tags per ledger**
against 11 from a named Collection, for all 734 ledgers and 47 groups, with
`GUID` and `ALTERID` on every row. 15.5 MB in ~6 s.

Two limits:
- **No stock items.** `All Masters`, `List of StockItems` and `Stock Summary`
  all return empty or are rejected. A Collection asked for *no* fields
  returns only `NAME`. Stock items must be named field-by-field.
- **Cannot be filtered by AlterID** — all-or-nothing, so it serves day-1
  backfill and reconciliation while incremental sync uses the Collection.

**Neither path is a superset of the other.** `ADDRESS.LIST` comes from the
Collection but is absent from `List of Accounts`; `BatchRate`,
`BatchDiscount` and `IsNegIsPosSet` come from the Collection but not from
`day_book`. Both views are kept.

---

## 5. Layer 2 — `tally_store.py`

SQLite, append-only, git-like. `sqlite3` is stdlib; Postgres is the cloud's
job, not a local archive's.

### Schema

```sql
-- 42-field sync path, the every-minute target
CREATE TABLE voucher (
    guid TEXT NOT NULL, alterid INTEGER NOT NULL,
    vchtype TEXT, vdate TEXT, party TEXT, amount REAL,
    blob BLOB NOT NULL,            -- gzipped JSON of the full record
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (guid, alterid)
);

-- 783-field archive path, full fidelity
CREATE TABLE voucher_raw (
    guid TEXT NOT NULL, alterid INTEGER NOT NULL,
    vdate TEXT, vchtype TEXT, blob BLOB NOT NULL, fetched_at TEXT NOT NULL,
    PRIMARY KEY (guid, alterid)
);

-- one table for all six master types, not six near-identical ones
CREATE TABLE master (
    objtype TEXT NOT NULL, guid TEXT NOT NULL, alterid INTEGER NOT NULL,
    name TEXT, parent TEXT, blob BLOB NOT NULL, fetched_at TEXT NOT NULL,
    PRIMARY KEY (objtype, guid, alterid)
);

CREATE TABLE deleted_voucher (guid TEXT PRIMARY KEY, last_seen INTEGER,
                              detected_at TEXT NOT NULL);
CREATE TABLE deleted_master  (objtype TEXT, guid TEXT, name TEXT,
                              last_seen INTEGER, detected_at TEXT NOT NULL,
                              PRIMARY KEY (objtype, guid));

-- a day is listed here only once its count has been verified
CREATE TABLE archived_day (vdate TEXT PRIMARY KEY, vouchers INTEGER NOT NULL,
                           expected INTEGER NOT NULL, bytes INTEGER NOT NULL,
                           fetched_at TEXT NOT NULL);

CREATE TABLE sync_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
```

### The key design decision: `PRIMARY KEY (guid, alterid)`

An edit stores a **new row**, never an overwrite. `current()` returns
`MAX(alterid)`; `history()` returns every version. This makes "what did this
invoice look like before it was edited?" answerable — which Tally itself does
not make easy — and it is the thing a billing dispute needs.

Rows are never deleted. A cancelled invoice keeps its history; the fact of
deletion is recorded in `deleted_voucher` instead.

### Operations

| Function | Job |
|---|---|
| `backfill(conn, months=6)` | day 1: AlterID 0 → max, date cutoff applied |
| `sync(conn)` | thereafter: watermark → max |
| `archive(conn)` | day-chunked full-fidelity pull, verified |
| `reconcile(conn)` | diff live GUID set vs local → deletions |
| `sync_masters(conn, full=False)` | masters; accounting masters always via `all_accounts()` |
| `snapshot_stock(conn)` / `stock_now(conn)` | polled stock quantity + staleness |
| `live_vouchers(conn)` | current, not tombstoned, not cancelled |
| `verify_company(conn)` | binds the DB to one company |
| `reconcile_masters(conn)` | same for masters |
| `current/history/raw_voucher/master` | read-back |
| `stats/archive_stats/master_stats` | counts |

`backfill` and `sync` share one implementation (`_pull`), so there is no
second code path to keep in step:

```python
def _pull(conn, lo, hi, cutoff, window=WINDOW, log=print):
    at = lo
    while at < hi:
        top = min(at + window, hi)
        rows = t.vouchers(since=at, upto=top, timeout=600)
        n = save(conn, rows, cutoff)
        # Advance the watermark only AFTER the rows are durably stored, so an
        # interrupted backfill resumes from the last window that finished
        # instead of skipping one.
        _set(conn, "voucher_alterid", top)
        conn.commit()
        at = top
```

### Window size: `WINDOW = 5000`

Measured per-voucher cost **falls** as the window grows, because fixed
per-request overhead dominates:

| AlterID window | Vouchers | ms/voucher |
|---|---|---|
| 500 | 185 | **32** |
| 1,000 | 486 | 14 |
| 2,000 | 1,038 | 8 |
| 4,000 | 2,170 | **5** |

So the optimal strategy is *large* fixed windows, and **there is no
company-size threshold to branch on** — one window serves a kirana shop and a
large distributor alike.

### Safety guards

Five refusals, all deterministic, added after an external architecture review
(`TallyPrime_Architecture_Review.pdf`). Each one closes a path where Tally's
silent-degradation behaviour turns into data loss:

| Guard | Failure it prevents |
|---|---|
| `verify_company()` | A different company loaded in Tally merges two sets of books into one archive. `company()` takes the first row of whatever is open, so the name is bound on first use and compared on every write. Raises `CompanyMismatch`. |
| `MIN_LIVE_RATIO = 0.5` in `reconcile()` | A truncated or wrong-company response makes `live` small, and `ours - live` then tombstones the **entire archive**. Refuses to tombstone a bulk difference and records `reconcile_aborted`. |
| Rollback detection in `sync()` | Tally's counter going *backwards* (company restored from backup) makes `lo >= hi` true forever — sync would report "nothing new" and never sync again. Halts and records `watermark_rollback`. |
| Watermark rewind on `missing` | `_pull()` advances the watermark whether or not a window returned rows, so a wrongly-empty window leaves vouchers no future sync looks for. Rewinds to just below the lowest missing AlterID so the next `sync()` heals it. |
| `live_vouchers()` | `current()` returns whatever is stored. Stock and revenue logic must exclude tombstoned **and** cancelled/optional vouchers — a cancelled voucher still exists in Tally, so `reconcile()` will never flag it. |

### No race on concurrent entry

`sync()` reads `hi` once at the start and advances the watermark only as far
as `hi`. A voucher saved *during* a running sync gets an AlterID above `hi`
and is picked up next cycle — never skipped, never double-fetched.

**Residual risk:** the watermark advances after a window is stored whether or
not Tally returned everything. Given the silent-degradation behaviour, that
is a real hole, and `reconcile()` is the only thing that closes it.

### `archive()` — self-verifying

Tally's silent degradation means a byte count or an absence of exceptions
proves nothing. So the archiver gets an **independent second opinion**:

```python
def expected_per_day(timeout=900):
    """Authoritative voucher count per date, from the cheap metadata sweep."""
    counts = {}
    for r in t.vouchers(since=0, detail=False, timeout=timeout):
        d = (r.get("DATE") or "").strip()
        if d: counts[d] = counts.get(d, 0) + 1
    return counts
```

A day is listed as done **only if `vouchers = expected`**. The resume query
is literally that comparison:

```python
done = {r["vdate"] for r in conn.execute(
    "SELECT vdate FROM archived_day WHERE vouchers = expected")}
```

A short day therefore isn't "done" and is re-fetched next run, with no human
needed to read an alert and decide. Each day commits atomically, so an
interruption costs one day and never a gap.

`expected` is a parameter, because building it costs a ~47 s sweep and
`archive()` may be called repeatedly in one run.

### `reconcile()` — the only deletion detector

```python
live = {r["GUID"] for r in t.vouchers(since=0, detail=False, timeout=900)
        if r.get("GUID") and (r.get("DATE") or "") >= cutoff}
ours = {r["guid"] for r in conn.execute(
    "SELECT DISTINCT guid FROM voucher WHERE vdate >= ?", (cutoff,))}
gone    = (ours - live) - known_gone      # deleted in Tally
missing = live - ours - known_gone        # sync left a gap -> WARNING
back    = known_gone & live               # restored, or re-entered
```

Reports gaps in both directions. A voucher that reappears is un-deleted.

**Important limitation:** a *cancelled* voucher still exists in Tally, so
`reconcile` will never flag it. Only the `IsCancelled` / `IsOptional` fields
distinguish it from a live sale — which is why they are in `VOUCHER_FIELDS`.

### Master field specs

Probed, not guessed. `MASTER_SPECS` maps each type to `(fields, walks)`.
Corrections found by probing:

- `GSTHSNCode` ✓ / `HSNCode` ✗
- `Address` is a **field** yielding `ADDRESS.LIST`; as a `WALK` it silently
  returns nothing
- invalid on Collections: `PartyGSTIN`, `GSTIN`, `Email`, `LedgerMobile`,
  `LedgerContact`, `LedgerPhone`, `CreditPeriod`, `BillCreditPeriod`,
  `PriceLevel`, `MinOrderQty`, `MRPRate`, `Description`, `Narration`

Ledger and Group **always** come from `all_accounts()` (520 fields), never
the named list (14). This is not just richness: if an edited ledger were
stored from the narrow Collection, its new `(guid, alterid)` row would become
`current` and the ledger would **appear to lose its GSTIN and address** —
`current()` takes `MAX(alterid)` and `INSERT OR IGNORE` cannot later enrich
that same version. On an incremental pass the cheap Collection is used purely
to *detect* change (~8 ms); the 15.5 MB export is paid for only when
something actually changed. The other four types use Collections.

---

## 6. Layer 3 — `tally_agent.py`

Runs unattended. **No AI, no heuristics at runtime** — every decision is a
clock comparison or an equality check.

### Cadence

This laptop is on roughly **11:00–17:30**, sometimes again **20:00–21:00**.
There is no overnight idle window, so a single 01:00–05:00 maintenance slot
would never fire and every heavy job would reach its overdue escape hatch and
run in the middle of the working day — the exact outcome the window exists to
prevent. Hence three bands:

```python
CORE  = (11, 17)    # staff billing in Tally. Heavy work NEVER runs here.
MAINT = (20, 24)    # evening session: preferred slot for heavy work.
LATE  = (17, 24)    # after the working day: acceptable once overdue.
STALE_DAYS = 7      # past this, integrity beats convenience.
TICK  = 15

JOBS = {                   # name: (interval seconds, heavy)
    "vouchers":  (60,    False),
    "stock":     (300,   False),
    "masters":   (900,   False),
    "archive":   (86400, True),
    "reconcile": (86400, True),
}
```

### The scheduling predicate

```python
def due(job, last, now):
    interval, heavy = JOBS[job]
    if last is None:                      return True
    age = (now - last).total_seconds()
    if age < interval:                    return False
    if not heavy:                         return True
    if age >= STALE_DAYS * 86400:         return True    # integrity wins
    if in_band(now, CORE):                return False   # never while billing
    return in_band(now, MAINT) or (age >= 2 * interval
                                   and in_band(now, LATE))
```

**Being overdue widens the window; it never removes it.** An unbounded escape
hatch would put an 11-minute archive in front of someone mid-invoice. The
only override is the 7-day staleness cap, where data integrity outranks
convenience.

### `stock` — polled, not synced

**A sale does not bump the StockItem's AlterID.** Measured: of 210 items sold
in the last 7 days, only **21%** sit in the top decile of master AlterID,
against ~100% if sales touched the master (median AlterID 10,758 for
recently-sold vs 10,019 for never-sold — a mild correlation from price edits
on active lines, nowhere near sale-driven bumping).

So the AlterID-driven masters sync would serve a stale `ClosingBalance`
indefinitely and a website trusting it would **oversell**. Stock is therefore
polled unconditionally every 5 minutes into `stock_snapshot`, with
`observed_at` recorded so staleness is visible rather than assumed. 625 items
cost ~0.5 s, affordable even during CORE hours. `prune_snapshots()` keeps one
day of rounds.

### First-run deferral

A never-run heavy job is "due", so installing at 14:00 would have held Tally
for 11 minutes with the operator's screen frozen. Instead:

```python
if heavy and last is None and not is_night(now):
    # Backdate by one interval so it is already due by age: the only thing
    # holding it is the night gate, so it fires at the next window (tonight)
    # rather than a full interval later.
    _mark(conn, job, now - dt.timedelta(seconds=JOBS[job][0]))
    continue
```

Backdating matters: marking it as *just run* would delay the first archive
~35 hours (24 h to come due, then the next night gate). Backdated, it fires
at the next window, ~11 h after install. The light sync still starts
immediately, so data is current within a minute.

### Single-instance lock

```python
self.fh = open(self.path, "a+")
self.fh.seek(0)                     # <-- load-bearing
msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)
```

`msvcrt.locking` locks a range at the **current file position**, and append
mode leaves that at EOF — so without the `seek(0)` two instances lock
*different bytes*, both "succeed", and the second dies on a `PermissionError`
from its first write instead of being refused cleanly.

An OS byte-range lock, not a pid file: Windows releases it when the process
dies, so a crash or a kill cannot leave a stale lock that blocks every later
run. Nothing is written to the file — the lock is the mechanism, the pid goes
to the log.

### Failure policy

```python
except t.TallyOffline as e:  return False   # not marked done -> retry next tick
except t.TallyBusy as e:     return False   # ditto; never retry harder
except Exception:                           # unexpected
    log.error("%s FAILED\n%s", job, traceback.format_exc())
    return True                             # marked done -> retry on interval
```

Transient failures leave the last-run time untouched so the next tick retries.
An unexpected exception *is* marked done, deliberately: a persistent bug then
retries on its normal interval instead of hammering every 15 seconds.
Last-run times live in `sync_state`, so a reboot resumes the cadence.

Nothing propagates out of `tick()` — the loop cannot die.

### CLI

```
python tally_agent.py                # run forever
python tally_agent.py --once         # one pass, exit
python tally_agent.py --status       # schedule + what is due
python tally_agent.py --print-task   # schtasks line for logon start
python tally_agent.py --self-check   # tests
```

Logs rotate at 2 MB × 3 into `tally_agent.log`.

---

## 7. Measured performance

| Operation | Cost |
|---|---|
| Watermark read (idle poll) | **7 ms** |
| Idle `sync()` | 20 ms median (7–32) |
| Idle `sync_masters()` | 8 ms |
| `sync()` catching up 13 vouchers | ~4 s (fixed `WALK` overhead) |
| `backfill()` 10,657 vouchers | **63 s** |
| `sync_masters(full=True)` 1,433 masters | 7–9 s |
| `expected_per_day()` sweep | ~47 s |
| `archive()` per day | ~2 s |
| `archive()` full 185 days | **593 s** |
| `reconcile()` + `reconcile_masters()` | ~72 s |
| **Full cold build** | **~11 min** |

### Storage

| | Per voucher | 10,657 vouchers |
|---|---|---|
| `voucher` (42 fields, gzip per row) | 783 B | 8.4 MB |
| `voucher_raw` (783 fields, gzip per row) | 7.4 KB | 77.3 MB |
| Database total | | **108 MB** |

Per-row gzip costs ~4.7× more than gzipping a batch (649 B vs 139 B/voucher
measured) because it cannot share a dictionary across rows. Kept per-row so
any single voucher is independently readable; 108 MB is not worth optimising.

Projection for a large trader at 360,000 vouchers / 6 months: backfill ~36
min, archive ~2 h, ~2.6 GB. Linear, no algorithm change.

---

## 8. What the data actually contains

Findings about this company, as distinct from the software.

### Usable now

- **625 stock items**, 340 with non-zero stock
- **495 of 625 have `StandardPrice`** (e.g. `66.00/PCS`)
- **Unit conversions present**: `AdditionalUnits`, `Conversion`,
  `Denominator` — the carton/box/piece requirement already exists in Tally
- **606 customers / 59 suppliers**, across **19 groups** (18 route-wise
  sub-groups + 63 filed directly under `Sundry Debtors`)
- **10,657 vouchers**: Sales 4,402 · Receipt 3,827 · Payment 1,757 ·
  Purchase 604 · Journal 67
- Receivables are spread across 432 of the 605 customers, and payables are
  of a similar order. The actual figures are deliberately not recorded in
  version control -- they are the company's financial position, and nothing
  in this document needs them to make its point.

### Absent — blocks later phases

| Missing | Evidence | Blocks |
|---|---|---|
| **GST entirely** | 733/734 ledgers blank `GSTRegistrationType`, 1 "Unregistered"; 0 GSTINs; `GSTHSNCode = "Not Found"` on all 625 items; company returns no GST registration | Phase 5 invoicing |
| **Credit limits / periods** | `CreditLimit`, `BillCreditPeriod` exist as tags, **0 non-empty** of 734 | Phase 4 credit control |
| **Batch / expiry** | `IsBatchWiseOn`, `HasMFGDate`, `IsPerishableOn` = `No` on all 625 | FMCG expiry tracking |
| **Godown-wise stock** | 1 godown; `GODOWNNAME` unreachable from Collections | multi-location stock |
| **Contact details** | `Email`, `LedgerMobile`, `LedgerPhone` — 0 non-empty | customer comms |
| **Addresses** | 6 of 734 have one | delivery |

Note the method: these were established with `all_accounts()`, where the tag
*exists* and can be counted empty. A named-field fetch cannot distinguish
"wrong name" from "no data" — which is exactly how an earlier GSTIN
conclusion went wrong twice.

### Location is encoded in names, not address fields

489 of 606 customers (80%) carry a trailing `(place)` or `[place]`, but as
free text with casing variants (`Dewas` 35 **and** `DEWAS` 20; `Sanwer` 11
**and** `SANWER` 11). The **route group is the structured truth** and covers
all but 63.

### Party role is per-transaction, not per-party

12 parties appear in both Sales and Purchase vouchers. Two rejected rules:

- **Payment direction** fails on 23 parties — 18 pure customers we *paid*
  (refunds/claims) and 5 pure suppliers who *paid us*. Receipt and Payment
  are settlements, not sales.
- **Inventory direction** fails on returns: a Credit Note moves stock *in*
  from a customer, a Debit Note moves it *out* to a supplier. Both voucher
  types **are defined** in this company (all 24 standard types), so returns
  will occur.

**The correct discriminator is `VCHTYPE`** — already on every voucher.

Also: `Cash In Hand`, `Sandeep Bank Acc BOI` and `Unknown Sale` appear in
`PartyLedgerName` on cash sales. They are ledgers, not customers, and must be
filtered out of any customer list.

---

## 9. Verification

Each self-check asserts against live data. Notable invariants:

**`tally_connector.py`**
- parsers round-trip, incl. the `&#4;` sanitiser
- `TallyOffline` vs `TallyBusy` distinguishable — tested with a dead port and
  with a socket that accepts and never answers
- customer filter returns ledgers whose parent is a *sub-group* (guards
  against reverting to `$Parent =`)
- customer and supplier GUID sets are disjoint
- AlterID window stays inside its bounds; `WALK` returns line items

**`tally_store.py`**
- `save()` idempotent; an edit adds a version rather than overwriting
- `current()` = `MAX(alterid)`; `history()` keeps both
- cutoff and identity-less rows skipped
- resume fetches only the tail, not a restart
- `sync()` straight after a backfill is a no-op
- reconcile catches a planted phantom and does not re-report it
- archive: 3 days verified, resume skips them, a day with a doctored short
  count is re-fetched **and re-verified**
- ledger records carry >100 fields (guards the `all_accounts()` path)
- `IsCancelled`, `IsOptional`, `UpdatedDateTime`, `PartyName`,
  `EffectiveDate` present on stored vouchers

**`tally_agent.py`**
- interval, night-window and overdue-fallback arithmetic
- second process genuinely refused; lock released afterwards
- an offline job is **not** marked done
- live tick runs `vouchers` + `masters`
- first-run heavy jobs deferred during business hours, due at the next night
  window, and caught by the overdue fallback if the machine is never on then

---

## 10a. Review findings deliberately NOT implemented

From `TallyPrime_Architecture_Review.pdf`, with reasons:

| Recommendation | Why not |
|---|---|
| Circuit breaker for busy-Tally retries | The tick loop *is* the outer breaker: `_retry` makes 4 bounded attempts, then the job is skipped and retried next tick. A breaker adds state and a reset policy for no behavioural gain. |
| Separate success / failure / last-attempt columns per job | Better hygiene, but the current policy is deliberate and bounded (unexpected exception → marked done → retried on interval). Deferred until a real recurring failure needs diagnosing. |
| GUID-based master identity to survive renames | Already the design — `master(name=…)` is a convenience lookup. Documented; not worth more machinery. |
| Stock snapshot every 60 s | 300 s instead. 625 items cost ~0.5 s, and Tally is single-threaded and shared with the accountant. 78 snapshots per working day is ample for FMCG ordering at ~0.2% Tally occupancy. Configurable. |
| Monitoring dashboard, backup/retention policy | Correct, but there is no cloud side yet to surface them. Phase 4+. |
| Three-stock-quantity model; reservation handoff | Correct and recorded as a constraint, but reservations live in a cloud DB that does not exist. Phase 4. |
| Invoice dedup / read-back / unknown-result states | Correct. `Reference` is already carried for exactly this. Phase 5. |

The review's own caveat is worth repeating: its findings were "reproduced
using mocked Tally responses" and "those tests were not rerun while preparing
this PDF". Each P0 above was therefore re-verified here against the real
instance or by reading the current code — not accepted as reported. The stock
AlterID claim in particular was tested statistically, because the first
cheap check (`max(AlterID) near AltMstId`) gave a false pass.

## 10. Known gaps / deferred

Marked in code with `ponytail:` comments.

1. **`reconcile()` sweeps every voucher** and filters by date in Python
   (~47 s at 10k). At 360k it is ~25 min — fine overnight, but the fix is to
   filter on `$Date` inside the TDL expression. Not done because that
   expression is unverified and five TDL guesses have already failed.
2. **Batch / MFG-date / godown-wise stock unreachable and unverifiable** on
   this company. A batch-tracking client needs this re-probed before Phase 3
   promises expiry or godown availability. **Do not assume
   `BatchAllocations` is the right walk name.**
3. **`sync_masters` has no deletion detection inside the sync** — it relies
   on `reconcile_masters()` running separately.
4. **No cloud push** (Phase 2 Steps 8–9) — deferred by decision.
5. **No write path to Tally** (Phase 5). `Import` is unused. `Reference` is
   carried specifically so a website order id can be matched before invoicing
   to prevent duplicates.
6. **JSON export unreachable.** TallyPrime 7.0 reportedly supports it;
   `$$SysName:JSON` silently returns XML. Needs a different request shape.
   Low priority — XML works and is sanitised.

---

## 11. Phase status against the original plan

| Phase | Steps | Status |
|---|---|---|
| 1 — Connect to Tally | 1–5 | ✅ complete |
| 2 — Extract & sync | 6–7, 10–11 | ✅ complete |
| 2 — Cloud backend/push | 8–9 | ⏸ deferred by decision |
| 3 — Website & catalogue | 12–16 | ⬜ not started |
| 4 — Order management | 17–21 | ⬜ not started |
| 5 — Invoice write-back | 22–28 | ⬜ not started — **blocked by absent GST config** |
| 6 — Reliability | 29, 31, 33 | ✅ done early |
| 6 — Permissions/audit/backup | 30, 32, 34 | ⬜ partial (30 via resumable state) |

The mechanics are solid and measured. The **business-rule** layers in Phases
3–5 have no real data to be designed against: no GST, no credit limits, no
batches, no godowns. That is the next real blocker, and it is a data problem
rather than a code problem.

## 12. Layer 4 — the website (`api.py`, `cloud_db.py`)

`cloud_db.py` holds the Postgres schema and `connect()`. It imports nothing
local, so `api.py` can run in the cloud without the SQLite archive, Tally,
or any of the laptop modules — and the schema has exactly one home.

`api.py` is a FastAPI app serving both the JSON API and the prototype's
static files from one origin (no CORS, and the session cookie is
`httponly` + `samesite=lax`). It enforces three things:

1. **The allowlist is re-checked on every request**, not just at sign-in.
   Deactivating `app_user.active` cuts a salesperson off mid-session even
   though their cookie is valid for another 30 days. Revocation also
   *clears* the session, so a cookie once seen on a revoked account stays
   dead after re-enabling.
2. **Route scoping happens in SQL.** `_scope()` returns `AND FALSE` for a
   salesperson with no routes — never an empty predicate, which would
   silently widen to all 605 parties. Admin gets `routes=None`, meaning all;
   `None` and `[]` are opposites and callers must not conflate them.
3. **Money is recomputed server-side.** The phone sends qty and the rate it
   quoted (reps legitimately negotiate, so the rate is an input); amounts
   and the order total are calculated from those.

Google only establishes *who* someone is. `app_user` decides whether they
may use the app — anyone with a Gmail account can present a valid token.

`DEV_LOGIN` is a password-free sign-in for local work. It is disabled
automatically whenever `GOOGLE_CLIENT_ID` is set, so configuring real auth
cannot leave the bypass open behind it.

`test_api.py` asserts all of the above against the live database (36
checks) and deletes the rows it writes. These are the reason scoping can be
trusted: a break here leaks another rep's shops and balances, and no amount
of correct-looking UI would show it.

### The order round trip

    phone -> POST /api/orders -> cloud_order
          -> tally_cloud.pull_orders  -> shop_order        (laptop)
          -> tally_invoice.post       -> Tally voucher
          -> tally_cloud.push_confirmations -> cloud_order.tally_vchno
          -> GET /api/orders          -> "Invoiced · 4405"

`push_confirmations` is the leg that was missing: without it an order shows
as "placed" on the phone forever even after it is billed, so the rep
re-asks the office or places it twice. The laptop is the only writer of
`tally_guid`/`tally_vchno`/`status` once an order is placed, so there is no
conflict to resolve.

## 13. Writing to Tally — what is settled and what is not

**Settled: the voucher XML shape.** In an Item Invoice
(`OBJVIEW="Invoice Voucher View"`) the party line is `LEDGERENTRIES.LIST`
and comes *after* the inventory entries. It is **not**
`ALLLEDGERENTRIES.LIST` — that belongs to Accounting Voucher View, and
pairing it with an invoice view makes Tally answer `EXCEPTIONS=1` with no
message at all. Confirmed by diffing against Tally's own export
(`Sales_4397.xml`), which remains in the repo as the reference. Voucher
4405 was created and read back with both lines, correct rates and the
party balance, and the `REFERENCE` dedup guard was proven on the
lost-reply path (local record cleared, re-run found the existing voucher
instead of creating a second).

**Tally stops accepting voucher imports after a while. A restart clears
it.** Some hours after 4405 was created, every voucher import began
failing with:

    Voucher date is missing for: 'Sales' voucher .
    Verify the data, resolve errors (if any) and retry Split.

The message is misleading — `<DATE>` is present and correct. Established
by elimination:

* the payload that *created* 4405 now fails unchanged;
* **Tally refuses its own export file**, unmodified;
* master imports still succeed (create + delete + read-back, clean);
* the ledgers and stock items involved are configured identically to ones
  that worked, and the company period (1-Apr-26 … 13-Oct-26) contains the
  date;
* `SVCURRENTDATE`, and `SVFROMDATE`/`SVTODATE`, change nothing;
* `tallyerr.log` records nothing for these attempts;
* the process is healthy — no error dialog, no wedge.

So this is not the XML. It is a state TallyPrime gets into where the
voucher import path closes while master import stays open.

**Restarting TallyPrime clears it**, confirmed: immediately after a restart
the same unchanged payload created voucher 4406 (AltVchId 28584 -> 28585,
both lines and the party balance correct). This is the fourth distinct way
this Tally has degraded under sustained API use -- after the Day Book
freeze, the `Incorrect Object Type!` crash and the stale process squatting
on :9000 -- and the pattern is the same each time: Tally does not fail
loudly, it quietly stops doing one thing.

Consequences for the design:

* **Invoicing stays a manual step.** `tally_invoice.py` is deliberately not
  in `tally_agent`'s job table. An unattended job that silently stops
  billing is worse than one that was never automated: the orders pile up in
  `shop_order` looking placed, and nobody finds out until a shop calls.
* Whatever eventually automates it must **treat `EXCEPTIONS=1` as "Tally
  needs restarting", not as "this order is bad"** -- and must never retry
  the same payload in a loop, because the payload is not the problem.
* `post()` already refuses to claim success on anything but
  `CREATED=1, ERRORS=0, EXCEPTIONS=0`, and re-reads the voucher back by
  `REFERENCE` before recording it. That is what kept this failure visible
  instead of silently marking orders invoiced.

### Billing the queue: `tally_invoice.py --all`

    python tally_invoice.py --all              # dry run: what would be billed
    python tally_invoice.py --all --commit     # actually bill it

Invoices every order with `status` in `OPEN_STATUSES` and no voucher yet,
oldest first. Exit code 1 on any refusal, so it can be watched by something
other than a person reading the output.

**It stops at the first refusal**, and says which of the two refusals it
was. That distinction decides what the operator does next, so it is
derived from Tally's own counters rather than sniffed from the message
text (`InvoiceError.is_tally_stuck`):

* refusal with **no message**, or the misleading `date is missing` — Tally
  has closed the voucher import path and *every* order will fail. Restart
  TallyPrime. Nothing needs re-keying.
* refusal **naming a problem** (`Ledger 'X' does not exist!`) — that one
  order is wrong; the rest are untouched.

Continuing past the first failure would turn one restart into sixty
identical errors with the real message buried in the middle.

**Two Tally round-trips per batch, not two per order.** A collection query
costs ~5 s regardless of what it returns, and Tally is single-threaded —
while it answers, the billing desk's own screen is frozen. So the batch
asks once for every reference up front, sends the vouchers, then asks once
more to read back the numbers Tally assigned. Measured: **12 orders in
18 s**, against ~145 s for the per-order version. At the observed median of
27 sales a day that is ~25 s, and ~35 s on the busiest day on record (48).

Re-running is always safe. Orders already in Tally are found by `REFERENCE`
and recorded rather than resent, so a batch interrupted half way is fixed
by running it again. An order Tally claims to have created but which cannot
be read back is reported loudly and left **un-recorded** — marking an order
invoiced when the voucher cannot be found is the one outcome worth
stopping over.

## 14. A shop the rep found: `prospect`

A salesperson standing in a shop Tally has never heard of cannot wait for
head office. So a **prospect** is a real thing orders can be placed
against, with its own id (`NEW-...`, which can never collide with a Tally
guid), and the order is taken on the spot.

    rep proposes      POST /api/prospects         -> prospect, status pending
    rep orders        POST /api/orders            -> cloud_order, 'pending_party'
    office approves   POST /api/prospects/{}/decide
    laptop            create_approved_parties()   -> Tally ledger, read back
                                                  -> orders released to 'placed'
    laptop            tally_invoice.py --all      -> voucher

Four things this shape exists to get right:

**The order must not reach the invoice batch.** `pending_party` is
deliberately absent from `OPEN_STATUSES`, so `tally_invoice.pending()`
cannot see it. Billing a party with no ledger fails with "Ledger does not
exist" and, because the batch stops at the first refusal, would halt every
other order behind it.

**Approval does not create anything.** The office says yes; the *laptop*
creates the ledger, because only the laptop can reach Tally and only it can
confirm the ledger exists. The ledger is created, **read back**, and only
then are the orders released. Releasing on the strength of the import reply
alone would queue invoices against a ledger that might not be there.

**Route is not cosmetic.** Routes ARE Tally ledger groups -- `customer.route`
is the ledger's parent -- so the approved route is the group the ledger is
created under. Get it wrong and the shop is invisible to the rep who found
it, because scoping is by route. A salesperson may only propose on a route
they already hold; with more than one they have to choose.

**Duplicates are forever.** A second ledger for one shop splits its balance
and history permanently. Proposing runs a normalised comparison against
every existing customer and open prospect: an exact match is refused
outright, and a near match (ratio >= 0.82, or one name containing the
other) comes back as 409 with the candidates, so the phone can ask "did you
mean?" rather than silently creating the duplicate. The rep can override,
but only deliberately.

Statuses the rep sees are kept honest end to end: the confirmation screen
says "Order **saved**", not "Order placed", and carries the server's own
sentence about why no bill exists yet. `test_newparty.py` walks the whole
journey against the live cloud and the live Tally (36 checks) and removes
its own ledger afterwards, so it can be run again.

### Performance: connections are pooled, responses are gzipped

Opening a Postgres connection to Railway's public proxy costs **~11 s** --
`SELECT 1` alone measures 282 ms of round-trip, and a connection is TLS plus
auth on top. With a connection per request, `/api/data` took **36 s** for an
admin while the four queries inside it added up to 7 s.

`api.py` therefore uses a `psycopg_pool.ConnectionPool` (`min_size=1`, so the
first caller of the day does not pay the handshake either). Measured after:

| | before | after |
|---|---|---|
| `/api/data`, admin | 36.4 s | 6.4 s |
| `/api/data`, rep | 8.0 s | 4.1 s |
| `/api/me` | ~11 s | 0.6 s |

GZip middleware then takes the admin payload from 1,792 KB to **169 KB** on
the wire, and a rep's from 332 KB to **33 KB** -- about tenfold, which
matters far more on a phone with one bar than on office wifi.

What remains is almost entirely proxy latency: six queries at 282 ms each.
On Railway the API talks to Postgres over internal networking, where that
round-trip is sub-millisecond, so this should largely disappear -- though
that is a prediction from the measured round-trip, not yet a measurement of
the deployed service.

### Who may sign in: the People screen

The allowlist used to be a command on the laptop
(`tally_cloud.py --add`). That is fine for a developer and wrong for the
person running the business, so it is now a screen: **People**, owner only.

The demo's own User management module is *withheld* in live mode and
dropped from the owner's template. It edits a store in the browser, so
live it would be worse than absent -- the owner would add a salesperson,
watch them appear in the list, and that person would not be able to sign
in.

Each row shows the routes a rep holds and **how many shops that actually
resolves to**, with a warning when it is zero. A route that does not match
`customer.route` exactly produces a rep who can see nothing, and that is
otherwise invisible until they call to say the app is empty. For the same
reason the route picker is built from the routes that exist, with their
shop counts, rather than a free-text field.

Lock-out is the failure that has no recovery path without a developer and
a terminal, so four things are refused:

* switching off your own access
* removing yourself
* demoting yourself from owner to salesperson
* switching off or removing the **last active owner**, by anyone

Switching someone off keeps their history -- `cloud_order.salesperson` is
the record of who sold what and must survive the person leaving -- and
takes effect on their very next request, because the allowlist is checked
every time rather than only at sign-in.

`test_people.py` covers all of it against the live database (29 checks):
the role boundary, route validation, address normalisation (` ZZ.Claude@
Example.com ` and `zz.claude@example.com` must be the same person, or
sign-in silently fails to match), every lock-out guard, and that a
newly-added address can immediately sign in and sees only its own routes.
It removes everyone it adds.

### Running the agent unattended

    python tally_agent.py --install-task     # start at every logon
    python tally_agent.py --task-status      # registered? last run? result?
    python tally_agent.py --remove-task

Registered from an XML definition rather than a one-line `schtasks`
command, because Task Scheduler's defaults are wrong for something that
must simply stay up:

| Default | Why it is wrong here | Set to |
|---|---|---|
| `ExecutionTimeLimit` 3 days | Windows **kills** the agent after 72h | `PT0S` |
| console interpreter | a window pops up at every logon | `pythonw.exe` |
| stop on battery | sync dies when the laptop leaves the desk | off |
| stop on idle end | sync dies when someone touches the mouse | off |
| `Parallel` instances | two agents racing the same archive | `IgnoreNew` |

**`RestartOnFailure` does not do what its name suggests.** It is set, but
it was measured and does not cover a killed process: the process was
force-killed, the task went to `Ready`, and it was still dead 150 seconds
later. What actually keeps the agent up is a second trigger that fires
every five minutes forever. While the agent is alive `IgnoreNew` makes it
a no-op; when the agent is gone it starts a new one. Measured: killed,
back on its own after **291 s**. The agent's `msvcrt` file lock is the
second guard against two copies.

`install_task` parses the XML before handing it to Windows, because
Windows answers a malformed definition with `(18,36)::ERROR: incorrect
comment syntax` and nothing else -- that one was an XML comment
containing `--`, which is illegal and not an obvious rule.

**What this does and does not automate.** Vouchers, stock, balances,
masters, rates, the cloud push, order pull and invoice confirmations all
run unattended. **Invoicing does not** -- see section 13. The agent brings
orders down and marks them ready; a person still runs
`tally_invoice.py --all --commit`.

The task runs as the logged-in user with `LeastPrivilege`, needs no
administrator rights, and stops when that user logs out -- which is
correct, because it needs TallyPrime, and Tally is a desktop application
that is only running when someone is logged in.

### Billing without a person: the `invoice` job

Section 13 said invoicing stays manual until three things existed. They
now do.

**1. A refusal is classified, not retried blindly.** `post_all` already
told the two apart; the agent now acts on the difference, and the
difference matters in opposite directions:

* *Tally refusing everything* — the run stops at the first order and
  **nothing is counted against any order**. Treating this as the orders'
  fault would burn every attempt in the queue and quarantine a whole
  day's billing over one bad afternoon.
* *This order's own problem* (a ledger deleted since, an item renamed) —
  the attempt is counted and the run **continues**. Treating this as a
  stuck Tally would park one unbillable order at the head of the queue
  and block everything behind it for ever.

After `INVOICE_MAX_TRIES` (3) an order is set aside rather than retried
until the end of time, and reported as needing a person.

**2. It stops, and backs off.** A Tally that refused everything needs a
human to restart it; retrying every ten minutes until then just buries
the one message that matters. The first refusal is timestamped and
nothing is attempted for `INVOICE_RETRY_MINS` (30).

**3. A stall is visible.** This is the real problem: a stalled invoice run
has no symptom of its own. Orders sit saying "At the office", which is
exactly what they say when everything is fine, so the first sign is a
shop telephoning. `push_fast` now carries `orders_waiting`,
`oldest_wait_mins`, `orders_set_aside`, `invoice_blocked_since` and
`invoice_error` to `sync_status`, the API serves them, and the owner's
home screen shows a banner. A rep never sees it -- they can do nothing
about it and would only worry.

The job is **heavy**, so it never runs 11:00-17:00: it writes to the books
and freezes Tally for ~25s at a day's volume. In practice bills are raised
from 17:00 and overnight, which is the rhythm the manual command had
anyway.

Measured end to end, nobody touching it:

    05:48:28  pull: 1 new order(s)
    05:49-56  push fast: ... 1 order(s) awaiting billing
    05:56:47  1 order(s) to invoice
    05:56:53    WEB-1015751947  AASHIQ HUSAIN KIRANA  Rs 110.00  sent
    05:56:59    vouchers 4424
    05:57:02  confirm: 1 order(s) marked invoiced in the cloud

`test_invoice_auto.py` (21 checks) injects both refusals against a scratch
database and asserts the classification, the quarantine, and that a stuck
Tally costs no order its attempts. It never contacts Tally and never
touches a real order.
