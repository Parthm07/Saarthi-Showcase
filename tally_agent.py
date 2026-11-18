"""Unattended sync agent: runs the store operations on a fixed cadence.

Designed to run with nobody watching and nothing interpreting the results.
Every decision is a clock comparison or an equality check -- there is no
heuristic, no judgement, and no step that needs a human to read a message
and decide something.

    python tally_agent.py            # run forever
    python tally_agent.py --once     # one pass, then exit
    python tally_agent.py --status    # print state and exit
    python tally_agent.py --print-task  # the schtasks line to run at logon

Four properties it relies on:

  * Tally calls run serially under an OS-level lock. Catalogue uploads and
    derived reports use a separate worker that never calls Tally.
  * Heavy jobs are night-only. A full sweep takes ~47s during which Tally is
    unusable, so reconcile and archive wait for the quiet window unless they
    fall badly behind.
  * Last-run times live in the database, not in memory, so a restart or a
    reboot resumes the cadence instead of re-running everything.
  * A job that fails leaves its last-run time alone when the cause is
    transient (Tally closed or busy), so the next tick simply retries.

Stdlib only.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import datetime as dt
import logging
import logging.handlers
import msvcrt
import subprocess
import os
from pathlib import Path
import sqlite3
import sys
import time
import traceback
import tempfile

import tally_connector as t
import tally_shop as shop
import tally_store as s

from remote_sync import CLOUD_JOBS, RemoteSync
from saarthi_runtime import agent_stop_requested, data_directory, deletion_check_path, managed_job_allowed, runtime_settings
HERE = str(data_directory())
LOCK = os.path.join(HERE, "tally_agent.lock")
LOG = os.path.join(HERE, "tally_agent.log")

TICK = 3

# This laptop is on roughly 11:00-17:30, sometimes again 20:00-21:00. There
# is NO overnight idle window, so a 01:00-05:00 maintenance slot would never
# fire and every heavy job would reach the overdue escape hatch and run in
# the middle of the working day -- the exact outcome the window exists to
# prevent.
#
# So: three bands instead of one.
CORE  = (11, 17)    # staff billing in Tally. Heavy work NEVER runs here.
MAINT = (20, 11)    # 20:00 -> 11:00, WRAPPING midnight. Preferred slot.
LATE  = (17, 11)    # adds the 17:00-20:00 shoulder, once overdue.
STALE_DAYS = 7      # past this, integrity beats convenience: run anyway.
# MAINT and LATE wrap deliberately. Written as (20, 24) and (17, 24) they
# left 00:00-11:00 in no band at all, so a laptop awake at 00:30 -- which
# happens -- could not run a heavy job even though nobody was billing. The
# rule is "never during CORE", not "only in the evening".

# name -> (interval seconds, heavy)
# Voucher and master probes are cheap when nothing changes. Detected changes
# also bring dependent jobs forward; longer intervals remain as fallbacks.
#
# `stock` exists because a SALE DOES NOT BUMP THE STOCKITEM AlterID --
# measured: of 210 items sold in the last 7 days, only 21% sit in the top
# decile of master AlterID, against ~100% if sales touched the master. So
# the AlterID-driven masters sync would serve a stale ClosingBalance forever
# and the website would oversell. Stock is therefore polled unconditionally,
# not incrementally.
# After Tally refuses every voucher, wait this long before trying again.
# It needs a human to restart TallyPrime; retrying every ten minutes
# until then just buries the one message that matters.
INVOICE_RETRY_MINS = 30

JOBS = {
    "orders_pull": (10, False),
    "invoice":   (10,    False),
    "vouchers":  (10,    False),
    "orders_publish": (30, False),
    "admin":     (15,    False),
    "admin_backup": (3600,False),
    "masters":   (10,    False),
    "stock":     (45,    False),
    "rates":     (900,   False),
    "parties_visits": (60, False),
    "push":      (60,    False),
    "push_slow": (3600,  False),
    "retention": (3600,  False),
    "archive":   (86400, True),
    "reconcile": (86400, True),
}
CATALOGUE_JOBS = frozenset(('push', 'push_slow'))
BACKGROUND_JOBS = CATALOGUE_JOBS | {'rates', 'retention', 'admin_backup'}

log = logging.getLogger("tally_agent")


def setup_logging(verbose=False):
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s")
    fh = logging.handlers.RotatingFileHandler(
        LOG, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    log.addHandler(fh)
    if verbose:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        log.addHandler(sh)


class AlreadyRunning(Exception):
    """Another agent holds the lock."""


class Lock:
    """Exclusive single-instance lock.

    Uses an OS byte-range lock rather than a pid file: Windows releases it
    when the process dies, so a crash or a kill cannot leave a stale lock
    that blocks every later run.
    """

    def __init__(self, path=LOCK):
        self.path = path
        self.fh = None

    def __enter__(self):
        # msvcrt.locking locks a range at the CURRENT file position, so every
        # instance must seek(0) first or they lock different bytes and both
        # "succeed" -- the second one then dies on a PermissionError from its
        # first write instead of being refused cleanly. Nothing is written to
        # the file: the lock is the mechanism and the pid goes to the log.
        self.fh = open(self.path, "a+")
        self.fh.seek(0)
        try:
            msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            self.fh.close()
            self.fh = None
            raise AlreadyRunning(f"another agent holds {self.path}")
        return self

    def __exit__(self, *exc):
        if self.fh:
            try:
                self.fh.seek(0)
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass            # dying without unlocking is fine: Windows
            finally:            # releases the lock when the handle closes
                self.fh.close()
                self.fh = None


def in_band(now, band):
    """Is `now` inside `band`? Supports bands that wrap past midnight.

    (11, 17) is 11:00-17:00. (20, 11) is 20:00 through 11:00 the next day.
    """
    lo, hi = band
    h = now.hour
    return lo <= h < hi if lo < hi else (h >= lo or h < hi)


def due(job, last, now):
    """Should `job` run? Pure function of the clock and the last run.

    Light jobs run whenever due. Heavy jobs are gated by band:

      * inside MAINT (the evening session)        -> run
      * overdue (>= 2x interval) and inside LATE  -> run
      * stale beyond STALE_DAYS                   -> run regardless
      * otherwise                                 -> wait

    Being overdue widens the window; it never removes it. An unbounded
    escape hatch would put an 11-minute archive in front of someone
    mid-invoice, which is what the band exists to prevent.
    """
    interval, heavy = JOBS[job]
    if last is None:
        return True
    age = (now - last).total_seconds()
    if age < interval:
        return False
    if not heavy:
        return True
    if age >= STALE_DAYS * 86400:
        return True                                  # integrity wins
    if in_band(now, CORE):
        return False                                 # never during billing
    return in_band(now, MAINT) or (age >= 2 * interval
                                   and in_band(now, LATE))


def _last(conn, job):
    v = s._get(conn, f"last_run_{job}")
    if not v:
        return None
    try:
        return dt.datetime.fromisoformat(v)
    except ValueError:
        return None


def _mark(conn, job, when=None):
    try:
        s._set(conn, f"last_run_{job}", (when or dt.datetime.now()).isoformat(timespec="seconds"))
        conn.commit()
    except sqlite3.OperationalError as exc:
        if getattr(exc, 'sqlite_errorcode', None) not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            raise
        conn.rollback()
        log.warning('%s schedule will retry after the local archive is available', job)


def _run_soon(conn, *jobs):
    now = dt.datetime.now()
    for job in jobs:
        _mark(conn, job, now - dt.timedelta(seconds=JOBS[job][0]))


STALL_ALERT = 20          # consecutive skips before the log gets loud


def _stall(conn, job, kind, info):
    """Count consecutive unreachable attempts; escalate once it looks stuck.

    Deterministic: a counter and a threshold, no interpretation. Reset by
    the first success.
    """
    n = int(s._get(conn, f"stall_{job}", 0)) + 1
    s._set(conn, f"stall_{job}", n)
    conn.commit()
    if n == STALL_ALERT or (n > STALL_ALERT and n % STALL_ALERT == 0):
        log.warning(
            "%s has been unreachable (%s) for %d consecutive attempts. "
            "Someone should look at the Tally window: is a company loaded, "
            "and is it sitting on a dialog?", job, kind, n)


def _clear_stall(conn, job):
    if s._get(conn, f"stall_{job}"):
        s._set(conn, f"stall_{job}", 0)
        conn.commit()


def run_job(conn, job):
    """Run one job. Returns True if it completed and should be marked done.

    Transient failures return False so the last-run time is left alone and
    the next tick retries. An unexpected exception IS marked done, so a
    persistent bug retries on its normal interval instead of every tick.
    """
    try:
        no_tally_check = BACKGROUND_JOBS | {'orders_pull', 'orders_publish', 'vouchers', 'invoice', 'admin'}
        if job=='check_deletions' or (os.environ.get('SAARTHI_MANAGED')=='1'
                and job not in no_tally_check):
            s.verify_company(conn,log=log.info)
        if job == 'admin':
            import tally_admin
            n=tally_admin.run_pending(conn,log=log.info)
            if n:
                for following in ('vouchers','masters','stock','rates','push'):
                    _mark(conn,following,dt.datetime.now()-dt.timedelta(seconds=JOBS[following][0]))
        elif job == 'admin_backup':
            from pathlib import Path
            from desktop_archive import Archive
            from desktop_workflows import Workflows
            path=conn.execute('PRAGMA database_list').fetchone()[2]
            if path:
                work=Workflows(Archive(path))
                if work.path.exists():
                    backup=work.backup(Path(path).parent/'cloud_backup'/'local_archive')
                    log.info('admin backup verified: %s',backup)
        elif job == "vouchers":
            n = s.sync(conn, log=log.info)
            if n:
                _run_soon(conn, 'orders_publish', 'stock', 'rates', 'push')
            log.info("vouchers: %d stored", n)
        elif job == "stock":
            n = s.snapshot_stock(conn, log=log.info)
            b = s.snapshot_balances(conn, log=log.info)
            s.prune_snapshots(conn)
            s.prune_balances(conn)
            if n or b:
                _run_soon(conn, 'push')
            log.info("stock: %d items, %d party balances", n, b)
        elif job in ('orders_pull', 'orders_publish', 'parties_visits', 'push', 'push_slow'):
            import tally_cloud as cloud
            if not cloud.env("CLOUD_DB_URL"):
                return "ok"
            company = s._get(conn, 'company')
            if not company:
                return "ok"
            urgent = job in ('orders_pull', 'orders_publish')
            with cloud.connect(connect_timeout=5 if urgent else 30) as cx:
                with cx.cursor() as cur:
                    if urgent:
                        cur.execute("SET LOCAL statement_timeout='8s'")
                        cur.execute("SET LOCAL lock_timeout='4s'")
                    cur.execute("SELECT company FROM sync_status WHERE id=1")
                    remote = cur.fetchone()
                    if remote and remote[0] and remote[0] != company:
                        raise cloud.RefusedPush('Cloud and local company identities differ')
                    if job == 'orders_pull':
                        count = cloud.pull_orders(conn, cur, log=log.info, include_visits=False, batch_size=100)
                        if count:
                            _mark(conn, 'invoice', dt.datetime.now()-dt.timedelta(seconds=JOBS['invoice'][0]))
                    elif job == 'orders_publish':
                        import tally_updates
                        tally_updates.publish(conn, cur, log=log.info)
                        cloud.push_confirmations(conn, cur, log=log.info)
                    elif job == 'parties_visits':
                        from sales_visits import pull as pull_visits
                        cloud.create_approved_parties(conn, cur, log=log.info)
                        pull_visits(conn, cur)
                    elif job == 'push':
                        cloud.push_fast(conn, cur, log=log.info)
                    else:
                        cloud.push_slow(conn, cur, log=log.info, include_history=False)
        elif job == "invoice":
            import tally_invoice as inv
            w = inv.waiting(conn)
            if not w["waiting"]:
                if w["quarantined"]:
                    log.warning("invoice: nothing billable, but %d order(s) "
                                "are set aside and need a person",
                                w["quarantined"])
                return "ok"
            # A Tally that refused everything last time will refuse
            # everything now. Back off instead of retrying a stuck Tally
            # on every order cycle. This is separate from the normal cadence.
            blocked = s._get(conn, "invoice_blocked_since")
            if blocked:
                since = dt.datetime.fromisoformat(blocked)
                mins = (dt.datetime.now() - since).total_seconds() / 60
                if mins < INVOICE_RETRY_MINS:
                    log.warning("invoice: Tally refused every voucher %.0f "
                                "min ago and needs restarting -- %d order(s) "
                                "waiting, oldest %s min. Not retrying for "
                                "another %.0f min.", mins, w["waiting"],
                                w["oldest_mins"], INVOICE_RETRY_MINS - mins)
                    return "ok"
            # A failed sync must not let the next job post these orders
            # into some other company that the operator has loaded.
            s.verify_company(conn, log=log.info)
            res = inv.post_all(conn, commit=True, log=log.info, batch_size=3, notify_cloud=False)
            if res.get('done'):
                for following in ('vouchers', 'orders_publish'):
                    _mark(conn, following, dt.datetime.now()-dt.timedelta(seconds=JOBS[following][0]))
            if res.get("tally_stuck"):
                # Record it ONCE, so the backoff above measures from the
                # first refusal rather than resetting on every attempt.
                if not blocked:
                    s._set(conn, "invoice_blocked_since",
                           dt.datetime.now().isoformat(timespec="seconds"))
                s._set(conn, "invoice_error", str(res.get("error"))[:500])
                log.error("invoice: TALLY IS REFUSING ALL VOUCHERS. Restart "
                          "TallyPrime and reopen the company. %d order(s) "
                          "waiting, oldest %s min -- none are lost.",
                          w["waiting"], w["oldest_mins"])
            else:
                if blocked:
                    log.info("invoice: Tally is accepting vouchers again")
                s._set(conn, "invoice_blocked_since", "")
                s._set(conn, "invoice_error", "")
            conn.commit()
        elif job == "retention":
            import tally_cloud as cloud
            import tally_insights as ins
            import cloud_retention
            if not cloud.env('CLOUD_DB_URL'):
                return 'ok'
            watermark = s._get(conn,'voucher_alterid') or ''
            shop.rebuild_party_rates(conn,log=log.info)
            ins.rebuild(conn,log=log.info)
            if watermark != (s._get(conn,'voucher_alterid') or ''):
                log.info('retention deferred: invoices changed while facts were rebuilding')
                return 'ok'
            backup_dir=cloud.env('LOCAL_BACKUP_DIR') or os.path.join(HERE,'cloud_backup','local_archive')
            with cloud.connect() as cx, cx.cursor() as cur:
                cloud.push_party_rates(conn,cur,log=log.info)
                cloud.push_recommendations(conn,cur,log=log.info)
                cloud.push_daily_sales(conn,cur,log=log.info)
                cloud_retention.cleanup(conn,cur,backup_dir,log=log.info)
        elif job == "rates":
            # Recency, due dates, ageing and stock cover change with time
            # and stock snapshots even when the voucher watermark is quiet.
            wm = s._get(conn, "voucher_alterid")
            previous = s._get(conn, "rates_built_at_alterid")
            n = shop.rebuild_party_rates(conn, log=log.info)
            import tally_insights as ins
            ins.rebuild(conn, log=log.info)
            s._set(conn, "rates_built_at_alterid", wm)
            conn.commit()
            if wm != previous:
                _run_soon(conn, 'push')
            log.info("rates: %d party/item pairs", n)
        elif job == "masters":
            n = s.sync_masters(conn, log=log.info)
            if n:
                _run_soon(conn, 'stock', 'push')
            log.info("masters: %d stored", n)
        elif job == "archive":
            r = s.archive(conn, log=log.info)
            log.info("archive: %d days, %d vouchers, %d mismatched",
                     r["days"], r["vouchers"], r["mismatched"])
        elif job in ("reconcile", "check_deletions"):
            r = s.reconcile(conn, log=log.info)
            log.info("reconcile: %d live, %d deleted, %d missing",
                     r["live"], len(r["deleted"]), len(r["missing"]))
            if r["deleted"] or r["restored"]:
                for following in ('orders_publish', 'rates', 'push', 'push_slow'):
                    _mark(conn,following,dt.datetime.now()-dt.timedelta(seconds=JOBS[following][0]))
            if job == 'check_deletions':
                message = ('Check stopped: Tally returned an incomplete voucher list.'
                           if r['aborted'] else f"Check complete. {len(r['deleted'])} voucher(s) newly marked deleted.")
                s._set(conn, 'deletion_check_result', message)
                conn.commit()
            else:
                rm = s.reconcile_masters(conn, log=log.info)
                gaps = {k: len(v["missing"]) for k, v in rm.items() if v["missing"]}
                if gaps:
                    log.warning("master gaps: %s", gaps)
        else:
            raise ValueError(f"unknown job {job}")
        if job not in CATALOGUE_JOBS:
            _clear_stall(conn, job)
        return "ok"
    except t.TallyOffline as e:
        _stall(conn, job, "offline", log.info)
        log.info("%s skipped -- Tally not running (%s)", job, e)
        return "skip"
    except t.TallyBusy as e:
        # Only a genuinely in-flight request clears by itself. No company
        # loaded, or a Tally window parked on a dialog, never will -- so a
        # persistent stall has to get louder instead of backing off forever.
        _stall(conn, job, "busy", log.info)
        log.info("%s skipped -- Tally not answering (%s)", job, e)
        return "skip"
    except s.CompanyMismatch as e:
        # Never mark done, never retry into it: writing company B into
        # company A's archive is unrecoverable without a rebuild.
        log.error("%s HALTED -- %s", job, e)
        return "skip"
    except Exception:
        # "fail" still advances the last-run clock -- a persistent bug should
        # retry on its interval, not every 15 seconds -- but it must never be
        # reported as a successful run.
        conn.rollback()
        log.error("%s FAILED\n%s", job, traceback.format_exc())
        return "fail"


def run_background(database, job, remote=None):
    if not managed_job_allowed(job) or (remote and (remote.scheduler_paused or remote.paused and job in CLOUD_JOBS)):
        return 'skip'
    mode = 'ro' if job in CATALOGUE_JOBS else 'rw'
    with closing(sqlite3.connect(Path(database).as_uri()+'?mode='+mode, uri=True)) as local:
        local.row_factory = sqlite3.Row
        return run_job(local, job)


def tick(conn, now=None, *, worker=None, tasks=None, remote=None):
    """One pass over every job. Returns the names of the jobs that ran."""
    ran = []
    if tasks:
        for job, future in list(tasks.items()):
            if future.done():
                del tasks[job]
                try:
                    if future.result() == 'ok':
                        ran.append(job)
                except Exception:
                    log.exception('%s FAILED', job)
    for job, (_, heavy) in JOBS.items():
        if agent_stop_requested():break
        if not managed_job_allowed(job) or (remote and (remote.scheduler_paused or remote.paused and job in CLOUD_JOBS)):continue
        current = now or dt.datetime.now()
        last = _last(conn, job)
        if heavy and last is None and not in_band(current, MAINT):
            # First ever run, in business hours. A heavy job here would hold
            # Tally for ~11 minutes and freeze the operator's screen on
            # install day. Record it as "just run" so the normal interval
            # schedules it for tonight instead; the light sync still starts
            # immediately, so the website has current data within a minute.
            # Backdate by one interval so it is already due by age: the only
            # thing holding it is the night gate, so it fires at the next
            # window (tonight) rather than a full interval later.
            _mark(conn, job, current - dt.timedelta(seconds=JOBS[job][0]))
            log.info("%s deferred to the %02d:00-%02d:00 window",
                     job, MAINT[0], MAINT[1])
            continue
        if due(job, last, current):
            if job in BACKGROUND_JOBS and worker is not None:
                if job not in tasks:
                    database = conn.execute('PRAGMA database_list').fetchone()[2]
                    tasks[job] = worker.submit(run_background, database, job, remote) if remote else worker.submit(run_background, database, job)
                    _mark(conn, job, current)
                continue
            # "fail" still advances the clock -- a persistent bug should
            # retry on its interval, not every tick -- but it is NOT
            # reported as having run.
            outcome = run_job(conn, job)
            if outcome in ("ok", "fail"):
                _mark(conn, job)
            if outcome == "ok":
                ran.append(job)
    request = deletion_check_path()
    if not (remote and remote.scheduler_paused) and managed_job_allowed('reconcile') and request.is_file():
        outcome = run_job(conn, 'check_deletions')
        if outcome != 'ok':
            s._set(conn, 'deletion_check_result', 'Check could not finish. Verify the open Tally company and see the agent log.')
            conn.commit()
        request.unlink(missing_ok=True)
    return ran


TASK = "TallySyncAgent"


def _pythonw():
    """The windowless interpreter, so no console appears at every logon.

    Falls back to the normal one rather than failing -- a visible console
    is annoying, not broken.
    """
    w = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return w if os.path.exists(w) else sys.executable


def _task_xml():
    """A Task Scheduler definition for an agent that must simply stay up.

    Registered from XML rather than a one-line schtasks command because the
    defaults are wrong for a long-running process:

      * ExecutionTimeLimit defaults to 3 days, after which Windows KILLS
        the task. PT0S removes the limit.
      * A crash is otherwise final; RestartOnFailure brings it back.
      * StartWhenAvailable catches a logon the machine slept through.
      * Battery defaults stop the task on unplugging, which on a laptop
        means sync quietly dies when it leaves the desk.
      * IgnoreNew matches the agent's own single-instance lock, so a second
        copy is never started.

    The one-minute delay lets the desktop and TallyPrime settle first; the
    agent survives Tally being absent anyway, it just logs and retries.
    """
    import getpass
    user = f"{os.environ.get('USERDOMAIN', '')}\\{getpass.getuser()}".strip("\\")
    script = os.path.join(HERE, "tally_agent.py")
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Keeps the local Tally archive and the cloud in step, and
    brings website orders down to this laptop. Invoicing is NOT automated.</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{user}</UserId>
      <Delay>PT1M</Delay>
    </LogonTrigger>
    <!-- The watchdog, and the part that actually keeps this up.
         RestartOnFailure below reads like it covers a crash; it does not.
         Killing the process was measured: the task went to Ready and
         stayed dead for 150s. This trigger fires every five minutes
         forever, and MultipleInstancesPolicy IgnoreNew means it does
         nothing while the agent is alive, so it is a no-op almost always
         and a restart the one time it matters. The agent's own file lock
         is the second guard against two copies running. -->
    <CalendarTrigger>
      <StartBoundary>2026-01-01T00:00:00</StartBoundary>
      <Enabled>true</Enabled>
      <Repetition>
        <Interval>PT5M</Interval>
        <Duration>P1D</Duration>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure><Interval>PT1M</Interval><Count>99</Count></RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{_pythonw()}</Command>
      <Arguments>"{script}" --quiet</Arguments>
      <WorkingDirectory>{HERE}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>"""


