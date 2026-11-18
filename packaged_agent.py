"""Bundled agent entry point: set persistent paths before importing worker modules."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sys


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('--data-dir',required=True)
    parser.add_argument('--migrate-only',action='store_true')
    parser.add_argument('--bootstrap',action='store_true')
    parser.add_argument('--probe',action='store_true')
    parser.add_argument('--company',default='')
    parser.add_argument('--tally-url')
    parser.add_argument('--quiet',action='store_true')
    args=parser.parse_args(argv)
    folder=Path(args.data_dir).resolve();folder.mkdir(parents=True,exist_ok=True)
    os.environ['SAARTHI_DATA_DIR']=str(folder);os.environ['SAARTHI_MANAGED']='1'
    from saarthi_runtime import runtime_settings,pause_path
    config=runtime_settings(folder)
    if args.tally_url or config.get('tally_url'):os.environ['TALLY_URL']=args.tally_url or config['tally_url']
    if args.probe:
        from desktop_agent import running_agents
        if running_agents():raise ValueError('Stop the existing agent before first-run company probing/import')
        import tally_connector
        result={'company':tally_connector.company()}
        print(json.dumps(result));return 0
    if args.bootstrap and pause_path().exists():raise ValueError('An update/recovery is pending')
    import tally_shop as shop
    if args.migrate_only:
        if not (folder/'tally_raw.db').is_file():raise ValueError('Existing local archive missing')
        import sqlite3
        with closing(sqlite3.connect((folder/'tally_raw.db').as_uri()+'?mode=ro',uri=True)) as check:
            if check.execute('PRAGMA user_version').fetchone()[0]>1:raise ValueError('Archive schema is newer than this release')
        conn=shop.open_db();conn.execute('PRAGMA user_version=1');conn.commit();conn.close()
        from desktop_workflows import Workflows
        from desktop_archive import Archive
        with Workflows(Archive(folder/'tally_raw.db')).db():pass
        return 0
    import tally_agent as agent
    if args.bootstrap:
        import tally_connector as tally
        import tally_store as store
        import tally_insights
        if not args.company or tally.company()!=args.company:raise ValueError('The selected company is no longer loaded in Tally')
        __import__('saarthi_runtime').shared_lock().parent.mkdir(parents=True,exist_ok=True)
        with agent.Lock(str(__import__('saarthi_runtime').shared_lock())),agent.Lock():
            conn=shop.open_db()
            try:
                store.verify_company(conn)
                if store._get(conn,'bootstrap_complete')=='1':raise ValueError('This archive is already imported; use normal syncing')
                store._set(conn,'cutoff','19000101');conn.commit()
                store.sync_masters(conn,full=True)
                store.sync(conn)
                store.snapshot_stock(conn);store.snapshot_balances(conn)
                shop.rebuild_party_rates(conn);tally_insights.rebuild(conn)
                store._set(conn,'bootstrap_complete','1');conn.commit()
                print(json.dumps({'imported':True,'company':args.company}))
            finally:conn.close()
        return 0
    if pause_path().exists():raise ValueError('Update is in progress. Finish or recover the update first.')
    return agent.main(['--quiet'] if args.quiet else [])

if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as error:
        # Do not echo connection strings/credentials from dependency exceptions.
        if sys.stderr:print(type(error).__name__+': operation failed; check the local log/settings.',file=sys.stderr)
        raise SystemExit(1)
