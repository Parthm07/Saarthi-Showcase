# Saarthi deployment and updates — 0.6.0

## What `tally_raw.db` is

`tally_raw.db` is Saarthi's SQLite archive, created by reading masters, vouchers,
inventory and balances through Tally's local HTTP interface. It also holds the
local order queue, original submitted orders, request IDs and derived sales facts.
It is **not** Tally's own company data folder or a `.900` file. Do not rename a
Tally company file to `tally_raw.db` or copy an unrelated test company's archive.

`saarthi_admin.db`, beside it, holds admin requests, their outcomes, product images
and analysis targets. Keep both archives and their verified backups together.
Neither database is embedded in a software release.

## First installation on the business laptop (no Python needed)

1. Copy `SaarthiAdminSetup.exe` from the managed release output to the laptop.
   This complete installer includes Admin, the agent and their runtime.
2. Stop any old Python Tally agent and disable its old scheduled startup once.
   The installer refuses a conflicting worker; it never kills a process during
   an import. Keep Tally open with the correct business company loaded.
3. Check that Tally serves local XML requests, usually at `http://localhost:9000`.
   Tally's [integration prerequisites](https://help.tallysolutions.com/pre-requisites-for-integrations/)
   describe its HTTP port and loaded-company requirements. Configure Server or
   Both in its connectivity settings. Keep this connection local to the laptop.
4. Run Setup. If this **same company already has a Saarthi archive**, choose
   **Use an existing Saarthi tally_raw.db**, selecting its active original file.
   Keep its paired admin database in the same directory. This preserves pending
   work and duplicate-prevention IDs; creating a fresh archive would lose those.
5. For a genuinely new Saarthi installation, choose **Create tally_raw.db by
   reading Tally**, select an empty durable data folder, click **Read company from
   Tally**, verify the displayed company and click **Install / import**. Do this
   outside billing hours: the first full import may take time and keep Tally busy.
   It reads available history from 1900 onward; it cannot reconstruct past edits
   Tally no longer exposes. A partial import can resume in the same setup folder
   with the same company. It does not overwrite an existing completed archive.
6. The optional cloud PostgreSQL URL stays in `.env` in that data folder. It is
   needed for website/cloud syncing, not for local reporting. Configure the
   intended business cloud, never a test destination. It is not a Google login.
7. Open **Saarthi Admin** from Start. A new managed installation defaults to local
   observation mode: masters/vouchers/stock/balances are read; cloud jobs and
   automated invoice/admin writes are disabled. Check company, invoice totals,
   stock and balances against Tally. Existing explicit live-mode settings survive
   an upgrade. Check **Sync & backups** for the agent's actual status.
8. When ready, review pending/test orders and cloud destination, then use
   **Updates & setup → Enable automatic invoicing, admin Tally writes and
   configured cloud sync → Apply**. Website orders and Tally master/voucher
   changes are checked about every 10 seconds. Detected changes bring stock
   and cloud publishing forward; the open website checks every 15 seconds.
   This targets under a minute for routine changes while Tally, the agent and
   the cloud are reachable. Large imports, backlogs and Tally refusals can
   take longer. Deleted vouchers still need the scheduled or manual deletion
   check because Tally's change counter doesn't report deletions.

Opening Admin normally starts or attaches to the packaged agent. The worker
continues when Admin closes. This installer does not create a Windows boot task.
After a Windows restart, open Saarthi to start it. An update pauses it between
jobs, allowing the current import to finish. The bound Tally company is verified
before managed jobs; switching companies cannot silently redirect billing.

Default data location for a new import is `%LOCALAPPDATA%\SaarthiData` (or your
chosen folder). Settings and update backups live under `%LOCALAPPDATA%\SaarthiAdmin`.
Software lives under `%LOCALAPPDATA%\Programs\Saarthi Admin`, with versioned
subdirectories and a stable launcher. Data stays outside the application files.
Agent logs and `.env` stay in the data folder. Back up both databases to another
drive using **Create verified backup**; do not copy only an open SQLite main file
and omit its WAL. Native Tally backups remain a separate responsibility.

## Updating a feature from the development laptop

Make and test the code changes here, then build a **new version number**:

```powershell
Set-Location 'D:\Tally Project 2\Tally Project'
.\build_desktop.ps1 -Python 'C:\Users\PARTH MUNDRA\AppData\Local\Python\bin\python.exe' -Version 0.6.1
```

The build machine needs the dependencies in `requirements-desktop-build.txt`;
the business laptop does not. Default output is `D:\Tally Project 2\outputs\saarthi-managed`.
Use its complete `SaarthiAdminSetup.exe` for a first installation. For later
updates, transfer `Saarthi-0.6.1.zip` to the business laptop and choose
**Updates & setup → Choose signed release ZIP**. Save unfinished forms first.
Do not just replace a running EXE, copy the development database over production,
or reuse an already released version number for different code.

The updater verifies the publisher signature and all executable hashes, waits
for the current worker job, closes the local viewer, verifies backups of both
databases and settings, runs additive migrations and application self-checks,
then switches the version pointer. A failed validation restores those backups
and the previous pointer. Data, credentials and unresolved work stay on the
business laptop. Successful updates restart a previously running managed agent;
the viewer may need reopening after the first complete installation.

An interrupted migration leaves the agent paused. Use **Saarthi Recovery** in
Start (or **Recover interrupted update** in Admin) to recover the pre-update
state. If interruption happened before migrations began, recovery only clears
the pause without replacing business data. Recovery deliberately refuses to
restore an old snapshot after a completed update and later business entries.
Backups remain in the profile's `backups\before-update-*` folders.

The new source archive migration is local and additive. This workflow does not
deploy Railway, change the cloud schema or migrate Tally's native files. When a
feature also changes the website/API, deploy the compatible backend and required
cloud migrations separately, then update the laptop. A desktop build alone does
not update the phone website. This checkout's prior website-access migration is
documented in `DESKTOP_ADMIN.md`.

## Online desktop updates

On the business laptop, open **Updates & setup**, save this URL in **Publisher
HTTPS latest.json URL**, then click **Check for updates**:

```text
https://github.com/Parthm07/Saarthi-Desktop-Updates/releases/latest/download/latest.json
```

Saarthi checks the feed at startup and every 15 minutes while Admin is open.
When a newer version appears, save any unfinished forms and click
**Update available** in the sidebar. Saarthi downloads and verifies the update
and reopens after installing it. The business laptop does not need a GitHub login
to use the public feed.

On the development laptop, sign into GitHub CLI (`gh auth login`) with a
publisher account. Build and publish each new version with one command:

```powershell
.\build_desktop.ps1 -Python 'C:\Users\PARTH MUNDRA\AppData\Local\Python\bin\python.exe' -Version 0.6.8 -Publish
```

Use a version higher than the last release. To publish a ZIP already built,
pass it to `python publish_desktop.py 'path\to\Saarthi-0.6.7.zip'`. The publisher
verifies the ZIP, signs `latest.json`, and attaches both files to a GitHub
Release before it becomes public. The feed points to that specific release's
ZIP. If upload fails, keep the local ZIP and check the release in GitHub before
retrying.

The public downloads repository is for release files only. Keep source,
company databases, `.env`, and the private signing key out of it.

## Publisher keys and Windows trust

The build generated a private Ed25519 publisher key at
`.release-signing\release-key.pem` on the development machine. It is ignored by
Git: back it up securely and keep using the same key for future releases. The
public pin is `release_public_key.py`. Losing the private key requires a new
trusted installation/key-transition plan; simply making another key will not
allow existing installations to accept those packages.

Release signatures authenticate Saarthi update ZIPs. They are **not Windows
Authenticode signatures**. These EXEs are currently unsigned by a Windows-trusted
certificate, so Smart App Control can still block them. Do not disable Windows
protection as an update step. A trusted Windows signing/distribution route is
still required for a smooth installation on a computer enforcing that policy.

## Verification performed for this release

Automated checks use disposable archives and mocked Tally imports. They cover
tampered packages, unsafe ZIP entries, request/image/settings preservation,
downgrade refusal, migration rollback, interrupted recovery and read-only job
gating. Compiled executable checks use migration-only and self-check modes:
they do not create real Tally vouchers. Initial business-company import and a
controlled live entry must still be verified on the actual business laptop.