def _schtasks(*args):
    return subprocess.run(["schtasks", *args], capture_output=True, text=True)


def install_task(log=print):
    """Register the agent to start at logon. No administrator rights needed."""
    xml = _task_xml()
    # Windows answers a malformed definition with "(18,36)::ERROR:
    # incorrect comment syntax" and nothing else. Parsing it here names the
    # problem properly -- an XML comment containing "--" is what caused that
    # one, and it is not an obvious rule.
    import xml.etree.ElementTree as ET
    try:
        ET.fromstring(xml)
    except ET.ParseError as e:
        log(f"the task definition is not valid XML: {e}")
        return 1
    # schtasks insists on UTF-16 for /XML, and rejects a BOM-less file.
    fd, path = tempfile.mkstemp(suffix=".xml")
    os.close(fd)
    try:
        with open(path, "w", encoding="utf-16") as f:
            f.write(xml)
        r = _schtasks("/Create", "/TN", TASK, "/XML", path, "/F")
        if r.returncode:
            log(f"could not register the task:\n{r.stdout}{r.stderr}")
            return 1
    finally:
        os.unlink(path)
    log(f"registered '{TASK}' -- starts a minute after you log in,")
    log(f"  and a watchdog re-checks every 5 minutes that it is still up.")
    log(f"  interpreter: {_pythonw()}")
    log(f"  no console window, no 3-day kill, restarts itself on a crash,")
    log(f"  keeps running on battery.")
    log(f"\nIt is NOT running yet. Start it now with:")
    log(f"  schtasks /Run /TN {TASK}")
    log(f"Check it any time with:  python tally_agent.py --task-status")
    return 0


