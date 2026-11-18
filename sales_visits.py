"""Small, idempotent visit facts; cloud keeps only acknowledged recent facts."""
import datetime as dt

LOCAL_SCHEMA='''CREATE TABLE IF NOT EXISTS sales_visit(
 salesperson TEXT NOT NULL,vdate TEXT NOT NULL,customer_guid TEXT NOT NULL,
 customer_name TEXT NOT NULL,route TEXT,visited_at TEXT NOT NULL,
 PRIMARY KEY(salesperson,vdate,customer_guid));'''
CLOUD_SCHEMA='''CREATE TABLE IF NOT EXISTS sales_visit(
 salesperson TEXT NOT NULL,vdate TEXT NOT NULL,customer_guid TEXT NOT NULL,
 customer_name TEXT NOT NULL,route TEXT,visited_at TIMESTAMPTZ NOT NULL,
 pulled_at TIMESTAMPTZ,PRIMARY KEY(salesperson,vdate,customer_guid));'''

def pull(local,cur):
    cur.execute("SELECT to_regclass('sales_visit')")
    if not cur.fetchone()[0]:return 0
    local.executescript(LOCAL_SCHEMA)
    cur.execute('SELECT salesperson,vdate,customer_guid,customer_name,route,visited_at FROM sales_visit WHERE pulled_at IS NULL')
    rows=cur.fetchall()
    for who,day,guid,name,route,at in rows:
        local.execute('INSERT OR IGNORE INTO sales_visit VALUES(?,?,?,?,?,?)',(who,day,guid,name,route,str(at)))
    local.execute("INSERT INTO sync_state(key,value) VALUES('visit_sync_at',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(dt.datetime.now(dt.timezone.utc).isoformat(),))
    local.commit() # Archive safely before acknowledging the cloud copy.
    for who,day,guid,*_ in rows:
        cur.execute('UPDATE sales_visit SET pulled_at=now() WHERE salesperson=%s AND vdate=%s AND customer_guid=%s',(who,day,guid))
    cur.execute("DELETE FROM sales_visit WHERE pulled_at IS NOT NULL AND visited_at < now()-interval '7 days'")
    return len(rows)
