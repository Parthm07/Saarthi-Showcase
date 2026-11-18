"""Rebuild web/ -- the copy that gets deployed -- from the prototype folder.

There are two copies of the site and that is a standing hazard, so this is
the only sanctioned way to produce the deployed one. It does two things by
hand that are easy to forget and expensive to get wrong:

  * It does NOT copy data-tally.js, and it strips the <script> tag that
    loads it. That file is the offline snapshot: 2.7 MB holding all 605
    customers and their outstanding balances. Static files are served
    before anyone signs in, so shipping it would publish the entire
    customer book -- balances included -- to any visitor who guessed the
    filename. Signed in, the browser gets its own scoped slice from
    /api/data instead, which is the whole point of the route scoping.
  * It re-stamps ?v= on every asset, because a browser that cached
    yesterday's sales-simple.js will happily keep running it.

    python sync_web.py            # show what would change
    python sync_web.py --write
"""
import argparse
import filecmp
import pathlib
import re
import shutil
import subprocess
import time

HERE = pathlib.Path(__file__).resolve().parent
SRC = pathlib.Path.home() / "Desktop" / "Saarthi Sales Copilot"
DST = HERE / "web"

# Everything the deployed site needs. Listed rather than globbed so a stray
# .bak, a scratch file or a new snapshot cannot be published by accident.
FILES = [
    "index.html", "app.css", "styles.css",
    "data.js", "core.js", "sales.js", "sales-simple.js", "planner.js",
    "visits.js", "analytics.js", "desk.js", "shell.js", "admin.js",
    "dash.js", "ops.js", "drafts.js", "live.js",
]

# Deliberately absent. See the module docstring.
WITHHELD = ["data-tally.js"]


def _syntax_errors():
    """Files that do not parse, via `node --check`.

    A single syntax error in any of these scripts stops the whole bundle
    executing, so the app silently falls back to the offline demo with the
    wrong customer list and no indication anything is wrong. That shipped
    once. If node is not installed the check is skipped rather than
    blocking a release -- it is a guard, not a dependency.
    """
    if not shutil.which("node"):
        return []
    bad = []
    for f in FILES:
        if not f.endswith(".js"):
            continue
        r = subprocess.run(["node", "--check", str(SRC / f)],
                           capture_output=True, text=True)
        if r.returncode:
            first = (r.stderr.strip().splitlines() or [""])
            bad.append(f"{f}: {[l for l in first if 'Error' in l] or first[-1:]}")
    return bad


def sync(write=False, log=print):
    if not SRC.is_dir():
        raise SystemExit(f"prototype folder not found: {SRC}")
    DST.mkdir(exist_ok=True)
    missing = [f for f in FILES if not (SRC / f).exists()]
    if missing:
        raise SystemExit(f"missing from the prototype: {', '.join(missing)}")

    broken = _syntax_errors()
    if broken:
        raise SystemExit(
            "refusing to publish -- these do not parse, and a JavaScript "
            "syntax error takes the WHOLE app down to the offline demo with "
            "nothing on screen to say why:\n  " + "\n  ".join(broken))

    changed = [f for f in FILES
               if not (DST / f).exists()
               or not filecmp.cmp(SRC / f, DST / f, shallow=False)]
    stale = [p.name for p in DST.iterdir()
             if p.is_file() and p.name not in FILES]

    if not write:
        log(f"{len(changed)} file(s) would be updated"
            + (f": {', '.join(changed)}" if changed else ""))
        if stale:
            log(f"{len(stale)} stale file(s) would be removed: "
                f"{', '.join(stale)}")
        log(f"withheld from the deployed copy: {', '.join(WITHHELD)}")
        log("re-run with --write to apply")
        return {"changed": changed, "stale": stale, "written": False}

    for f in FILES:
        shutil.copy2(SRC / f, DST / f)
    for name in stale:
        (DST / name).unlink()

    # Drop the snapshot's <script> tag and re-stamp every asset.
    html = (DST / "index.html").read_text(encoding="utf-8")
    for name in WITHHELD:
        html = re.sub(rf'\s*<script[^>]*src="{re.escape(name)}[^"]*"[^>]*>'
                      rf'</script>', "", html)
    v = str(int(time.time()))
    html = re.sub(r"\?v=\d+", "?v=" + v, html)
    (DST / "index.html").write_text(html, encoding="utf-8")

    for name in WITHHELD:
        assert name not in html, f"{name} is still referenced by index.html"
        assert not (DST / name).exists(), f"{name} is still in {DST}"

    kb = sum(p.stat().st_size for p in DST.iterdir() if p.is_file()) / 1024
    log(f"web/ rebuilt: {len(FILES)} files, {kb:,.0f} KB, asset version {v}")
    log(f"withheld: {', '.join(WITHHELD)}")
    if stale:
        log(f"removed stale: {', '.join(stale)}")
    return {"changed": changed, "stale": stale, "written": True, "v": v}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    sync(write=ap.parse_args().write)