def remove_task(log=print):
    r = _schtasks("/Delete", "/TN", TASK, "/F")
    log(f"removed '{TASK}'" if not r.returncode
        else f"{r.stdout}{r.stderr}".strip())
    return r.returncode


def task_status(log=print):
    r = _schtasks("/Query", "/TN", TASK, "/V", "/FO", "LIST")
    if r.returncode:
        log(f"'{TASK}' is not registered. Install it with --install-task")
        return 1
    keep = ("TaskName", "Status", "Last Run Time", "Last Result",
            "Next Run Time", "Task To Run", "Start In", "Schedule Type")
    for ln in r.stdout.splitlines():
        if any(ln.strip().startswith(k) for k in keep):
            log("  " + " ".join(ln.split()))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--once", action="store_true", help="one pass, then exit")
    ap.add_argument("--status", action="store_true", help="print state, exit")
    ap.add_argument("--print-task", action="store_true",
                    help="print the Task Scheduler definition, without installing")
    ap.add_argument("--install-task", action="store_true",
                    help="start the agent automatically at every logon")
    ap.add_argument("--remove-task", action="store_true")
    ap.add_argument("--task-status", action="store_true",
                    help="is it registered, and when did it last run")
    ap.add_argument("--quiet", action="store_true", help="log to file only")
    a = ap.parse_args(argv)

    if a.print_task:
        print(_task_xml())
        return 0
    if a.install_task:
        return install_task()
    if a.remove_task:
        return remove_task()
    if a.task_status:
        return task_status()

    if not a.status and agent_stop_requested():
        return 0
    setup_logging(verbose=not a.quiet)
    # shop.open_db creates BOTH schemas: the store's tables and the
    # shop's. s.open_db alone leaves shop_order_line and
    # party_item_rate missing, which the rates and push jobs need.
    conn = shop.open_db()

    if a.status:
        now = dt.datetime.now()
        print(f"now {now:%Y-%m-%d %H:%M:%S}   core {CORE[0]}-{CORE[1]} "
              f"(in: {in_band(now, CORE)})   maint {MAINT[0]}-{MAINT[1]} "
              f"(in: {in_band(now, MAINT)})   late {LATE[0]}-{LATE[1]} "
              f"(in: {in_band(now, LATE)})")
        print(f"{'job':<11}{'interval':>9}{'heavy':>7}  {'last run':<20}due")
        for job, (iv, heavy) in JOBS.items():
            last = _last(conn, job)
            print(f"{job:<11}{iv:>9}{str(heavy):>7}  "
                  f"{last.isoformat(' ', 'seconds') if last else '-':<20}"
                  f"{due(job, last, now)}")
        st, ar = s.stats(conn), s.archive_stats(conn)
        print(f"\nsynced   : {st['vouchers']} vouchers, watermark "
              f"{st['watermark']}")
        print(f"archived : {ar['vouchers']} vouchers, "
              f"{ar['days_verified']}/{ar['days']} days verified")
        conn.close()
        return 0

    try:
        # Shared across source checkouts for this Windows account. Keep the
        # old per-folder lock too, so older agents in this folder still exclude us.
        shared=os.path.join(os.environ.get('LOCALAPPDATA',os.path.expanduser('~')),
                            'SaarthiAgent','connector.lock')
        os.makedirs(os.path.dirname(shared),exist_ok=True)
        with Lock(path=shared), Lock(), ThreadPoolExecutor(max_workers=1) as worker:
            log.info("agent started (pid %d)", os.getpid())
            if a.once:
                log.info("single pass ran: %s", tick(conn) or "nothing due")
                return 0
            tasks = {}
            remote = None
            if os.environ.get('SAARTHI_MANAGED') == '1':
                config = runtime_settings()
                if config.get('agent_sync_url') and config.get('agent_sync_token'):
                    remote = RemoteSync(config['agent_sync_url'], config['agent_sync_token'], data_directory())
            while True:
                if agent_stop_requested():
                    log.info('Agent stopping at a job boundary');return 0
                if remote:
                    remote.check(tasks, live_enabled=runtime_settings().get('billing_enabled') is True)
                    if agent_stop_requested():return 0
                tick(conn, worker=worker, tasks=tasks, remote=remote)
                time.sleep(TICK)
    except AlreadyRunning as e:
        log.warning("not starting: %s", e)
        return 1
    except KeyboardInterrupt:
        log.info("agent stopped")
        return 0
    finally:
        conn.close()


