"""Publish local product pictures once per content hash, shared by many items."""
import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path

CLOUD_SCHEMA = '''
CREATE TABLE IF NOT EXISTS product_media (
 digest TEXT PRIMARY KEY, mime TEXT NOT NULL, body BYTEA NOT NULL);
CREATE TABLE IF NOT EXISTS item_media (
 guid TEXT PRIMARY KEY REFERENCES item(guid) ON DELETE CASCADE,
 digest TEXT NOT NULL REFERENCES product_media(digest));
'''


def available(cur):
    cur.execute("SELECT to_regclass('public.item_media')")
    return bool(cur.fetchone()[0])


def push(local, cur, log=print):
    """Run inside the catalogue push transaction; never access an unrelated DB."""
    source = local.execute('PRAGMA database_list').fetchone()[2]
    path = Path(source).parent / 'saarthi_admin.db' if source else None
    if not path or not path.is_file():
        return 0
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='product_image'").fetchone():
            return 0
        pictures = conn.execute('SELECT guid,mime,body FROM product_image').fetchall()
    if not pictures:
        return 0
    if not available(cur):
        log('Product images pending: apply cloud_db.py --media-schema on the cloud database')
        return 0
    cur.execute('SELECT guid FROM item')
    valid = {r[0] for r in cur.fetchall()}
    cur.execute('SELECT guid,digest FROM item_media')
    existing = dict(cur.fetchall())
    cur.execute('SELECT digest FROM product_media')
    uploaded = {r[0] for r in cur.fetchall()}
    changed = 0
    for guid, mime, body in pictures:
        if guid not in valid:
            continue
        digest = hashlib.sha256(body).hexdigest()
        if existing.get(guid) == digest:
            continue
        if digest not in uploaded:
            cur.execute('INSERT INTO product_media VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',
                        (digest, mime, body))
            uploaded.add(digest)
        cur.execute('INSERT INTO item_media VALUES(%s,%s) ON CONFLICT(guid) DO UPDATE SET digest=excluded.digest',
                    (guid, digest))
        changed += 1
    cur.execute('DELETE FROM product_media WHERE NOT EXISTS (SELECT 1 FROM item_media WHERE item_media.digest=product_media.digest)')
    if changed:
        log(f'images: {changed} product picture(s) published')
    return changed
