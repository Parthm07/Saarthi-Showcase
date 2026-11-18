"""Minimal TallyPrime connector: XML over HTTP to localhost:9000.

Tally must be open with a company loaded, and connectivity enabled
(F1 > Settings > Connectivity). Stdlib only -- no pip install needed.

Run `python tally_connector.py` for a self-check.
"""
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

# Port 9000 is the default but `ServerPort` in tally.ini is configurable, and
# Tally may sit on another machine, so this has to be overridable at deploy
# time. Resolved per call, not captured as a default argument -- a default
# binds at def time and would make the setting permanently unchangeable.
URL = os.environ.get("TALLY_URL", "http://localhost:9000")


class TallyError(Exception):
    """Base for every way talking to Tally can fail."""


class TallyOffline(TallyError):
    """Nothing is listening: Tally is closed, or on another port."""


class TallyBusy(TallyError):
    """Tally accepted the connection but did not answer in time.

    Three different causes, indistinguishable over the wire:

      1. A request is genuinely in flight. Tally is single-threaded with no
         way to cancel, so one oversized query makes every later one time
         out -- and freezes the operator's UI with it.
      2. No company is loaded.
      3. The UI is parked somewhere that does not service the HTTP server --
         a modal dialog, or the company-select screen. Observed: the process
         sits at flat CPU, 146 MB and Responding=True, yet answers nothing,
         not even a plain GET.

    Only (1) resolves on its own. (2) and (3) need a person, so an agent
    that logs "busy, working" and backs off forever is lying. Never retry
    harder and never widen the request; after a few attempts, say out loud
    that someone should look at the Tally window.
    """


# Tally emits illegal numeric character references in master data -- &#4;
# shows up ~6000 times in a full master export, as a wildcard marker on
# names like "&#4; Any". ElementTree rejects the whole document over one of
# them, so strip anything outside the XML 1.0 legal set before parsing.
_CHARREF = re.compile(r"&#(x[0-9a-fA-F]+|[0-9]+);")


def _legal(code):
    return (code in (0x9, 0xA, 0xD)
            or 0x20 <= code <= 0xD7FF
            or 0xE000 <= code <= 0xFFFD
            or 0x10000 <= code <= 0x10FFFF)


def _strip_bad_refs(text):
    def keep(m):
        raw = m.group(1)
        code = int(raw[1:], 16) if raw[0] in "xX" else int(raw)
        return m.group(0) if _legal(code) else ""
    return _CHARREF.sub(keep, text)


def _port_accepts(url, timeout=3):
    """True if a TCP connection to the url's host:port completes."""
    p = urllib.parse.urlsplit(url)
    try:
        with socket.create_connection((p.hostname, p.port or 80), timeout):
            return True
    except OSError:
        return False