def _self_check():
    """Scheduling logic and the lock, without touching Tally."""
    import subprocess
    D = dt.datetime
    core  = D(2026, 10, 10, 14, 0)    # 2pm    -- staff billing
    maint = D(2026, 10, 10, 20, 30)   # 8:30pm -- evening session
    late  = D(2026, 10, 10, 18, 0)    # 6pm    -- after work, before evening
    dawn  = D(2026, 10, 10, 0, 33)    # 00:33  -- laptop awake late
    morn  = D(2026, 10, 10, 10, 0)    # 10am   -- before billing starts

    assert in_band(core, CORE) and not in_band(core, MAINT)
    assert in_band(maint, MAINT) and in_band(maint, LATE)
    assert in_band(late, LATE) and not in_band(late, MAINT)
    # The wrapping bands must cover past midnight and the pre-work morning,
    # which a non-wrapping (20, 24) silently excluded.
    assert in_band(dawn, MAINT) and in_band(dawn, LATE)
    assert in_band(morn, MAINT), "10am should be maintenance-eligible"
    assert not in_band(dawn, CORE) and not in_band(morn, CORE)
    # and the whole clock is covered: every hour is either CORE or MAINT
    for h in range(24):
        n = D(2026, 10, 10, h, 0)
        assert in_band(n, CORE) != in_band(n, MAINT) or in_band(n, LATE), \
            f"hour {h} falls in no band"
    assert not any(in_band(D(2026, 10, 10, h, 0), MAINT)
                   for h in range(11, 17)), "MAINT overlaps billing hours"

    # light jobs: interval only
    assert due("vouchers", None, core)
    assert not due("vouchers", core - dt.timedelta(seconds=30), core)
    assert due("vouchers", core - dt.timedelta(seconds=90), core)
    assert due("stock", core - dt.timedelta(seconds=400), core)
    assert not due("stock", core - dt.timedelta(seconds=100), core)

    # heavy jobs must NEVER run during billing hours, however overdue
    for hours in (25, 49, 100):
        assert not due("reconcile", core - dt.timedelta(hours=hours), core), \
            f"heavy job ran during CORE hours at {hours}h overdue"
    # but the 7-day staleness cap overrides even that
    assert due("reconcile", core - dt.timedelta(days=8), core), \
        "a week-stale heavy job never runs"

    # evening session is the preferred slot
    assert due("reconcile", maint - dt.timedelta(hours=25), maint)
    # 6pm: allowed only once overdue, not merely due
    assert not due("reconcile", late - dt.timedelta(hours=25), late)
    assert due("reconcile", late - dt.timedelta(hours=49), late)
    print("schedule logic ok (core exclusion, evening slot, overdue widening,"
          " 7-day cap)")

    # the lock must actually exclude a second process
    with Lock(LOCK + ".test"):
        r = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, r'" + HERE + "');"
             "import tally_agent as a;"
             "ok=False\n"
             "try:\n"
             "    with a.Lock(a.LOCK + '.test'): pass\n"
             "except a.AlreadyRunning: ok=True\n"
             "sys.exit(0 if ok else 1)"],
            capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, \
            f"second instance was NOT excluded: {r.stdout}{r.stderr}"
    # and released afterwards
    with Lock(LOCK + ".test"):
        pass
    os.remove(LOCK + ".test")
    print("single-instance lock ok (second process refused, lock released)")

    # a job that cannot reach Tally must not be marked done

    conn = shop.open_db(os.path.join(tempfile.mkdtemp(), "a.db"))
    setup_logging(verbose=False)
    saved, t.URL = t.URL, "http://127.0.0.1:65123"
    try:
        assert run_job(conn, "vouchers") == "skip", \
            "offline job reported success"
        assert _last(conn, "vouchers") is None, "offline job was marked done"
    finally:
        t.URL = saved
    print("offline handling ok (not marked done, retries next tick)")

    # A real pass against the live Tally -- light jobs only. The heavy ones
    # are marked just-run first: their gating is already covered by the
    # due() cases above, and letting tick() run a full archive here would
    # make this check take ~12 minutes depending on the hour it is run.
    now = dt.datetime.now()
    for job, (_, heavy) in JOBS.items():
        # push_slow sends ~2 MB to Postgres and takes ~90s; tally_cloud's own
        # self-check covers it. This check is about scheduling, not re-testing
        # the push.
        if heavy or job == "push_slow":
            _mark(conn, job, now)
    t0 = time.time()
    ran = tick(conn)
    secs = time.time() - t0
    if not ran:
        print("skipping live assertions -- Tally unreachable or busy")
        conn.close()
        return
    # `ran` holds only jobs that SUCCEEDED. A job that threw is marked done
    # so it retries on its interval, but it must not appear here -- that
    # distinction is what let a missing-table bug look like a clean run.
    for want in ("vouchers", "stock", "masters", "rates"):
        assert want in ran, f"{want} did not run successfully: {ran}"
    assert _last(conn, "vouchers"), "successful job was not marked done"
    # immediately after, nothing light is due again
    # Measured from when it was marked, not from now: the tick itself can take
    # longer than the 60s interval, which would make a correctly-scheduled job
    # legitimately due again and fail a naive assertion.
    marked = _last(conn, "vouchers")
    assert not due("vouchers", marked, marked + dt.timedelta(seconds=1)), \
        "a just-run job was immediately due again"
    assert due("vouchers", marked, marked + dt.timedelta(seconds=61)), \
        "a job never becomes due after its interval"
    st = s.stats(conn)
    assert st["vouchers"], "live tick stored no vouchers"
    print(f"live tick ok in {secs:.0f}s: ran {ran}, "
          f"{st['vouchers']} vouchers stored")

    # first-run heavy jobs must be deferred, not run, during business hours
    daytime = dt.datetime.now().replace(hour=14, minute=0, second=0)
    fresh = shop.open_db(os.path.join(tempfile.mkdtemp(), "b.db"))
    # Mark the light jobs as just-run so this exercises only the heavy path.
    for job, (_, heavy) in JOBS.items():
        if not heavy:
            _mark(fresh, job, daytime)
    assert tick(fresh, now=daytime) == [], \
        "a heavy job ran on first tick during business hours"
    for job in ("archive", "reconcile"):
        assert _last(fresh, job) is not None, f"{job} was not deferred"
        assert not due(job, _last(fresh, job), daytime), \
            f"{job} is still due right after deferral"
    # it fires that same evening, ~6h later, not a full interval on
    evening = daytime.replace(hour=20, minute=30)
    assert due("archive", _last(fresh, "archive"), evening), \
        "deferred archive did not become due in the evening session"
    # if the evening session never happens, it must NOT barge into billing
    assert not due("archive", _last(fresh, "archive"),
                   daytime + dt.timedelta(days=1)), \
        "deferred archive barged into CORE hours the next day"
    # but it does become due after work the next day, once overdue
    assert due("archive", _last(fresh, "archive"),
               (daytime + dt.timedelta(days=1)).replace(hour=18)), \
        "deferred archive never runs after work on a laptop with no evenings"
    fresh.close()
    print("first-run deferral ok: waits for the evening session, never CORE")
    conn.close()
    print("agent ok")


if __name__ == "__main__":
    if "--self-check" in sys.argv:
        _self_check()
    else:
        sys.exit(main())
