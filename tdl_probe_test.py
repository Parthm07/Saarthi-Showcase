"""Does a user-defined TDL Collection answer over the XML API?

Run after registering tdl_probe.txt in tally.ini and restarting Tally.
Decides, with evidence, whether the TDL route is open on this licence.

    python tdl_probe_test.py
"""
import sys

import tally_connector as t


def ask(name, obj_type, *fields):
    """Query a collection DEFINED IN TDL by name, requesting nothing extra.

    A TDL-defined collection is addressed by its own name with no inline
    <TDLMESSAGE> definition -- that is the whole point of the test.
    """
    root = t.tally(
        f'<ENVELOPE><HEADER><VERSION>1</VERSION>'
        f'<TALLYREQUEST>Export</TALLYREQUEST><TYPE>Collection</TYPE>'
        f'<ID>{name}</ID></HEADER><BODY><DESC><STATICVARIABLES>'
        f'<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>'
        f'</STATICVARIABLES></DESC></BODY></ENVELOPE>', timeout=120)
    data = root.find(".//DATA")
    rows = list(data.iter(obj_type.upper())) if data is not None else []
    return rows


def main():
    try:
        print("company:", t.company())
    except t.TallyError as e:
        print(f"Tally unreachable: {type(e).__name__}: {e}")
        return 1

    verdict = {}

    # 1. does the TDL file load at all?
    try:
        rows = ask("ClaudeProbeItems", "StockItem")
        verdict["source TDL loads"] = bool(rows)
        print(f"ClaudeProbeItems -> {len(rows)} rows")
        for el in rows[:3]:
            vals = {c.tag: (c.text or "").strip() for c in el}
            print("   ", el.get("NAME"), {k: v for k, v in vals.items()
                                         if k != "LANGUAGENAME.LIST"})
    except Exception as e:
        verdict["source TDL loads"] = False
        print(f"ClaudeProbeItems -> {type(e).__name__}: {e}")

    # 2. the prize: godown-wise / batch stock, unreachable from plain XML
    try:
        rows = ask("ClaudeProbeGodownStock", "Batch")
        got = []
        for el in rows[:5]:
            got.append({c.tag: (c.text or "").strip() for c in el})
        verdict["godown/batch reachable"] = bool(rows)
        print(f"\nClaudeProbeGodownStock -> {len(rows)} rows")
        for g in got:
            print("   ", g)
    except Exception as e:
        verdict["godown/batch reachable"] = False
        print(f"\nClaudeProbeGodownStock -> {type(e).__name__}: {e}")

    print("\n--- verdict ---")
    for k, v in verdict.items():
        print(f"   {k:<26} {'YES' if v else 'no'}")
    if verdict.get("source TDL loads"):
        print("\nPlain-text TDL works on this licence. A targeted plugin is"
              "\npossible without TallyPrime Developer.")
    else:
        print("\nNo TDL collection answered. Either the file is not"
              "\nregistered, Tally reported a load error, or this licence"
              "\nwill not load source TDL. Check Tally's screen for a TDL"
              "\nerror message, then remove the tally.ini line and restart.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