def tally(xml, url=None, timeout=60):
    """POST a Tally XML envelope, return the parsed response root."""
    url = url or URL          # resolved now, so URL stays reconfigurable
    req = urllib.request.Request(
        url, xml.encode("utf-8"), {"Content-Type": "text/xml"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", e)
        if isinstance(reason, (TimeoutError, socket.timeout)):
            # A timeout alone does not say which: a *connect* timeout means
            # nothing is there, a *read* timeout means Tally took the request
            # and is still chewing on it. urlopen does not distinguish, so
            # ask the socket -- a wedged Tally still completes a TCP
            # handshake while its UI is frozen.
            if _port_accepts(url):
                raise TallyBusy(
                    f"{url} accepted the connection but sent nothing in"
                    f" {timeout}s -- a request is in flight, OR no company"
                    f" is loaded, OR the Tally window is sitting on a"
                    f" dialog") from e
            raise TallyOffline(f"{url} unreachable: {e}") from e
        raise TallyOffline(f"{url} unreachable: {e}") from e
    # Tally's only error message, and it means malformed request XML -- not a
    # connection problem. Surface it loudly or you will chase ghosts.
    if "<RESPONSE>" in body and "cannot be processed" in body:
        raise ValueError("Tally rejected the request XML (check your tags)")
    return ET.fromstring(_strip_bad_refs(body))


# Object types verified to exist on TallyPrime 7.0. Everything in here has
# been queried successfully against a live company.
#
# This list is a GUARD, not documentation. Tally's three kinds of mistake are
# not equally safe:
#
#   wrong FIELD name      -> silently dropped. Harmless.
#   wrong FILTER formula  -> zero rows, no error. Harmless.
#   wrong OBJECT TYPE     -> "Incorrect Object Type!" internal error, which
#                            took the company down and left the process
#                            squatting on port 9000 for two days.
#
# That last one is from this project's own tallyerr.log, caused by probing
# PriceLevel and PriceList -- types that do not exist. A typo must not be
# able to crash an accountant's Tally, so an unknown type is refused here
# instead of being sent. Add a name only after confirming it by hand, and
# pass allow_unverified=True for a deliberate one-off probe.
KNOWN_TYPES = frozenset((
    "Company", "Ledger", "Group", "StockItem", "StockGroup", "StockCategory",
    "Godown", "Unit", "Voucher", "VoucherType", "Currency", "Batch",
    "CostCentre", "CostCategory", "Budget", "AttendanceType", "GSTClassification",
))


class UnknownObjectType(TallyError):
    """Refused before sending: Tally errors internally on a bad type."""


def collection(obj_type, *fields, name="Coll", where=None, walk=(),
               timeout=60, allow_unverified=False):
    """Fetch one Tally object type with just the fields asked for.

    `where` is a TDL expression, e.g. '$$IsLedOfGrp:$Name:$$GroupSundryDebtors'.
    Unknown field names are dropped silently by Tally -- no error. A filter
    expression Tally cannot evaluate returns zero rows, also without an
    error. Always eyeball the first response when changing either.

    An unknown `obj_type` is refused outright: see KNOWN_TYPES.
    """
    if obj_type not in KNOWN_TYPES and not allow_unverified:
        raise UnknownObjectType(
            f"{obj_type!r} is not a verified Tally object type. Sending an"
            f" unknown type risks an 'Incorrect Object Type!' internal error"
            f" that can take the loaded company down. Confirm it by hand,"
            f" add it to KNOWN_TYPES, or pass allow_unverified=True.")
    methods = "".join(f"<NATIVEMETHOD>{f}</NATIVEMETHOD>" for f in fields)
    # WALK pulls nested sub-collections (line items, ledger postings, batch
    # allocations). FETCH * is ignored and Object-by-GUID/MasterID does not
    # resolve a voucher, so WALK is the only way to get them from a
    # Collection. It costs ~783 B/voucher against day_book()'s ~68 KB,
    # because it omits the empty placeholder containers -- but it also
    # returns 42 fields against day_book()'s 267, so the two are
    # complementary rather than one replacing the other.
    walks = "".join(f"<WALK>{w}</WALK>" for w in walk)
    flt = "<FILTER>Flt</FILTER>" if where else ""
    # TDL comparisons contain '<' and '&', which would start a tag or an
    # entity and get the whole envelope rejected as "Unknown Request".
    sysf = (f'<SYSTEM TYPE="Formula" NAME="Flt">{escape(where)}</SYSTEM>'
            if where else "")
    root = tally(
        f'<ENVELOPE><HEADER><VERSION>1</VERSION>'
        f'<TALLYREQUEST>Export</TALLYREQUEST><TYPE>Collection</TYPE>'
        f'<ID>{name}</ID></HEADER><BODY><DESC><STATICVARIABLES>'
        f'<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT></STATICVARIABLES>'
        f'<TDL><TDLMESSAGE><COLLECTION NAME="{name}" ISMODIFY="No">'
        f'<TYPE>{obj_type}</TYPE>{methods}{walks}{flt}</COLLECTION>{sysf}'
        f'</TDLMESSAGE></TDL></DESC></BODY></ENVELOPE>', timeout=timeout)
    # Scope to <DATA>: the response header carries a <CMPINFO> block with
    # per-type counters using the same tag names (<COMPANY>0</COMPANY>),
    # which a document-wide search picks up as a phantom record.
    data = root.find(".//DATA")
    out = []
    for el in (data if data is not None else []).iter(obj_type.upper()):
        out.append(_record(el))
    return out


def _record(el):
    """Flatten one object, keeping WALKed sub-collections as nested lists."""
    rec = {}
    if el.get("NAME"):
        rec["NAME"] = el.get("NAME")
    for child in el:
        if child.tag == "LANGUAGENAME.LIST":
            continue
        if child.tag.endswith(".LIST"):
            rec.setdefault(child.tag, []).append(_record(child))
        else:
            rec[child.tag] = (child.text or "").strip()
    return rec


def qty(s):
    """'12 PCS' -> (12.0, 'PCS'). Blank -> (0.0, None).

    Tally sends quantities and rates as strings with the unit baked in, and
    uses a blank tag for both zero and not-set.
    """
    s = (s or "").strip().replace(",", "")
    if not s:
        return 0.0, None
    head, _, unit = s.partition(" ")
    try:
        return float(head), (unit.strip() or None)
    except ValueError:
        return 0.0, s


def rate(s):
    """'1.00/PCS' -> (1.0, 'PCS'). Blank -> (0.0, None)."""
    s = (s or "").strip().replace(",", "")
    if not s:
        return 0.0, None
    head, _, unit = s.partition("/")
    try:
        return float(head), (unit.strip() or None)
    except ValueError:
        return 0.0, s


def amount(s):
    """'-1,234.50' -> -1234.5. Blank -> 0.0. Tally shows Dr/Cr by sign."""
    s = (s or "").strip().replace(",", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


# Match stock and parties on GUID, never on NAME: a rename in Tally would
# otherwise create a silent duplicate product on the website.
STOCK_FIELDS = ("Name", "Parent", "BaseUnits", "ClosingBalance",
                "ClosingRate", "ClosingValue", "GUID")
# No PartyGSTIN here: it is a tag on the full Object/List-of-Accounts export
# but NOT a valid Collection field name, so asking for it here is silently
# dropped. GSTIN comes from all_accounts() instead.
PARTY_FIELDS = ("Name", "Parent", "ClosingBalance", "LedStateName", "GUID")

# Customers sit in route-wise sub-groups (Dewas Debtors, Nagda Route, ...),
# not directly under Sundry Debtors. '$Parent = "Sundry Debtors"' returns 63
# of 606 -- it drops 90% of the customer base without any error. $$IsLedOfGrp
# walks the whole sub-tree. Note it takes $Name, not $Parent: the other
# variants evaluate to zero rows, silently.
IS_CUSTOMER = "$$IsLedOfGrp:$Name:$$GroupSundryDebtors"
IS_SUPPLIER = "$$IsLedOfGrp:$Name:$$GroupSundryCreditors"

# Classify by group, never by the sign of ClosingBalance: 18 of 606 customers
# carry a positive (Cr) balance from advances, and 8 of 59 suppliers carry a
# negative one. Sign tells you who owes whom today, not what the party is.


def company():
    """Name of the company currently loaded in Tally.

    Via a Company collection: ~2 KB. Reading SVCURRENTCOMPANY off a
    'List of Accounts' export would cost 15 MB for the same string.
    """
    rows = collection("Company", "Name", "StartingFrom", name="Cmp")
    return rows[0]["NAME"] if rows else None


def stock_items():
    return collection("StockItem", *STOCK_FIELDS, name="StkList")


def parties():
    return collection("Ledger", *PARTY_FIELDS, name="LedList",
                      where=IS_CUSTOMER)


def suppliers():
    return collection("Ledger", *PARTY_FIELDS, name="SupList",
                      where=IS_SUPPLIER)


# --- incremental sync ------------------------------------------------------
# AlterID is a per-company counter that increments on every create and edit.
# It is the only safe watermark: a voucher DATED in June can be ENTERED in
# October, so a date-based "since yesterday" sync misses backdated entries
# permanently. AlterID catches them because the edit is new even when the
# date is old.
#
# What AlterID cannot see is DELETIONS -- a deleted voucher simply stops
# existing and nothing's AlterID changes. tally_store.reconcile() covers that
# by diffing Tally's live GUID set against the local one.

# Derived by diffing a full exploded export (267 populated tags) against what
# a minimal field list returned (34). Most of the 236-tag gap is dead weight
# -- VAT, Excise, CST, FBT, cash-denomination counts, IGN*CRC validation
# flags -- but these were worth adding, and all of them come back non-empty.
# Only RoundLimit was requested and silently dropped.
VOUCHER_FIELDS = (
    "Date", "EffectiveDate", "VoucherTypeName", "VoucherNumber",
    "PartyLedgerName", "Amount", "AlterID", "MasterID", "GUID",
    # Status. A cancelled or optional voucher still EXISTS in Tally, so
    # reconcile() will never flag it as deleted -- without these the archive
    # happily serves a cancelled invoice as a live sale.
    "IsCancelled", "IsOptional", "IsPostDated", "UpdatedDateTime",
    # Party and delivery identity, beyond the ledger name.
    "PartyName", "PartyMailingName", "BasicBuyerName", "StateName",
    "ConsigneeStateName",
    # Narration/Reference is where a website order id would live, which is
    # how Phase 5 avoids billing the same order twice.
    "Narration", "Reference", "ReferenceDate",
    "HasDiscounts", "IsEwayBillApplicable", "VchEntryMode")
VOUCHER_WALKS = ("AllInventoryEntries", "LedgerEntries")


def all_accounts(timeout=600):
    """Every accounting master with every field, in one request.

    522 distinct tags per ledger against 11 from a named Collection, and no
    field name to get wrong -- which is also how you can prove a field is
    genuinely empty rather than merely mis-named.

    Costs 15.5 MB / ~6s on a 734-ledger company, and cannot be filtered by
    AlterID: it is all-or-nothing. So it serves day-1 backfill and
    reconciliation, while the frequent incremental sync uses the cheap
    AlterID-filtered Collection.

    Does NOT include stock items, stock groups, godowns or units -- no
    report returns those, and a Collection asked for no fields returns only
    NAME. Those must be named explicitly.
    """
    root = tally(
        '<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>Export'
        '</TALLYREQUEST><TYPE>Data</TYPE><ID>List of Accounts</ID></HEADER>'
        '<BODY><DESC><STATICVARIABLES>'
        '<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>'
        '</STATICVARIABLES></DESC></BODY></ENVELOPE>', timeout=timeout)
    data = root.find(".//DATA")
    if data is None:
        return {}
    return {name: [_record(e) for e in data.iter(tag)]
            for name, tag in (("Ledger", "LEDGER"), ("Group", "GROUP"),
                              ("Currency", "CURRENCY"))}


def day_book(day, timeout=300):
    """Every voucher of ONE day, fully exploded: all 267 populated fields.

    SVCURRENTDATE is mandatory and is the whole trick. With only
    SVFROMDATE/SVTODATE the report silently returns *today* instead, with
    STATUS=1 and no error -- and an in-period range without SVCURRENTDATE
    sends Tally into a state that burns hours and freezes its UI.

    One day at a time: a range is not honoured even with SVCURRENTDATE, so
    the caller loops. That is also what keeps each request bounded (~9 MB on
    a 133-voucher day) and unable to wedge Tally.

    `day` is YYYYMMDD.
    """
    root = tally(
        f'<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>Export'
        f'</TALLYREQUEST><TYPE>Data</TYPE><ID>Day Book</ID></HEADER><BODY>'
        f'<DESC><STATICVARIABLES>'
        f'<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>'
        f'<SVFROMDATE TYPE="Date">{day}</SVFROMDATE>'
        f'<SVTODATE TYPE="Date">{day}</SVTODATE>'
        f'<SVCURRENTDATE TYPE="Date">{day}</SVCURRENTDATE>'
        f'<EXPLODEFLAG>Yes</EXPLODEFLAG>'
        f'</STATICVARIABLES></DESC></BODY></ENVELOPE>', timeout=timeout)
    out = []
    for v in root.iter("VOUCHER"):
        rec = _record(v)
        # The attributes carry identity the children do not repeat.
        for k, val in v.attrib.items():
            rec.setdefault("@" + k, val)
        out.append(rec)
    return out


def watermarks():
    """(master_alter_id, voucher_alter_id) -- the company's current maxima.

    Raises rather than returning (None, None) when no company is loaded.
    Tally answers that case cheerfully with an empty collection, and a
    None watermark propagated into `lo >= hi` as a TypeError that got logged
    as an unexpected failure instead of a clean skip.
    """
    c = collection("Company", "Name", "AltMstId", "AltVchId", name="Cmp")
    if not c:
        raise TallyOffline(
            "Tally answered but no company is loaded -- open the company")
    r = c[0]
    return int(r.get("ALTMSTID") or 0), int(r.get("ALTVCHID") or 0)


def vouchers(since=0, upto=None, detail=True, timeout=180):
    """Vouchers with `since` < AlterID <= `upto`, newest edits included.

    detail=True WALKs line items and ledger postings (~1.2 KB/voucher);
    detail=False returns metadata only, which is the cheap sweep used to
    decide what actually needs fetching.
    """
    if upto is None:
        upto = watermarks()[1]
    where = f"$AlterID > {since} AND $AlterID <= {upto}"
    return collection("Voucher", *VOUCHER_FIELDS, name="VSync", where=where,
                      walk=VOUCHER_WALKS if detail else (), timeout=timeout)


def _check_error_paths():
    """Offline and busy must be distinguishable -- the retry policy differs.

    Treating a busy Tally as offline is how a sync agent turns one wedged
    request into two.
    """
    import threading
    try:
        tally("<ENVELOPE/>", url="http://127.0.0.1:65123", timeout=2)
    except TallyOffline:
        pass
    else:
        raise AssertionError("dead port should raise TallyOffline")

    # A socket that accepts and never answers: exactly a wedged Tally. The
    # accepted socket must be held open -- letting it close aborts the
    # connection instead of hanging, which is a different failure.
    import time
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    held = []

    def hold():
        conn, _ = srv.accept()
        held.append(conn)
        time.sleep(30)

    threading.Thread(target=hold, daemon=True).start()
    try:
        tally("<ENVELOPE/>", url=f"http://127.0.0.1:{srv.getsockname()[1]}",
              timeout=2)
    except TallyBusy:
        pass
    else:
        raise AssertionError("silent socket should raise TallyBusy")
    finally:
        srv.close()
    print("error paths ok (offline vs busy distinguishable)")


def _self_check():
    assert qty("12 PCS") == (12.0, "PCS")
    assert qty("1,250 BOX") == (1250.0, "BOX")
    assert qty("") == (0.0, None)
    assert rate("1.00/PCS") == (1.0, "PCS")
    assert rate("") == (0.0, None)
    assert amount("-1,234.50") == -1234.5
    assert amount("") == 0.0
    assert _strip_bad_refs("<A>&#4; Any &amp; &#65;</A>") == "<A> Any &amp; &#65;</A>"
    print("parsers ok")

    # The object-type guard must refuse before anything reaches Tally. The
    # two names below are the ones that actually caused the outage.
    for bad in ("PriceLevel", "PriceList", "StockItems", "Ledgers"):
        try:
            collection(bad, "Name", name="Guard")
        except UnknownObjectType:
            pass
        else:
            raise AssertionError(f"{bad!r} was sent to Tally unguarded")
    assert "StockItem" in KNOWN_TYPES and "Voucher" in KNOWN_TYPES
    print(f"object-type guard ok ({len(KNOWN_TYPES)} verified types; "
          f"PriceLevel/PriceList refused)")
    _check_error_paths()

    try:
        name = company()
    except TallyOffline as e:
        print("Tally offline --", e)
        print("Open Tally, load the company, enable F1 > Settings >"
              " Connectivity, then rerun.")
        return
    except TallyBusy as e:
        print("Tally is BUSY, not down --", e)
        print("Something is already running inside Tally. Wait for it; do"
              " not retry, and do not send a bigger request.")
        return
    assert name, "Tally answered but no company is loaded"
    print("company:", name)

    items, pts = stock_items(), parties()
    assert items and pts, "Tally answered but returned no records"
    assert all(i["GUID"] for i in items), "stock item without a GUID"
    # Guards the filter above: customers are grouped by route, so a working
    # filter must return ledgers whose parent is a sub-group, not the
    # "Sundry Debtors" group itself.
    assert any(p.get("PARENT") != "Sundry Debtors" for p in pts), \
        "customer filter is not walking sub-groups"
    sup = suppliers()
    # Customers and suppliers must be disjoint sets of GUIDs.
    assert not ({p["GUID"] for p in pts} & {s["GUID"] for s in sup}), \
        "a ledger is classified as both customer and supplier"
    print(f"stock items: {len(items)}   customers: {len(pts)}   "
          f"suppliers: {len(sup)}")

    i = items[0]
    print(f"  {i['NAME']!r} group={i['PARENT']!r} "
          f"qty={qty(i['CLOSINGBALANCE'])} rate={rate(i['CLOSINGRATE'])}")
    p = pts[0]
    print(f"  {p['NAME']!r} state={p.get('LEDSTATENAME') or '-'} "
          f"balance={amount(p['CLOSINGBALANCE'])}")

    mst, vch = watermarks()
    assert mst and vch, "company exposes no AlterID watermarks"
    print(f"watermarks: AltMstId={mst} AltVchId={vch}")

    # The incremental window must stay inside its bounds, and detail must
    # actually bring line items back.
    lo = vch - 10
    recent = vouchers(since=lo, upto=vch)
    ids = [int(v["ALTERID"]) for v in recent if v.get("ALTERID")]
    assert ids, "AlterID window returned nothing -- filter not applied"
    assert all(lo < i <= vch for i in ids), "AlterID window leaked bounds"
    lines = sum(len(v.get("ALLINVENTORYENTRIES.LIST", [])) for v in recent)
    assert lines, "WALK returned no line items"
    print(f"incremental: {len(recent)} vouchers in AlterID({lo}..{vch}], "
          f"{lines} line items")
    print("phase 1 ok")


if __name__ == "__main__":
    _self_check()
